"""Planification de l'orchestrateur : routeur LLM + gardes déterministes + ré-export `complete`.

Le routeur décide du traitement d'une requête analyste, avec une **posture
d'orchestrateur actif** (il agit / route) et non de gatekeeper (qui refuse et
renvoie le travail à l'utilisateur). Trois traitements :
- `investigate` : analyser un SUJET du monde via des agents spécialisés.
- `data_ops`    : agir sur / INTERROGER notre patrimoine de données (lister,
  consulter, ingérer des collections/datasets) → agent de gestion de données.
  Normalisé en `investigate` à la sortie (la FSM ne connaît que investigate/chitchat).
- `chitchat`    : purement conversationnel ; sinon UNE question de clarification.

Au-dessus du LLM (modèle faible, faillible), des **gardes déterministes** ne
dépendent pas de lui :
- mention explicite d'un agent (« demande à Search ») → on force le routage ;
- interrogation de notre stock (« les data qu'on a sur X ») → agent data ;
- ancrage de l'instruction sur le dernier sujet de l'historique quand le message
  courant n'est qu'un ordre laconique (« demande à Search »).

`plan_investigation` (heuristique mots-clés) reste le repli si le LLM est HS /
renvoie du JSON malformé. **Aucun fallback "dispatch à tous"** : si rien ne
match, liste vide et la FSM bascule en chitchat.
"""

import hashlib
import json
import logging
import re
import unicodedata
from typing import Awaitable, Callable

from hemicycle_shared.llm_client import (
    complete_stream as _complete_stream_raw,
    complete_usage,
)  # complete_usage ré-exporté directement ; complete_stream enveloppé ci-dessous

log = logging.getLogger("orchestrator.llm")

# Budget par défaut quand le client ne transmet pas max_tokens (anciens clients,
# replay d'events). Aligné sur la valeur par défaut de SettingsView côté front.
DEFAULT_LLM_MAX_TOKENS = 100_000
# Cap système : Hémicycle ne dépasse JAMAIS ce contexte d'input total, peu importe
# ce que le client envoie. Aligné avec CONTEXT_TOKENS_MAX côté front (store.ts).
MAX_LLM_CONTEXT_TOKENS = 256_000

# S6 — purposes où le raisonnement (<think>) est désactivé par défaut quand le
# caller n'a pas posé d'override explicite.
#   "report-writing" couvre la rédaction de rapport COMPLET, la synthèse chat ET la
#   compaction (les trois partagent ce purpose côté stub). La phase <think> n'est pas
#   le livrable sur ces tâches ; elle ajoute 30-60 s de latence et partage le même cap
#   de tokens que le rapport/résumé — risque de tronquer le contenu produit (cf. S2).
#   "subagent-reasoning" couvre le géocodage et les lookups structurés rapides.
#   "planning" est ABSENT : la FSM passe toujours un bool explicite (thinking_on)
#   issu du bouton UI, jamais None — le défaut par purpose ne s'appliquerait pas.
_THINK_OFF_PURPOSES: frozenset[str] = frozenset({"report-writing", "subagent-reasoning"})


async def complete_stream(
    purpose: str,
    priority: str,
    prompt: str = "",
    lang: str = "en",
    enable_thinking: bool | None = None,
):
    """Wrapper de `hemicycle_shared.llm_client.complete_stream` avec détection d'erreur de flux.

    Lève `RuntimeError` si :
    - le stub émet un événement `error` explicite (exception upstream vLLM/LiteLLM) ;
    - le chunk `done` porte `finish_reason="error"` ou `error=True` (terminaison non-propre) ;
    - le générateur s'épuise sans émettre un `done` propre (coupure réseau avant fin).

    Sans cette garde, un hoquet vLLM à 80 % d'un rapport produisait un texte tronqué
    publié comme canonique : le stub émettait `token:"" + done` sans flag d'erreur,
    et la FSM acceptait le texte partiel comme complet (condition ne testant que le
    texte vide). Le repli déterministe `build_report_fallback` est maintenant déclenché
    dans TOUS les cas de terminaison non-propre, pas seulement sur flux vide.

    S6 : si `enable_thinking` n'est pas fourni (None) et que le purpose fait partie de
    `_THINK_OFF_PURPOSES`, le raisonnement est désactivé explicitement. Cela couvre la
    rédaction de rapport, la synthèse chat et le géocodage sans que la FSM ait besoin
    de passer le flag (elle ne le passe pas pour ces purposes aujourd'hui).
    """
    # Défaut par purpose (S6) : désactive le think pour les tâches où il est inutile.
    if enable_thinking is None and purpose in _THINK_OFF_PURPOSES:
        enable_thinking = False
    _stream_clean = False
    async for chunk in _complete_stream_raw(purpose, priority, prompt, lang, enable_thinking):
        ctype = chunk.get("type")
        if ctype == "error":
            # Erreur upstream explicite : on casse le flux vers l'appelant pour que
            # son `except Exception` déclenche le repli déterministe.
            raise RuntimeError(
                f"flux LLM upstream error : {(chunk.get('text') or 'inconnu')[:256]}"
            )
        if ctype == "done":
            if chunk.get("error") or chunk.get("finish_reason") == "error":
                raise RuntimeError(
                    "flux LLM terminé sur erreur (finish_reason=error ou error=True)"
                )
            _stream_clean = True
        yield chunk
    # Générateur épuisé sans `done` propre → coupure réseau avant la fin.
    if not _stream_clean:
        raise RuntimeError("flux LLM interrompu : aucun chunk done propre reçu")


def _resolve_budget_tokens(max_tokens: int | None) -> int:
    """Normalise le budget de tokens : défaut si absent, clamp au cap système."""
    raw = max_tokens if max_tokens and max_tokens > 0 else DEFAULT_LLM_MAX_TOKENS
    return min(raw, MAX_LLM_CONTEXT_TOKENS)


_WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)


def _estimate_tokens(text: str) -> int:
    """Estimation rapide du nombre de tokens sans dépendance externe.

    Heuristique standard : ~4 caractères par token (calibré sur GPT-3/4).
    Retourne au moins 1 pour éviter les divisions par zéro en amont.
    """
    return max(1, len(text) // 4)


def _build_attachments_section(
    attachments: list[dict],
    budget_chars: int | None,
    lang: str = "en",
) -> str:
    """Formate les pièces jointes en bloc insérable dans un prompt LLM.

    Chaque PJ est présentée avec son nom, sa taille et son contenu texte.
    Si `budget_chars` est fourni, le contenu est tronqué proportionnellement
    pour tenir dans le budget, avec un minimum de 1 500 caractères par PJ
    pour permettre la détection de langue. Les PJ excédentaires sont annotées.
    """
    if not attachments:
        return ""
    fr = lang == "fr"
    header = (
        "PIÈCES JOINTES FOURNIES PAR L'ANALYSTE :"
        if fr
        else "ANALYST-PROVIDED ATTACHMENTS:"
    )

    # Calcul du budget disponible par PJ (tronquer proportionnellement).
    # Convention du projet : la clé du contenu textuel est `text` (cf. _load_attachments).
    contents = []
    for att in attachments:
        text_content: str = att.get("text") or att.get("content") or ""
        contents.append(text_content)

    if budget_chars is not None and budget_chars > 0:
        total_raw = sum(len(c) for c in contents)
        if total_raw > budget_chars:
            # On répartit proportionnellement ; plancher 1 500 chars/PJ.
            floor = 1500
            n = len(attachments)
            max_total = max(budget_chars, floor * n)
            per_pj = max(floor, max_total // n if n else max_total)
            # Vérifier si même avec le plancher on dépasse → garder seulement les premières PJ.
            if floor * n > budget_chars:
                # On tronque à N PJ entières qui tiennent dans le budget.
                keep = max(1, budget_chars // floor)
                truncated_contents = contents[:keep]
                omit_note = (
                    "(omise — budget tokens)" if fr else "(omitted — token budget)"
                )
                truncated_contents += [omit_note] * (n - keep)
                contents = truncated_contents
            else:
                contents = [c[:per_pj] for c in contents]

    lines = [f"\n{header}"]
    for i, (att, content) in enumerate(zip(attachments, contents), 1):
        filename = att.get("filename") or att.get("id") or f"pj-{i}"
        raw_text = att.get("text") or att.get("content") or ""
        size_info = f"{len(raw_text)} caractères" if raw_text else "n/a"
        if fr:
            lines.append(f"[{i}] {filename} ({size_info}) :")
        else:
            lines.append(f"[{i}] {filename} ({size_info}):")
        if content and content not in (
            "(omise — budget tokens)",
            "(omitted — token budget)",
        ):
            lines.append(content)
        elif not raw_text:
            img_note = (
                "(image — pas de contenu textuel disponible)"
                if fr
                else "(image — no text content available)"
            )
            lines.append(img_note)
        else:
            lines.append(content)
    return "\n".join(lines) + "\n"


def _keywords(text: str) -> set[str]:
    """Tokens normalisés (lowercase) d'un texte — utilisés pour le match manifest."""
    return {t.lower() for t in _WORD_RE.findall(text) if len(t) > 2}


def _norm(text: str) -> str:
    """Minuscule + suppression des accents + ponctuation→espaces (match robuste, ASCII)."""
    s = unicodedata.normalize("NFD", (text or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _agent_matches(query_tokens: set[str], agent_meta: dict) -> bool:
    """True si la discipline ou les mots-clés du manifest croisent la requête."""
    haystack = " ".join(
        str(agent_meta.get(k, "")) for k in ("discipline", "when_to_use", "description")
    ).lower()
    if not haystack.strip():
        return False
    for tok in query_tokens:
        if tok in haystack:
            return True
    return False


# --------------------------------------------------------------------------
# Gardes déterministes (indépendantes du LLM faible)
# --------------------------------------------------------------------------

# Mots trop génériques pour signaler une MENTION explicite d'agent : on les exclut
# des tokens de mention (« data », « management » apparaissent dans des requêtes
# d'analyse banales — l'interrogation du stock est gérée par _is_catalog_query).
_MENTION_STOPWORDS = {
    "agent",
    "agents",
    "service",
    "srv",
    "hemicycle",
    "bot",
    "management",
    "mgmt",
    "data",
    "datamgt",
    "donnees",
}


def _agent_mention_tokens(agent_id: str, meta: dict) -> set[str]:
    """Tokens qui, présents dans la requête, dénotent une mention EXPLICITE de l'agent.

    Construits depuis la discipline et l'id (ex. `mi_search`/discipline SEARCH →
    {`search`, `mi_search`}), filtrés des mots génériques. La gestion de données n'a pas de
    token propre (trop ambigu) — elle passe par `_is_catalog_query`.
    """
    toks: set[str] = set()
    for src in (meta.get("discipline") or "", agent_id):
        for part in _norm(src).split():
            if len(part) > 2 and part not in _MENTION_STOPWORDS:
                toks.add(part)
    return toks


def _mentioned_agents(text: str, catalog: dict[str, dict]) -> list[str]:
    """Agents du catalogue explicitement nommés dans la requête (par discipline/id)."""
    words = set(_norm(text).split())
    if not words:
        return []
    return [
        aid for aid, meta in catalog.items() if _agent_mention_tokens(aid, meta) & words
    ]


# Tournures qui dénotent une INTERROGATION de notre propre patrimoine de données
# (≠ analyse d'un sujet). Volontairement spécifiques pour ne pas capter une
# investigation banale ; normalisées (sans accents, apostrophes→espaces).
_CATALOG_QUERY_PATTERNS = (
    "quelles donnees",
    "quelles collections",
    "quels datasets",
    "quels jeux de donnees",
    "donnees disponibles",
    "collections disponibles",
    "datasets disponibles",
    "liste les collections",
    "lister les collections",
    "liste des collections",
    "data qu on a",
    "donnees qu on a",
    "donnees dont on dispose",
    "donnees qu on possede",
    "ce qu on a comme donnees",
    "qu est ce qu on a comme donnees",
    "nos collections",
    "nos datasets",
)


def _is_catalog_query(text: str) -> bool:
    """True si la requête interroge ce que le système DÉTIENT (lister/consulter collections)."""
    low = _norm(text)
    return any(p in low for p in _CATALOG_QUERY_PATTERNS)


def _data_agent_id(catalog: dict[str, dict]) -> str | None:
    """Repère l'agent de gestion de données (discipline DATAMGT, sinon heuristique manifest)."""
    for aid, meta in catalog.items():
        if (meta.get("discipline") or "").upper() == "DATAMGT":
            return aid
    for aid, meta in catalog.items():
        hay = _norm(
            f"{aid} {meta.get('when_to_use', '')} {meta.get('description', '')}"
        )
        if "gestion de donnees" in hay or "dataset" in hay or "collections" in hay:
            return aid
    return None


# Tournures citoyennes SANS ambiguïté de domaine parlementaire. Le routeur LLM tend
# soit à sur-mobiliser (fan-out vers tous les agents), soit à demander une précision
# inutile → on capte ces tournures en déterministe et on route vers LA discipline.
# Ordre = priorité (une question « comment a voté mon député » part sur VOTES, qui
# résout lui-même le député). Normalisées comme `_norm` (minuscule, sans accents).
_PARL_GUARDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("VOTES", (
        "comment a vote", "comment ont vote", "ont ils vote", "ont elles vote",
        "a t il vote", "a t elle vote", "a vote pour", "a vote contre",
        "qui a vote", "resultat du scrutin", "resultat du vote", "scrutin n",
        "vote sur la motion de censure", "vote de la motion de censure",
        "vote des deputes", "votes des deputes", "adopte ou rejete",
    )),
    ("DEPUTES", (
        "qui est le depute", "qui est la deputee", "mon depute", "ma deputee",
        "fiche du depute", "quel depute", "quelle deputee",
        "depute de la", "depute de ma", "deputee de la", "circonscription",
    )),
    ("LOIS", (
        "ou en est le projet de loi", "ou en est la proposition de loi",
        "ou en est la loi", "ou en est le texte", "parcours de la loi",
        "parcours du texte", "navette du texte", "dossier legislatif",
        "etapes de la loi", "avancement de la loi", "avancement du texte",
    )),
)


# Tournures de pédagogie pure : la réponse est une EXPLICATION (routeur → chitchat
# vulgarisé), pas une donnée — la garde parlementaire ne doit PAS s'y déclencher.
_PEDAGOGY_PATTERNS = (
    "c est quoi", "qu est ce qu", "a quoi sert", "comment fonctionne",
    "explique moi", "expliquez moi", "definition", "ca veut dire quoi",
    "que veut dire", "comment marche", "comment une loi", "comment est votee",
)

# Lexique parlementaire DÉTERMINISTE (dérivé du skill vulgarisation-loi, sourcé).
# Une question de pédagogie pure sur un de ces termes reçoit cette réponse SANS
# appel LLM : correcte, neutre, sourcée, latence nulle — et disponible même LLM
# down ou rate-limité (429). Le terme le plus long gagne (« motion de censure »
# avant « motion »).
_SRC_VP = "Source : vie-publique.fr (fiche « Comment est votée une loi ? »)."
_LEXIQUE_PARLEMENTAIRE: tuple[tuple[str, str], ...] = (
    ("49 3", "L'article 49.3 de la Constitution permet au Gouvernement de faire adopter "
             "un texte à l'Assemblée nationale sans vote. En contrepartie, les députés "
             "peuvent déposer une motion de censure (un vote pour renverser le "
             "Gouvernement) dans les 24 heures. Si la motion n'obtient pas la majorité "
             "absolue (289 voix), le texte est considéré comme adopté. "
             "Source : article 49 de la Constitution — conseil-constitutionnel.fr."),
    ("motion de censure", "Une motion de censure est le vote par lequel l'Assemblée "
             "nationale peut renverser le Gouvernement. Elle doit être signée par au "
             "moins un dixième des députés, et n'est adoptée que si la majorité absolue "
             "des membres de l'Assemblée (289 voix) vote pour. Seuls les votes « pour » "
             "sont comptés : ne pas voter revient à ne pas la soutenir. "
             "Source : article 49 de la Constitution — assemblee-nationale.fr."),
    ("navette parlementaire", "La navette parlementaire est l'aller-retour d'un texte de "
             "loi entre l'Assemblée nationale et le Sénat. Chaque chambre examine et "
             "peut modifier le texte, jusqu'à ce que les deux votent la même version. "
             "En cas de désaccord persistant, une commission mixte paritaire (7 députés "
             "+ 7 sénateurs) cherche un compromis ; sinon, l'Assemblée peut avoir le "
             f"dernier mot. {_SRC_VP}"),
    ("commission mixte paritaire", "La commission mixte paritaire (CMP) réunit 7 députés "
             "et 7 sénateurs pour trouver un compromis quand l'Assemblée et le Sénat "
             "n'arrivent pas à voter un texte identique. Si la CMP échoue, le texte "
             "repart en navette et l'Assemblée nationale peut avoir le dernier mot. "
             f"{_SRC_VP}"),
    ("scrutin public", "Un scrutin public est un vote des députés dont le détail est "
             "publié : on sait qui a voté pour, contre, qui s'est abstenu et qui n'a pas "
             "pris part au vote (« non-votant » : présent mais ne votant pas — ce n'est "
             "pas la même chose qu'une abstention). Tous les scrutins publics sont "
             "consultables sur assemblee-nationale.fr. "
             "Source : data.assemblee-nationale.fr (jeu de données Scrutins)."),
    ("amendement", "Un amendement est une proposition de modification d'un texte de loi "
             "en cours de discussion : ajouter, supprimer ou réécrire un passage. Tout "
             "député peut en déposer. Les amendements sont examinés en commission puis "
             f"en séance, et votés un par un. {_SRC_VP}"),
    ("promulgation", "La promulgation est la signature de la loi par le Président de la "
             "République, dans les 15 jours suivant son adoption définitive. C'est elle "
             "qui rend la loi applicable, après publication au Journal officiel. Avant "
             f"cette étape, un texte adopté n'est pas encore une loi. {_SRC_VP}"),
    ("proposition de loi", "Une proposition de loi est un texte proposé par un ou "
             "plusieurs parlementaires (députés ou sénateurs). Quand le texte vient du "
             "Gouvernement, on parle au contraire de « projet de loi ». Les deux suivent "
             f"ensuite le même parcours : commissions, séance, navette. {_SRC_VP}"),
    ("projet de loi", "Un projet de loi est un texte proposé par le Gouvernement (quand "
             "il vient de parlementaires, on parle de « proposition de loi »). Il est "
             "examiné en commission, débattu et amendé en séance, puis fait la navette "
             f"entre l'Assemblée et le Sénat jusqu'à un accord. {_SRC_VP}"),
    ("rapporteur", "Le rapporteur est le député chargé d'étudier un texte en détail au "
             "nom d'une commission : il analyse le texte, propose des modifications et "
             f"guide le débat en séance. {_SRC_VP}"),
    ("groupe parlementaire", "Un groupe parlementaire est une « équipe » de députés "
             "partageant une orientation politique (au moins 15 députés à l'Assemblée). "
             "Les groupes organisent les débats, les temps de parole et les postes. Un "
             "député sans groupe est dit « non-inscrit ». "
             "Source : Règlement de l'Assemblée nationale — assemblee-nationale.fr."),
    ("question au gouvernement", "Les questions au Gouvernement (QAG) sont des questions "
             "posées en séance publique par les députés aux ministres, retransmises en "
             "direct. Elles ont lieu chaque semaine et permettent de contrôler l'action "
             "du Gouvernement. Source : assemblee-nationale.fr."),
)


def _lexique_reply(text: str) -> str | None:
    """Réponse pédagogique déterministe si la question porte sur un terme du lexique."""
    n = _norm(text)
    if not any(p in n for p in _PEDAGOGY_PATTERNS):
        return None
    n_compact = n.replace(" ", "")
    for terme, reponse in _LEXIQUE_PARLEMENTAIRE:
        if terme in n or terme.replace(" ", "") in n_compact:
            return reponse
    return None


def _parl_guard_discipline(text: str) -> str | None:
    """Discipline (VOTES/DEPUTES/LOIS) si la question matche une tournure sans ambiguïté."""
    n = _norm(text)
    if any(p in n for p in _PEDAGOGY_PATTERNS):
        return None
    for discipline, phrases in _PARL_GUARDS:
        if any(p in n for p in phrases):
            return discipline
    return None


def _agent_by_discipline(catalog: dict[str, dict], discipline: str) -> str | None:
    """Repère l'agent d'une discipline donnée dans le catalogue."""
    for aid, meta in catalog.items():
        if (meta.get("discipline") or "").upper() == discipline:
            return aid
    return None


# Verbes de délégation : « demande à… », « via… », « passe à… » → ordre laconique
# dont le SUJET est ailleurs (dans l'historique), pas dans le message courant.
_DELEGATION_VERBS = {
    "demande",
    "demandez",
    "envoie",
    "envoyez",
    "passe",
    "passez",
    "route",
    "routez",
    "lance",
    "lancez",
    "utilise",
    "utilisez",
    "fais",
    "faites",
    "via",
    "avec",
    "demander",
}


def _is_terse_directive(text: str) -> bool:
    """True si le message est un ordre court de délégation (« demande à Search »)."""
    words = _norm(text).split()
    if len(words) > 6:
        return False
    return any(w in _DELEGATION_VERBS for w in words)


def _last_substantive_user_topic(history: list[dict] | None) -> str:
    """Dernier message UTILISATEUR porteur d'un vrai sujet (pas un ordre laconique).

    Sert à ancrer l'instruction d'un agent quand le message courant n'est qu'une
    délégation (« demande à Search ») : le sujet (« …sur l'Allemagne ») vient du
    tour précédent.
    """
    for turn in reversed(history or []):
        if turn.get("role") != "user":
            continue
        t = (turn.get("text") or "").strip()
        if not t or _is_terse_directive(t) or len(t.split()) < 3:
            continue
        return t
    return ""


def default_instruction(text: str, agent_meta: dict, lang: str = "en") -> str:
    """Consigne de repli quand le LLM n'en fournit pas — ciblée par discipline, localisée.

    Sert hors mode forward (LLM simulé) ou quand le routeur omet l'instruction.
    Reste meilleure que l'ancien gabarit `Investiguer « … » via …`.
    """
    if lang == "fr":
        label = (agent_meta.get("discipline") or "").upper() or "SPÉCIALISÉ"
        return (
            f"En tant qu'agent {label}, analyse la demande et produis un livrable {label} "
            f"ciblé et actionnable. Demande : « {text} »"
        )
    label = (agent_meta.get("discipline") or "").upper() or "SPECIALIZED"
    return (
        f"As a {label} agent, analyze the request and produce a focused, actionable "
        f"{label} deliverable. Request: “{text}”"
    )


def _build_steps(
    text: str,
    agent_ids: list[str],
    agent_catalog: dict[str, dict],
    instructions: dict[str, str] | None = None,
    lang: str = "en",
) -> list[dict]:
    """Construit la liste de plan_steps pour les agents retenus (skip ceux inconnus).

    `instructions` (optionnel) = consignes ciblées par agent produites par le LLM
    routeur. À défaut (heuristique / LLM muet), on retombe sur `default_instruction`.
    """
    instructions = instructions or {}
    steps: list[dict] = []
    i = 0
    for agent_id in agent_ids:
        if agent_id not in agent_catalog:
            continue
        i += 1
        meta = agent_catalog.get(agent_id, {})
        instr = (instructions.get(agent_id) or "").strip() or default_instruction(
            text, meta, lang
        )
        steps.append(
            {
                "plan_step_id": f"step-{i}",
                "agent_type": agent_id,
                "instruction": instr,
            }
        )
    return steps


def build_report_prompt(
    text: str,
    results: list[dict],
    lang: str = "en",
    attachments: list[dict] | None = None,
    max_tokens: int | None = None,
    channel_prompt: str | None = None,
) -> str:
    """Compose le prompt de rédaction du rapport à partir des résultats réels des agents.

    `results` = [{agent_type, output}, ...] (sorties structurées des tâches `done`).
    On reformule chaque sortie en bloc lisible (summary, sources, localisations,
    détections) pour que le LLM produise une **vraie synthèse** au lieu d'un texte
    générique — fini le prompt vide. Le prompt système est localisé (`lang`).

    `attachments` : pièces jointes de l'analyste — réintroduites dans le prompt de
    rédaction pour que le rédacteur cite les sources d'origine (pas seulement les
    sorties d'agents). Budget contrôlé par `max_tokens` (défaut DEFAULT_LLM_MAX_TOKENS).
    """
    budget_tokens = _resolve_budget_tokens(max_tokens)
    # 70 % du budget réservé au prompt, 30 % à la réponse ; ~4 chars/token.
    _prompt_budget_chars = int(budget_tokens * 0.7 * 4)
    attachments_section = _build_attachments_section(
        attachments or [], _prompt_budget_chars, lang=lang
    )
    fr = lang == "fr"
    blocks: list[str] = []
    for r in results:
        agent = str(r.get("agent_type", "?")).upper()
        out = r.get("output") or {}
        lines = [f"### {'Apport' if fr else 'Contribution'} {agent}"]
        if out.get("summary"):
            lines.append(str(out["summary"]))
        # Blocs métier parlementaires : chiffres EXACTS injectés pour que la synthèse
        # ne puisse citer que des décomptes officiels (jamais inventés).
        sc = out.get("scrutin") or {}
        if sc.get("uid") or sc.get("titre"):
            lines.append(
                f"- scrutin n°{sc.get('numero', '?')} du {sc.get('date', '?')} : "
                f"{sc.get('titre', '')} — sort : {sc.get('sort', '?')} ; "
                f"pour {sc.get('pour', 0)}, contre {sc.get('contre', 0)}, "
                f"abstentions {sc.get('abstentions', 0)}, non-votants {sc.get('non_votants', 0)}"
            )
        pd = out.get("position_depute") or {}
        if pd.get("nom"):
            if pd.get("position") == "absent":
                lines.append(f"- {pd['nom']} ({pd.get('groupe', '?')}) n'apparaît pas dans ce "
                             "scrutin : il/elle n'a pas pris part au vote")
            else:
                lines.append(f"- vote de {pd['nom']} ({pd.get('groupe', '?')}) : "
                             f"{pd.get('position', '?')}")
        for g in (out.get("groupes") or [])[:8]:
            lines.append(
                f"- groupe {g.get('nom', '?')} : pour {g.get('pour', 0)}, "
                f"contre {g.get('contre', 0)}, abstention {g.get('abstention', 0)}, "
                f"non-votants {g.get('nonVotant', 0)}"
            )
        dep = out.get("depute") or {}
        if dep.get("nom_complet"):
            lines.append(
                f"- député : {dep['nom_complet']} — groupe {dep.get('groupe', '?')} — "
                f"{dep.get('circo', '?')} — "
                f"{'mandat en cours' if dep.get('actif') else 'ancien député (mandat terminé)'}"
            )
        st = out.get("stats") or {}
        if st.get("participation") is not None:
            lines.append(f"- participation aux scrutins publics : {st['participation']} % ; "
                         f"votes alignés avec son groupe : {st.get('loyaute', '?')} % "
                         "(calculs locaux)")
        dos = out.get("dossier") or {}
        if dos.get("titre"):
            lines.append(f"- dossier législatif : {dos['titre']} — statut : {dos.get('statut', '?')}")
        for e in (out.get("etapes") or [])[:10]:
            if e.get("libelle"):
                lines.append(f"- étape [{e.get('statut', '?')}] {e.get('date', '')} : "
                             f"{e['libelle']} ({e.get('chambre', '')})")
        for s in (out.get("sources") or [])[:5]:
            lines.append(
                f"- source officielle : {s.get('title', '')} — {s.get('url', '')} — "
                f"{s.get('snippet', '')}".strip()
            )
        blocks.append("\n".join(lines))
    # Règles de mise en forme communes aux deux langues. Le rapport est rendu côté
    # front dans un éditeur TipTap (page A4) — il a besoin de VRAIS titres `#` `##`
    # `###`, de VRAIS tableaux GFM (`| col | col |` + ligne séparatrice `|---|`),
    # et de paragraphes courts. Sans ces consignes le LLM se rabat sur du `**gras**`
    # à plat → rendu illisible (tout à la même taille, tableaux en bouillie).
    fmt_rules_fr = (
        "RÈGLES DE MISE EN FORME (impératives — la rédaction est lue dans un éditeur de page A4) :\n"
        "- Titre principal en `# Titre` (un seul, en haut).\n"
        "- Sections de premier niveau en `## Titre de section` (pas de **gras** comme substitut).\n"
        "- Paragraphes courts (2-4 phrases), séparés par une ligne vide.\n"
        "- Listes à puces avec `- ` et listes numérotées avec `1. `, `2. `, etc.\n"
        "- Tableaux STRICTEMENT au format GFM, AVEC la ligne de séparation, p. ex. :\n"
        "    | Groupe | Pour | Contre | Abstention |\n"
        "    |---|---|---|---|\n"
        "    | Exemple | 12 | 3 | 1 |\n"
        "  N'invente JAMAIS un tableau sans la ligne `|---|---|` — sinon il est cassé.\n"
        "- `**gras**` réservé aux mots-clés à l'intérieur d'un paragraphe (pas comme titre).\n"
        "- STRUCTURE : 1) la réponse directe à la question en 1-2 phrases ; 2) le contexte "
        "minimal pour comprendre ; 3) le détail (chiffres, étapes) ; 4) une section "
        "`## Sources` listant les liens officiels fournis ; 5) une courte section "
        "`## Pour aller plus loin` (UNE seule piste).\n"
    )
    vulgarisation_rules = (
        "RÈGLES DE VULGARISATION (impératives — tu écris pour un citoyen, pas un juriste) :\n"
        "1. EXACTITUDE D'ABORD : simplifier n'est pas approximer. N'invente JAMAIS un vote, "
        "un chiffre, une date ou une étape de procédure. Utilise UNIQUEMENT les chiffres "
        "fournis dans les ÉLÉMENTS COLLECTÉS. Si une donnée manque, dis-le explicitement.\n"
        "2. NEUTRALITÉ ABSOLUE : aucun jugement de valeur sur un vote, un groupe, un député "
        "ou une loi. Attention aux formulations orientées : écris « 45 députés ont voté "
        "pour », jamais « seulement 45 députés ont voté pour ». Emploie uniquement les noms "
        "de groupes fournis dans les données : n'ajoute ni coalition, ni alliance, ni "
        "étiquette politique qui n'y figure pas.\n"
        "3. SOURCER : chaque affirmation factuelle renvoie à sa source officielle "
        "(fournie dans les éléments collectés).\n"
        "4. NIVEAU DE LANGUE : un élève de 3e (~15 ans). Phrases courtes. Un concept par "
        "phrase. Mots courants d'abord, terme technique ensuite entre parenthèses si utile.\n"
        "5. JARGON DÉFINI À LA VOLÉE : chaque terme parlementaire employé (scrutin public, "
        "navette, CMP, motion de censure…) est défini en une phrase simple à sa première "
        "apparition.\n"
        "6. PIÈGES À NE JAMAIS COMMETTRE : un député qui n'apparaît pas dans un scrutin "
        "« n'a pas pris part au vote » (ne pas écrire « était absent » sans donnée "
        "explicite) ; « non-votant » ≠ « abstention » (deux positions distinctes) ; un "
        "texte adopté par l'Assemblée n'est PAS une loi (il reste la navette avec le Sénat, "
        "l'éventuelle commission mixte paritaire, le Conseil constitutionnel, la "
        "promulgation) ; la position d'un groupe ne vaut pas pour chacun de ses membres ; "
        "l'Assemblée nationale n'est pas le Parlement (Assemblée + Sénat).\n"
    )
    fmt_rules_en = (
        "FORMATTING RULES (mandatory — the report is rendered in an A4-page editor):\n"
        "- One top-level title with `# Title` (only one, at the very top).\n"
        "- First-level sections as `## 1. Section title` (do NOT use **bold** as a heading substitute).\n"
        "- Sub-sections as `### A. Sub-section`.\n"
        "- Short paragraphs (2–4 sentences), separated by a blank line.\n"
        "- Bulleted lists with `- ` and numbered lists with `1. `, `2. `, etc.\n"
        "- Tables STRICTLY in GFM format, WITH the separator row, e.g.:\n"
        "    | Unit | Goal | Place | Status |\n"
        "    |---|---|---|---|\n"
        "    | b2 | Capture | Kiev | Pending |\n"
        "  NEVER write a table without the `|---|---|` separator — it will render broken.\n"
        "- `**bold**` is for keywords inside a paragraph (never as a heading).\n"
        "- End with `## Confidence level` and `## Recommendation` sections.\n"
    )
    # Cellule d'état-major : oriente la synthèse vers le mandat de la cellule.
    cell_fr = (
        f"CONTEXTE CELLULE : tu rédiges pour une cellule d'état-major dont le mandat "
        f"est « {channel_prompt} ». Priorise ce qui sert ce mandat.\n\n"
        if channel_prompt
        else ""
    )
    cell_en = (
        f"CELL CONTEXT: you are writing for a staff cell whose mandate is "
        f'"{channel_prompt}". Prioritise what serves this mandate.\n\n'
        if channel_prompt
        else ""
    )
    if fr:
        findings = (
            "\n\n".join(blocks) if blocks else "(aucun résultat exploitable collecté)"
        )
        return (
            "Tu es le rédacteur d'« Hémicycle », l'assistant citoyen de l'Assemblée "
            "nationale. Rédige une EXPLICATION CITOYENNE claire, factuelle et sourcée en "
            "français, à partir des éléments collectés par les agents sur les données "
            "officielles. Ne réclame PAS de données supplémentaires : appuie-toi "
            "uniquement sur ce qui suit.\n\n"
            f"{cell_fr}"
            f"{vulgarisation_rules}\n"
            f"{fmt_rules_fr}\n"
            f"QUESTION DU CITOYEN : {text!r}\n"
            f"{attachments_section}\n"
            f"ÉLÉMENTS COLLECTÉS (les seuls chiffres autorisés) :\n{findings}\n"
        )
    findings = "\n\n".join(blocks) if blocks else "(no usable result collected)"
    return (
        "You are the analyst-writer of an intelligence orchestrator. "
        "Write a structured, actionable SUMMARY in English from the elements collected "
        "by the agents. Do NOT ask for additional data: rely only on what follows.\n\n"
        f"{cell_en}"
        f"{fmt_rules_en}\n"
        f"INVESTIGATION SUBJECT: {text!r}\n"
        f"{attachments_section}\n"
        f"COLLECTED ELEMENTS:\n{findings}\n"
    )


def build_chat_summary_prompt(
    text: str, results: list[dict], lang: str = "en", channel_prompt: str | None = None
) -> str:
    """Prompt « synthèse chat » — réponse courte (3-5 phrases) pour clore le tour SANS rapport.

    Utilisé quand l'analyste n'a PAS coché le bouton Rapport : on lui doit malgré tout
    un mot d'orchestrateur dans le chat qui répond à sa question en s'appuyant sur
    ce que les agents ont remonté. Pas de titres, pas de tableaux, pas de sections —
    juste un paragraphe direct, factuel, conversationnel.
    """
    fr = lang == "fr"
    blocks: list[str] = []
    for r in results:
        agent = str(r.get("agent_type", "?")).upper()
        out = r.get("output") or {}
        bits = [f"[{agent}]"]
        if out.get("summary"):
            bits.append(str(out["summary"])[:600])
        sc = out.get("scrutin") or {}
        if sc.get("uid") or sc.get("titre"):
            bits.append(f"[scrutin n°{sc.get('numero', '?')} du {sc.get('date', '?')} — "
                        f"{sc.get('sort', '?')} : pour {sc.get('pour', 0)}, "
                        f"contre {sc.get('contre', 0)}, abst. {sc.get('abstentions', 0)}, "
                        f"NV {sc.get('non_votants', 0)}]")
        pd = out.get("position_depute") or {}
        if pd.get("nom"):
            bits.append(f"[{pd['nom']} : "
                        f"{'n_a pas pris part au vote' if pd.get('position') == 'absent' else pd.get('position', '?')}]")
        dep = out.get("depute") or {}
        if dep.get("nom_complet"):
            bits.append(f"[{dep['nom_complet']} — {dep.get('groupe', '?')} — {dep.get('circo', '?')}]")
        dos = out.get("dossier") or {}
        if dos.get("titre"):
            bits.append(f"[dossier : {dos['titre']} — statut : {dos.get('statut', '?')}]")
        if out.get("sources"):
            urls = ", ".join(s.get("url", "") for s in out["sources"][:2] if s.get("url"))
            bits.append(f"(sources officielles : {urls})")
        blocks.append(" ".join(bits))
    findings = "\n".join(blocks) or (
        "(aucun résultat exploitable collecté)"
        if fr
        else "(no usable result collected)"
    )
    # Cellule d'état-major : la réponse doit tenir compte de l'interlocuteur (mandat
    # de la cellule) et privilégier ce qui sert sa mission.
    cell_fr = (
        f"Tu réponds dans le canal d'une cellule d'état-major dont le mandat est : "
        f"« {channel_prompt} ». Oriente ta réponse vers ce mandat et tiens compte de "
        f"l'interlocuteur (la cellule).\n"
        if channel_prompt
        else ""
    )
    cell_en = (
        f"You are answering in a staff-cell channel whose mandate is: "
        f'"{channel_prompt}". Steer your reply toward this mandate and take the '
        f"interlocutor (the cell) into account.\n"
        if channel_prompt
        else ""
    )
    if fr:
        return (
            "Tu es « Hémicycle », l'assistant citoyen de l'Assemblée nationale. Un "
            "citoyen a posé une question et tes agents ont interrogé les données "
            "officielles. Rédige une RÉPONSE DIRECTE et CONCISE (3 à 5 phrases, un "
            "seul paragraphe) en français clair (niveau collège), factuelle et "
            "NEUTRE (aucun jugement de valeur, pas de « seulement X députés »). "
            "Utilise UNIQUEMENT les chiffres remontés ci-dessous, exactement — "
            "n'invente jamais un vote, un chiffre ou une date ; si la donnée "
            "manque, dis-le. Emploie uniquement les NOMS de groupes fournis dans "
            "les données : n'ajoute ni coalition, ni alliance, ni étiquette "
            "politique qui n'y figure pas. Cite la source officielle en fin de "
            "réponse. "
            "Rappels : « non-votant » ≠ « abstention » ; un député hors du scrutin "
            "« n'a pas pris part au vote » ; un texte adopté n'est pas encore une "
            "loi. Ne dis pas « les agents ont fait X » : restitue l'INFORMATION. "
            "AUCUN préambule ni méta-commentaire : ne commence pas par « Voici une "
            "synthèse », ne recopie pas les étiquettes [AGENT_…] — commence "
            "directement par la réponse.\n\n"
            f"{cell_fr}"
            f"QUESTION DU CITOYEN : {text!r}\n\n"
            f"ÉLÉMENTS COLLECTÉS (les seuls chiffres autorisés) :\n{findings}\n\n"
            "Réponse (français, paragraphe direct) :"
        )
    return (
        "You are the orchestrator of intelligence agents. The analyst asked a "
        "question and the agents have worked. Write a DIRECT, CONCISE REPLY "
        "(3 to 5 sentences, one paragraph) that answers their request using "
        "what the agents reported. No heading, no section, no table, no bullet "
        'list — a conversational paragraph. Don\'t say "the agents did X": '
        "restate the INFORMATION (who, where, when, how many), not the process. "
        'End with one sentence that opens the next step if relevant ("Want me '
        'to dig into X?").\n\n'
        f"{cell_en}"
        f"ANALYST REQUEST: {text!r}\n\n"
        f"ELEMENTS COLLECTED BY THE AGENTS:\n{findings}\n\n"
        "Reply (English, direct paragraph):"
    )


def build_chat_summary_fallback(
    text: str, results: list[dict], lang: str = "en"
) -> str:
    """Synthèse de repli SANS LLM — utilisée si le pool est HS lors d'un tour sans rapport.

    Garantit qu'on ne laisse JAMAIS l'analyste sans réponse finale, même si tout
    le sous-système LLM est en rade. Texte court, factuel, dérivé directement des
    sorties d'agents.
    """
    fr = lang == "fr"
    if not results:
        return (
            "Aucun agent n'a remonté d'élément exploitable sur cette requête."
            if fr
            else "No agent reported any usable element on this request."
        )
    pieces: list[str] = []
    for r in results:
        agent = str(r.get("agent_type", "?")).upper()
        out = r.get("output") or {}
        s = (out.get("summary") or out.get("reply") or "").strip()
        if s:
            pieces.append(f"{agent} : {s[:240]}")
        elif out.get("scrutin"):
            sc = out["scrutin"]
            pieces.append(f"{agent} : scrutin n°{sc.get('numero', '?')} — "
                          f"{sc.get('sort', '?')} (pour {sc.get('pour', 0)}, "
                          f"contre {sc.get('contre', 0)}).")
        elif out.get("depute"):
            pieces.append(f"{agent} : fiche de {out['depute'].get('nom_complet', '?')} trouvée.")
        elif out.get("etapes"):
            pieces.append(f"{agent} : {len(out['etapes'])} étape(s) du parcours du texte.")
        elif out.get("sources"):
            pieces.append(f"{agent} : {len(out['sources'])} source(s) officielle(s) trouvée(s).")
    return " ".join(pieces) or (
        "Investigation terminée." if fr else "Investigation complete."
    )


def build_report_fallback(text: str, results: list[dict], lang: str = "en") -> str:
    """Synthèse DÉTERMINISTE de repli (sans LLM) quand la rédaction LLM échoue.

    Garantit que la conv ne reste JAMAIS bloquée en WRITING_REPORT : si l'appel
    LLM de rédaction lève (timeout, pool indispo), on assemble directement les
    sorties déjà collectées par les agents en un markdown lisible. Pas d'appel
    réseau — donc toujours disponible.
    """
    fr = lang == "fr"
    title = f"# {'Synthèse' if fr else 'Summary'} — {text[:120]}"
    note = (
        "_Rédaction automatique de repli (service de synthèse indisponible)._"
        if fr
        else "_Automatic fallback summary (synthesis service unavailable)._"
    )
    blocks = [title, note]
    for r in results:
        agent = str(r.get("agent_type", "?"))
        out = r.get("output") or {}
        blocks.append(f"\n## {agent}")
        if out.get("summary"):
            blocks.append(str(out["summary"]))
        sc = out.get("scrutin") or {}
        if sc.get("uid") or sc.get("titre"):
            blocks.append(
                f"- Scrutin n°{sc.get('numero', '?')} du {sc.get('date', '?')} — "
                f"{sc.get('sort', '?')} : {sc.get('pour', 0)} pour, {sc.get('contre', 0)} "
                f"contre, {sc.get('abstentions', 0)} abstentions, "
                f"{sc.get('non_votants', 0)} non-votants."
            )
        etapes = out.get("etapes") or []
        if etapes:
            lignes = "\n".join(f"- {e.get('date', '')} : {e.get('libelle', '')}"
                               for e in etapes[:8] if e.get("libelle"))
            blocks.append(lignes)
        srcs = out.get("sources") or []
        if srcs:
            liens = "\n".join(f"- [{s.get('title', 'source')}]({s.get('url', '')})"
                              for s in srcs[:4] if s.get("url"))
            if liens:
                blocks.append(f"{'**Sources officielles :**' if fr else '**Sources:**'}\n{liens}")
    if len(blocks) == 2:
        blocks.append(
            "_(aucun résultat exploitable)_" if fr else "_(no usable result)_"
        )
    return "\n\n".join(blocks)


def plan_investigation(
    text: str, agent_catalog: dict[str, dict], lang: str = "en"
) -> list[dict]:
    """Heuristique de repli : un step par agent dont le manifest croise la requête.

    Contrairement à la version précédente, **aucun fallback "dispatch à tous"** :
    si rien ne match, on retourne `[]` et c'est à l'appelant de décider quoi faire
    (typiquement passer en chitchat dans la FSM).
    """
    if not agent_catalog:
        return []
    tokens = _keywords(text)
    chosen = [
        aid for aid, meta in agent_catalog.items() if _agent_matches(tokens, meta)
    ]
    return _build_steps(text, chosen, agent_catalog, lang=lang)


def _parse_router_json(raw: str) -> dict | None:
    """Tolère un préambule éventuel autour du JSON — retourne dict ou None si KO."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    candidate = m.group(0) if m else raw
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_agents_instructions(
    data: dict, catalog: dict[str, dict]
) -> tuple[list[str], dict[str, str]]:
    """Extrait (agents connus, instructions) du JSON routeur.

    `agents` accepte deux formes : ["mi_search", …] (legacy) ou
    [{"id":"mi_search","instruction":"…"}, …] (consignes ciblées par agent).
    """
    agents: list[str] = []
    instructions: dict[str, str] = {}
    for a in data.get("agents") or []:
        if isinstance(a, dict):
            aid = a.get("id") or a.get("agent_type")
            if aid in catalog and aid not in agents:
                agents.append(aid)
                instr = str(a.get("instruction") or "").strip()
                if instr:
                    instructions[aid] = instr
        elif isinstance(a, str) and a in catalog and a not in agents:
            agents.append(a)
    return agents, instructions


_MEMORY_KINDS = {"about_user", "learned_fact"}


def _extract_proposed_memories(data: dict) -> list[dict]:
    """Filtre `proposed_memories` du JSON routeur : kind validé, contenu non vide.

    Rejette les `kind` hors `_MEMORY_KINDS` (le LLM peut halluciner `entity`/`preference`)
    et coupe le contenu à 500 caractères (limite raisonnable pour un fait atomique).
    """
    raw = data.get("proposed_memories") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        content = str(item.get("content") or "").strip()
        if kind not in _MEMORY_KINDS or not content:
            continue
        out.append({"kind": kind, "content": content[:500]})
    return out


def _extract_relevant_memory_ids(data: dict, known_ids: set[int]) -> list[int]:
    """Filtre `relevant_memory_ids` du JSON routeur : ne garde que les ids connus.

    Le LLM peut omettre le champ (fallback heuristique, modèle qui zappe), renvoyer
    des strings ("1" au lieu de 1), ou inventer un id absent de la mémoire envoyée —
    on coerce en int et on intersecte avec `known_ids` pour éviter d'afficher du
    bruit côté widget.
    """
    raw = data.get("relevant_memory_ids") or []
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for v in raw:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if i in known_ids and i not in out:
            out.append(i)
    return out


def _format_history(history: list[dict] | None) -> str:
    """Sérialise l'historique récent en bloc lisible par le LLM (vide si rien)."""
    if not history:
        return ""
    lines = []
    for turn in history:
        role = turn.get("role", "user")
        text = (turn.get("text") or "").strip().replace("\n", " ")
        if not text:
            continue
        tag = "User" if role == "user" else "Assistant"
        lines.append(f"{tag}: {text}")
    return "\n".join(lines)


# Widgets du tableau que l'orchestrateur peut faire apparaître à la demande de
# l'analyste (« ouvre / affiche / spawn le widget X »). Clé = `specId` attendu
# côté frontend (cf. widgetRegistry.tsx) ; valeur = description bilingue pour
# guider le choix du routeur LLM.
SPAWNABLE_WIDGETS: dict[str, str] = {
    "texteditor": "traitement de texte / éditeur de synthèse — rich text editor",
    "viewfile": "lecteur de fichier (PDF, image, Word, Excel…) — universal file viewer",
    "notes": "bloc-notes / post-it — sticky notes",
}


def _widget_catalog_desc() -> str:
    """Liste formatée des widgets ouvrables, injectée dans le prompt routeur."""
    return "\n".join(f"  - {key} : {desc}" for key, desc in SPAWNABLE_WIDGETS.items())


def _extract_widgets(data: dict) -> list[str]:
    """Extrait du JSON routeur les widgets à ouvrir, filtrés au catalogue (dédup)."""
    raw = data.get("widgets") or data.get("open_widgets") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for w in raw:
        key = str(w).strip().lower()
        if key in SPAWNABLE_WIDGETS and key not in out:
            out.append(key)
    return out


def _build_routing_prompt(
    text: str,
    agent_catalog: dict[str, dict],
    history: list[dict] | None = None,
    lang: str = "en",
    memories: list[dict] | None = None,
    ref_context: str | None = None,
    self_context: str | None = None,
    attachments: list[dict] | None = None,
    max_tokens: int | None = None,
    channel_prompt: str | None = None,
) -> str:
    """Compose le prompt JSON-only envoyé au LLM pour décider intent + agents + reply.

    L'historique récent (max ~8 messages) est injecté pour interpréter les suivis :
    « demande à l'agent Search » ou « en France, avec la recherche fédérée » n'ont de
    sens qu'en regard du tour précédent. Le prompt impose une **posture
    d'orchestrateur** (router/agir, jamais refuser ni renvoyer le travail à
    l'analyste) et distingue trois traitements (investigate / data_ops / chitchat).
    Localisé par `lang` : la `reply`/`instruction` produites sortent dans cette langue.
    `self_context` : résultats (widgets) déjà produits dans la MÊME conversation —
    déclenche le bloc PRIORITÉ même sans mémoire LT ni @contexte.
    `attachments` : pièces jointes de l'analyste — injectées dans le prompt pour que
    le LLM puisse détecter la langue, les entités, etc. Budget contrôlé par `max_tokens`.
    """
    history_block = _format_history(history)
    # Budget caractères disponibles pour la section PJ : on réserve 30 % du
    # budget total pour la réponse du LLM et garde 70 % pour le prompt ; la
    # section PJ est la seule tronquable (les autres sections sont bornées).
    _budget_max = _resolve_budget_tokens(max_tokens)
    _prompt_budget_chars = int(_budget_max * 0.7) * 4  # tokens → chars (~4 chars/token)
    if lang == "fr":
        catalog_desc = (
            "\n".join(
                f"- {aid} (discipline={meta.get('discipline', '?')}): "
                f"{meta.get('when_to_use') or meta.get('description') or '(pas de description)'}"
                for aid, meta in agent_catalog.items()
            )
            or "(aucun agent enregistré)"
        )
        history_section = (
            f"\nHISTORIQUE RÉCENT DE LA CONVERSATION (du plus ancien au plus récent) :\n"
            f"{history_block}\n"
            if history_block
            else ""
        )
        memory_section = ""
        if memories:
            # Chaque ligne porte son id pour que le LLM puisse renvoyer `relevant_memory_ids`.
            lines = [
                f"- [id={m['id']}] {m['content']}" for m in memories if m.get("content")
            ]
            if lines:
                memory_section = (
                    "\nMÉMOIRE INTERNE DISPONIBLE (faits déjà connus du système — sélectionne "
                    "ceux réellement pertinents pour la requête courante via `relevant_memory_ids`) :\n"
                    + "\n".join(lines)
                    + "\n"
                )
        self_context_section = (
            f"\nRÉSULTATS DÉJÀ PRODUITS DANS CETTE CONVERSATION (synthèses d'agents / rapports "
            f"déjà fournis à l'analyste) :\n{self_context}\n"
            if self_context
            else ""
        )
        ref_context_section = (
            f"\nCONTEXTE DES CONVERSATIONS RÉFÉRENCÉES (historique fourni par l'analyste) :\n"
            f"{ref_context}\n"
            if ref_context
            else ""
        )
        # Connaissance déjà disponible (mémoire / résultats intra-conv / @contexte) : on impose,
        # AVANT le routage, de répondre directement si la réponse y est. Placé en tête pour primer
        # sur la posture « investigate » (sinon le routeur spawne un agent par défaut).
        priority_block = (
            "PRIORITÉ ABSOLUE — RÉPONDRE DEPUIS LA CONNAISSANCE DÉJÀ DISPONIBLE :\n"
            "Avant toute autre considération, examine la MÉMOIRE INTERNE, les RÉSULTATS DÉJÀ "
            "PRODUITS DANS CETTE CONVERSATION et le CONTEXTE DES CONVERSATIONS RÉFÉRENCÉES "
            "fournis plus bas. Si la réponse à la requête s'y trouve DÉJÀ (en tout ou en "
            "substance), tu DOIS répondre directement : intent=chitchat, la réponse complète "
            "dans `reply`, AUCUN agent dans `agents`. Ne relance JAMAIS une investigation pour "
            "une information déjà connue ou déjà fournie. Cette règle PRIME sur la posture "
            "d'orchestrateur et le routage ci-dessous.\n\n"
            if (memory_section or self_context_section or ref_context_section)
            else ""
        )
        # Salon d'état-major : oriente le plan vers le mandat de la cellule (G1 = personnel,
        # G2 = renseignement…). Ne restreint pas brutalement, mais priorise ce qui sert la cellule.
        channel_section = (
            "CONTEXTE — CELLULE D'ÉTAT-MAJOR :\n"
            f"Cette requête provient d'un salon de cellule dont le mandat est : « {channel_prompt} ». "
            "Oriente le plan et le choix des agents vers ce qui sert ce mandat ; une information hors "
            "périmètre n'est pertinente que si elle a un impact direct sur la mission de la cellule.\n\n"
            if channel_prompt
            else ""
        )
        return (
            "Tu es le ROUTEUR d'« Hémicycle », un orchestrateur d'agents au service des citoyens "
            "pour comprendre l'activité de l'Assemblée nationale (votes, députés, lois). Tu reçois "
            "la question du citoyen ET l'historique récent, et tu décides du traitement.\n\n"
            f"{priority_block}"
            f"{channel_section}"
            "POSTURE (essentiel) : tu ES l'orchestrateur — c'est TOI qui parles aux agents et qui "
            "déclenches les tâches. N'écris JAMAIS que tu « ne peux pas exécuter une demande », que "
            "tu « n'as pas accès » à un agent ou aux données, ni que le citoyen doit « utiliser "
            "l'agent X » ou « reformuler ». Ton métier, c'est de router. AGIS.\n\n"
            "NEUTRALITÉ ABSOLUE : jamais de jugement de valeur sur un vote, un groupe, un député "
            "ou une loi. Tu décris des faits sourcés ; le citoyen se fait son opinion.\n\n"
            "QUATRE traitements possibles :\n"
            "  - `investigate` : la question (seule ou COMBINÉE au contexte des tours précédents) "
            "porte sur des DONNÉES parlementaires précises : un scrutin/vote, un député ou un "
            "groupe, un texte/dossier législatif. Mobilise le ou les agents spécialisés "
            "pertinents — EN RÈGLE GÉNÉRALE UN SEUL agent, deux au grand maximum si la question "
            "combine vraiment deux domaines (chaque agent ajoute 20-40 s de latence).\n"
            "  - `data_ops` : la requête porte sur NOTRE patrimoine de données — soit INTERROGER ce "
            "que le système détient (« quelles données / quelles collections a-t-on sur X », lister "
            "ou consulter des datasets, état du backend de données), soit une ACTION sur la donnée "
            "(ingérer / verser / importer / indexer / créer / modifier / supprimer une collection ou "
            "un dataset). Mobilise l'agent de gestion de données. Un nom de solution cité comme CIBLE "
            "de versement (« dans la solution OSINT ») indique OÙ stocker, PAS une demande d'analyse.\n"
            "  - `personalize` : l'analyste veut t'apprendre quelque chose SUR LUI (rôle, "
            "préférences, contraintes : « personnalise-toi », « apprends que je préfère X », "
            "« retiens que mon rôle est Y ») OU t'enseigner un fait du monde à mémoriser "
            "(« retiens que Z », « note que… »). Dans ce cas : (a) si l'analyste a déjà donné "
            "des éléments concrets, extrais-les en `proposed_memories` (kind=`about_user` pour "
            "tout ce qui parle de l'analyste, `learned_fact` pour un fait du monde) ; (b) sinon, "
            "pose UNE question ciblée dans `reply` (rôle, langue préférée, format de rapport, "
            "domaines de spécialité…). AUCUN agent. Les `proposed_memories` seront proposés à "
            "l'analyste via un widget de confirmation — n'écris RIEN en mémoire toi-même.\n"
            "  - `chitchat` : (a) la requête est purement conversationnelle (salutation, "
            "remerciement, question sur toi-même) → réponds brièvement dans `reply` ; "
            "(b) PÉDAGOGIE PARLEMENTAIRE PURE — la question porte sur le fonctionnement des "
            "institutions et non sur une donnée précise (« c'est quoi le 49.3 ? », « comment une "
            "loi est-elle votée ? », « c'est quoi la navette parlementaire ? », « à quoi sert une "
            "motion de censure ? ») → réponds toi-même dans `reply` avec une explication "
            "vulgarisée COMPLÈTE : niveau collège, phrases courtes, chaque terme technique défini "
            "à la volée, factuelle et neutre, AUCUN agent mobilisé. Si la requête est ACTIONNABLE "
            "mais trop imprécise pour router, choisis `chitchat` et pose dans `reply` UNE seule "
            "question de clarification courte et concrète (jamais un refus).\n\n"
            "RÈGLES :\n"
            "  - Suivi/complément d'un tour précédent (ajout d'une zone géo, d'un agent, « et en "
            "France ? ») : traite l'ENSEMBLE comme une requête concrète et route (investigate/data_ops).\n"
            "  - Si le citoyen nomme explicitement un agent disponible (« demande à l'agent "
            "votes »…), RETIENS-LE dans `agents` et déduis le SUJET du dernier tour pertinent de "
            "l'historique. Ne réponds pas que tu ne peux pas le solliciter.\n"
            "  - Pour CHAQUE agent retenu, rédige une `instruction` : consigne courte, ACTIONNABLE "
            "et spécifique à son domaine, qui CONSERVE les éléments saillants de la question "
            "(nom du député, titre ou numéro du scrutin, date, titre du texte de loi…) — au besoin "
            "RÉCUPÉRÉS DANS L'HISTORIQUE (ex. agent_votes : « Retrouver le scrutin sur la motion "
            "de censure du 8 octobre 2024 et donner le vote du député Untel ainsi que le détail "
            "par groupe »).\n"
            "  - Rédige `reply` et chaque `instruction` EN FRANÇAIS.\n"
            "  - `relevant_memory_ids` : liste des ids (entiers) des lignes de MÉMOIRE INTERNE "
            "DISPONIBLE que tu as effectivement utilisées pour formuler ta décision/réponse — "
            "liste vide si aucune n'est pertinente. Ne mets PAS d'id qui n'apparaît pas dans la "
            "section MÉMOIRE INTERNE DISPONIBLE.\n"
            "  - `proposed_memories` : UNIQUEMENT si intent=`personalize`. Liste de "
            '`{"kind":"about_user"|"learned_fact","content":"<une phrase courte au présent>"}`. '
            "Une phrase = un fait atomique (ne combine pas plusieurs faits). `about_user` pour "
            "tout ce qui parle de l'analyste (rôle, préférences, contraintes, identité), "
            "`learned_fact` pour un fait du monde. Liste vide si rien à mémoriser pour ce tour "
            "(ex. tu poses une question dans `reply`).\n"
            "  - `widgets` : si l'analyste demande d'OUVRIR / AFFICHER / SPAWNER un outil ou "
            "widget du tableau (pas d'effectuer une tâche d'analyse), mets dans `widgets` la liste "
            "des clés correspondantes, avec intent=`chitchat`, AUCUN agent, et `reply` = courte "
            "confirmation (ex. « J'ouvre le widget de traduction. »). N'invente PAS de clé : choisis "
            "UNIQUEMENT dans le catalogue ci-dessous. Liste vide si la requête n'est pas une demande "
            "d'ouverture de widget.\n"
            f"WIDGETS OUVRABLES :\n{_widget_catalog_desc()}\n"
            f"\nAGENTS DISPONIBLES :\n{catalog_desc}\n"
            f"{history_section}"
            f"{memory_section}"
            f"{self_context_section}"
            f"{ref_context_section}"
            + _build_attachments_section(
                attachments or [], _prompt_budget_chars, lang="fr"
            )
            + f"\nNOUVELLE QUESTION DU CITOYEN : {text!r}\n\n"
            "Réponds STRICTEMENT en JSON valide, sans markdown ni commentaire, avec exactement cette "
            "forme :\n"
            '{"intent":"investigate"|"data_ops"|"personalize"|"chitchat",'
            '"agents":[{"id":"<id_agent>","instruction":"<mission ciblée pour cet agent>"}, ...],'
            '"reply":"<texte de réponse si chitchat/personalize, sinon chaîne vide>",'
            '"widgets":["<clé widget à ouvrir>", ...],'
            '"relevant_memory_ids":[<int>, ...],'
            '"proposed_memories":[{"kind":"about_user"|"learned_fact","content":"<fait>"}, ...]}'
        )
    catalog_desc = (
        "\n".join(
            f"- {aid} (discipline={meta.get('discipline', '?')}): "
            f"{meta.get('when_to_use') or meta.get('description') or '(no description)'}"
            for aid, meta in agent_catalog.items()
        )
        or "(no agent registered)"
    )
    history_section = (
        f"\nRECENT CONVERSATION HISTORY (oldest to newest):\n{history_block}\n"
        if history_block
        else ""
    )
    memory_section = ""
    if memories:
        lines = [
            f"- [id={m['id']}] {m['content']}" for m in memories if m.get("content")
        ]
        if lines:
            memory_section = (
                "\nAVAILABLE INTERNAL MEMORY (facts already known by the system — select the ones "
                "actually relevant to the current request via `relevant_memory_ids`):\n"
                + "\n".join(lines)
                + "\n"
            )
    self_context_section = (
        f"\nRESULTS ALREADY PRODUCED IN THIS CONVERSATION (agent syntheses / reports already "
        f"given to the analyst):\n{self_context}\n"
        if self_context
        else ""
    )
    ref_context_section = (
        f"\nCONTEXT FROM REFERENCED CONVERSATIONS (history provided by analyst):\n"
        f"{ref_context}\n"
        if ref_context
        else ""
    )
    # Already-available knowledge (memory / intra-conv results / @context): force a direct answer
    # BEFORE routing. Placed up top so it takes precedence over the "investigate" posture (else
    # the router spawns an agent by default).
    priority_block = (
        "ABSOLUTE PRIORITY — ANSWER FROM ALREADY-AVAILABLE KNOWLEDGE:\n"
        "Before anything else, examine the RELEVANT INTERNAL MEMORY, the RESULTS ALREADY PRODUCED "
        "IN THIS CONVERSATION and the CONTEXT FROM REFERENCED CONVERSATIONS provided below. If the "
        "answer to the request is ALREADY there (in whole or in substance), you MUST answer "
        "directly: intent=chitchat, the full answer in `reply`, NO agent in `agents`. NEVER launch "
        "an investigation for information that is already known or already provided. This rule "
        "TAKES PRECEDENCE over the orchestrator posture and the routing below.\n\n"
        if (memory_section or self_context_section or ref_context_section)
        else ""
    )
    # Staff-cell channel: steer the plan toward the cell's mandate (G1 = personnel,
    # G2 = intelligence…). Does not hard-restrict, but prioritises what serves the cell.
    channel_section = (
        "CONTEXT — STAFF CELL:\n"
        f"This request comes from a staff-cell channel whose mandate is: \"{channel_prompt}\". "
        "Steer the plan and the choice of agents toward what serves this mandate; information "
        "outside that scope is only relevant if it directly impacts the cell's mission.\n\n"
        if channel_prompt
        else ""
    )
    return (
        "You are the ROUTER of an intelligence agent orchestrator. You receive the "
        "analyst's request AND the recent history, and you decide how to handle it.\n\n"
        f"{priority_block}"
        f"{channel_section}"
        "POSTURE (essential): you ARE the orchestrator — YOU talk to the agents and trigger the "
        'tasks. NEVER write that you "cannot execute a request", that you "have no access" to '
        'an agent or to data, nor that the analyst should "use agent X" or "rephrase". Your job '
        "is to route. ACT.\n\n"
        "FOUR possible handlings:\n"
        "  - `investigate`: the request (alone or COMBINED with the context of previous turns) "
        "designates a SUBJECT TO ANALYZE (country, event, person, infrastructure, position, "
        "imagery…). Mobilize the relevant specialized agent(s).\n"
        "  - `data_ops`: the request concerns OUR data holdings — either QUERYING what the system "
        'holds ("what data / what collections do we have on X", listing or consulting datasets, '
        "data backend status), or an ACTION on the data (ingest / load / import / index / create / "
        "modify / delete a collection or a dataset). Mobilize the data-management agent. A solution "
        'name cited as a load TARGET ("into the OSINT solution") indicates WHERE to store, NOT an '
        "analysis request.\n"
        "  - `personalize`: the analyst wants to TEACH you something ABOUT THEMSELVES (role, "
        'preferences, constraints: "personalize yourself", "learn that I prefer X", "remember my '
        'role is Y") OR teach you a world fact to remember ("remember that Z", "note that…"). '
        "Then: (a) if the analyst already provided concrete elements, extract them into "
        "`proposed_memories` (kind=`about_user` for anything about the analyst, `learned_fact` "
        "for a world fact); (b) otherwise ask ONE targeted question in `reply` (role, preferred "
        "language, report format, specialty domains…). NO agent. The `proposed_memories` will be "
        "shown to the analyst via a confirmation widget — do NOT write anything to memory yourself.\n"
        "  - `chitchat`: the request is purely conversational (greeting, thanks, a question about "
        "yourself or how you work). Answer yourself, briefly, in `reply`. If the request is "
        "ACTIONABLE but too vague to route, choose `chitchat` and ask in `reply` ONE single short, "
        "concrete clarification question (never a refusal).\n\n"
        "RULES:\n"
        '  - Follow-up/complement of a previous turn (adding a geo area, an agent, "and in '
        'France?"): treat the WHOLE as a concrete request and route (investigate/data_ops).\n'
        '  - If the analyst explicitly names an available agent ("ask Search", "via IMINT", '
        '"with the geo agent"…), KEEP it in `agents` and infer the SUBJECT from the last relevant '
        "turn of the history. Do not reply that you cannot call it.\n"
        "  - For EACH selected agent, write an `instruction`: a short, ACTIONABLE directive specific "
        "to its discipline, extracting the salient elements (entities, places, period, objective) — "
        "if needed RETRIEVED FROM THE HISTORY. Do not copy the raw request; rephrase it as a focused "
        'mission (e.g. Search: "Search the internal knowledge bases for recent information on X in area Y; correlate the cited actors").\n'
        "  - LANGUAGE CHAINING: if an agent accepts only one language (e.g. `nlp-agent` is "
        "English-only) and the content to process — attachment OR analyst text — is in ANOTHER "
        'language, ADD an upstream `translator-agent` step (translator). NEVER write "please '
        'translate first" or "the service requires English" in the downstream agent\'s '
        "instruction: it is NOT a translator and will ignore the directive. Translation is a "
        "SEPARATE step that feeds the next agent via reference chaining.\n"
        "  - Write `reply` and every `instruction` IN ENGLISH.\n"
        "  - `relevant_memory_ids`: list of ids (integers) of the lines from AVAILABLE INTERNAL "
        "MEMORY you actually used to form your decision/reply — empty list if none is relevant. "
        "Do NOT include an id that does not appear in the AVAILABLE INTERNAL MEMORY section.\n"
        "  - `proposed_memories`: ONLY when intent=`personalize`. List of "
        '`{"kind":"about_user"|"learned_fact","content":"<one short sentence in the present>"}`. '
        "One sentence = one atomic fact (do NOT combine multiple facts). `about_user` for "
        "anything about the analyst (role, preferences, constraints, identity), `learned_fact` "
        "for a world fact. Empty list if nothing to memorize on this turn (e.g. you ask a "
        "question in `reply`).\n"
        "  - `widgets`: if the analyst asks to OPEN / SHOW / SPAWN a dashboard tool or widget "
        "(not to perform an analysis task), put the matching keys in `widgets`, with "
        "intent=`chitchat`, NO agent, and `reply` = a short confirmation (e.g. \"Opening the "
        "translation widget.\"). Do NOT invent keys: pick ONLY from the catalog below. Empty list "
        "if the request is not a widget-open request.\n"
        f"OPENABLE WIDGETS:\n{_widget_catalog_desc()}\n"
        f"\nAVAILABLE AGENTS:\n{catalog_desc}\n"
        f"{history_section}"
        f"{memory_section}"
        f"{self_context_section}"
        f"{ref_context_section}"
        + _build_attachments_section(attachments or [], _prompt_budget_chars, lang="en")
        + f"\nNEW ANALYST REQUEST: {text!r}\n\n"
        "Reply STRICTLY in valid JSON, no markdown or comment, with exactly this shape:\n"
        '{"intent":"investigate"|"data_ops"|"personalize"|"chitchat",'
        '"agents":[{"id":"<agent_id>","instruction":"<focused mission for this agent>"}, ...],'
        '"reply":"<reply text if chitchat/personalize, otherwise empty string>",'
        '"widgets":["<widget key to open>", ...],'
        '"relevant_memory_ids":[<int>, ...],'
        '"proposed_memories":[{"kind":"about_user"|"learned_fact","content":"<fact>"}, ...]}'
    )


def _ground_instructions(
    agents: list[str],
    instructions: dict[str, str],
    text: str,
    history: list[dict] | None,
    catalog: dict[str, dict],
    lang: str = "en",
) -> dict[str, str]:
    """Complète les instructions manquantes en ancrant le sujet sur l'historique.

    Si le message courant n'est qu'un ordre laconique (« demande à Search »), le
    sujet réel (« …sur l'Allemagne ») vient du dernier tour utilisateur porteur de
    sens. Les instructions déjà fournies par le LLM ne sont pas écrasées.
    """
    subject = text
    if _is_terse_directive(text):
        topic = _last_substantive_user_topic(history)
        if topic:
            subject = topic
    for aid in agents:
        if not (instructions.get(aid) or "").strip():
            instructions[aid] = default_instruction(subject, catalog.get(aid, {}), lang)
    return instructions


async def route_request(
    text: str,
    agent_catalog: dict[str, dict],
    history: list[dict] | None = None,
    lang: str = "en",
    on_thinking: Callable[[str, bool], Awaitable[None]] | None = None,
    memories: list[dict] | None = None,
    ref_context: str | None = None,
    self_context: str | None = None,
    attachments: list[dict] | None = None,
    max_tokens: int | None = None,
    channel_prompt: str | None = None,
    enable_thinking: bool | None = None,
) -> dict:
    """Décide via LLM (+ gardes déterministes) si la requête mérite une investigation.

    Retourne toujours `{"intent", "agents", "instructions", "reply", "tokens"}`, avec
    `intent ∈ {"investigate", "chitchat"}` (la FSM ne connaît que ces deux-là —
    `data_ops` est normalisé en `investigate` vers l'agent de gestion de données).
    `tokens` = usage total de l'appel LLM (prompt + completion), 0 si pas d'appel.

    Au-dessus du LLM, trois gardes indépendantes du modèle :
      1. mention explicite d'un agent → on force le routage vers lui ;
      2. interrogation de notre stock de données → agent de gestion de données ;
      3. ancrage de l'instruction sur l'historique pour les ordres laconiques.

    Si le LLM est HS / JSON invalide, repli sur l'heuristique `plan_investigation`
    AVANT d'appliquer les gardes (elles peuvent encore rattraper le routage).

    Si `on_thinking` est fourni, la planification se fait en **streaming** : les
    chunks `thinking` sont relayés en direct, seuls les chunks `token` composent
    le JSON final à parser. L'usage voyage dans le chunk `done`.
    """
    if not agent_catalog:
        return {
            "intent": "chitchat",
            "agents": [],
            "instructions": {},
            "reply": "",
            "widgets": [],
            "tokens": 0,
            "relevant_memory_ids": [],
            "proposed_memories": [],
            # Métadonnées de routage (P9) : aucun appel LLM ici, catalogue vide.
            "router_raw": "",
            "router_source": "fallback",
            "router_tokens": None,
            "prompt_hash": None,
        }

    # --- Garde 0 : pédagogie parlementaire du lexique → réponse déterministe ---
    # Correcte, neutre, sourcée, latence nulle, insensible aux pannes/429 du LLM.
    lex = _lexique_reply(text)
    if lex is not None:
        return {
            "intent": "chitchat",
            "agents": [],
            "instructions": {},
            "reply": lex,
            "widgets": [],
            "tokens": 0,
            "relevant_memory_ids": [],
            "proposed_memories": [],
            "router_raw": "",
            "router_source": "guard_lexique",
            "router_tokens": None,
            "prompt_hash": None,
        }

    prompt = _build_routing_prompt(
        text,
        agent_catalog,
        history=history,
        lang=lang,
        memories=memories,
        ref_context=ref_context,
        self_context=self_context,
        attachments=attachments,
        max_tokens=max_tokens,
        channel_prompt=channel_prompt,
    )
    # Métadonnées de routage (P9), exposées à la FSM pour répondre depuis la base à
    # « pourquoi ce plan ? LLM ou fallback ? quelle sortie brute ? ». `prompt_hash` =
    # empreinte stable (sha1 court) du prompt routeur. `router_source` est renseignée
    # par le chemin qui produit RÉELLEMENT le plan (llm / fallback / guard_*).
    prompt_hash = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
    router_source = "fallback"

    def _router_meta() -> dict:
        """Snapshot des 4 champs de routage à l'instant du `return` (lecture des locals)."""
        return {
            "router_raw": raw,
            "router_source": router_source,
            "router_tokens": tokens if tokens else None,
            "prompt_hash": prompt_hash,
        }

    raw = ""
    tokens = 0
    try:
        if on_thinking is not None:
            stream_usage: dict = {}
            async for chunk in complete_stream("planning", "high", prompt, lang=lang,
                                               enable_thinking=enable_thinking):
                ctype, ctext = chunk.get("type"), chunk.get("text", "")
                if ctype == "thinking" and ctext:
                    await on_thinking(ctext, False)
                elif ctype == "token":
                    raw += ctext
                elif ctype == "done":
                    # L'usage est injecté dans le chunk done par llm-stub.
                    stream_usage = chunk.get("usage") or {}
                    await on_thinking("", True)
            tokens = (stream_usage.get("prompt_tokens") or 0) + (
                stream_usage.get("completion_tokens") or 0
            )
        else:
            text_out, usage = await complete_usage(
                "planning", "high", prompt, lang=lang, enable_thinking=enable_thinking
            )
            raw = text_out
            tokens = (usage.get("prompt_tokens") or 0) + (
                usage.get("completion_tokens") or 0
            )
    except Exception as e:  # noqa: BLE001
        log.warning("router LLM injoignable (%s) — fallback heuristique", e)
        if on_thinking is not None:
            try:
                await on_thinking("", True)
            except Exception:
                pass

    data = _parse_router_json(raw) if raw else None

    # P5 : parse routeur en échec (retour vide OU JSON invalide) → UNE seule
    # re-demande SANS raisonnement avant le repli heuristique. Le <think> a pu
    # épuiser le cap avant le JSON (`content` vide) ou laisser un blob d'accolades
    # brouillon ; un appel no-think resserre la sortie sur le seul JSON de routage.
    # Une tentative unique, puis on tombe sur `plan_investigation` comme aujourd'hui.
    if data is None:
        try:
            retry_out, retry_usage = await complete_usage(
                "planning", "high", prompt, lang=lang, enable_thinking=False
            )
            tokens += (retry_usage.get("prompt_tokens") or 0) + (
                retry_usage.get("completion_tokens") or 0
            )
            retry_data = _parse_router_json(retry_out) if retry_out else None
            if retry_data is not None:
                raw, data = retry_out, retry_data
        except Exception as e:  # noqa: BLE001
            log.warning("router LLM : re-demande échouée (%s) — fallback heuristique", e)

    data_agent = _data_agent_id(agent_catalog)
    # Widgets à ouvrir, indépendants de l'intent (demande « ouvre le widget X »).
    widgets = _extract_widgets(data) if data else []

    # --- Décision LLM (ou repli heuristique) ---
    known_mem_ids = {m["id"] for m in (memories or []) if "id" in m}
    valid_intents = ("investigate", "data_ops", "personalize", "chitchat")
    if data and data.get("intent") in valid_intents:
        router_source = "llm"
        intent = data["intent"]
        agents, instructions = _extract_agents_instructions(data, agent_catalog)
        reply = str(data.get("reply") or "")
        relevant_memory_ids = _extract_relevant_memory_ids(data, known_mem_ids)
        proposed_memories = (
            _extract_proposed_memories(data) if intent == "personalize" else []
        )
        if intent == "data_ops":
            if data_agent and data_agent not in agents:
                agents.insert(0, data_agent)
            intent = "investigate"
    else:
        # Repli heuristique mots-clés : `router_source="fallback"`, `router_raw` = la
        # sortie brute reçue (vide ou JSON invalide), alimentés par `_router_meta()`.
        router_source = "fallback"
        if raw:
            log.warning(
                "router LLM : JSON invalide (extrait=%r) — fallback heuristique",
                raw[:160],
            )
        steps = plan_investigation(text, agent_catalog, lang)
        agents = [s["agent_type"] for s in steps]
        instructions = {}
        intent = "investigate" if agents else "chitchat"
        reply = ""
        relevant_memory_ids = []
        proposed_memories = []

    # --- Demande pure d'ouverture de widget : aucun agent, aucune investigation ---
    # On court-circuite AVANT les gardes de routage (qui pourraient, ex., mapper le
    # mot « traduction » sur l'agent traducteur) : ouvrir un widget ≠ lancer une tâche.
    if widgets and not agents:
        return {
            "intent": "chitchat",
            "agents": [],
            "instructions": {},
            "reply": reply,
            "widgets": widgets,
            "tokens": tokens,
            "relevant_memory_ids": relevant_memory_ids,
            "proposed_memories": proposed_memories,
            **_router_meta(),
        }

    # --- Garde 1 : mention explicite d'un agent → on force le routage vers lui ---
    for aid in _mentioned_agents(text, agent_catalog):
        if aid not in agents:
            agents.append(aid)
            router_source = "guard_mention"
    if agents and intent == "chitchat":
        intent, reply = "investigate", ""

    # --- Garde 2 : interrogation de notre stock de données → agent data ---
    if intent == "chitchat" and data_agent and _is_catalog_query(text):
        if data_agent not in agents:
            agents.insert(0, data_agent)
        intent, reply = "investigate", ""
        router_source = "guard_data"

    # --- Garde 3 : tournure parlementaire sans ambiguïté (« comment a voté… »,
    #     « qui est le député de… », « où en est la loi… ») → l'agent de LA
    #     discipline, EN EXCLUSIF. Court-circuite le routeur LLM qui, sur ces
    #     requêtes, tend à fan-out vers tous les agents (latence inutile). La
    #     demande BRUTE sert d'instruction : l'agent extrait lui-même député/
    #     scrutin/dossier de la question (meilleur rappel qu'une reformulation). ---
    guard_disc = _parl_guard_discipline(text)
    parl_agent = _agent_by_discipline(agent_catalog, guard_disc) if guard_disc else None
    if parl_agent:
        agents = [parl_agent]
        instructions = {parl_agent: text}
        intent, reply = "investigate", ""
        router_source = "guard_parlement"

    # Cohérence : investigate sans agent connu → chitchat avec question de clarification.
    if intent == "investigate" and not agents:
        clarify = (
            "Sur quel sujet précis veux-tu que je lance l'investigation "
            "(pays, événement, personne, infrastructure…) ?"
            if lang == "fr"
            else "What precise subject should I launch the investigation on "
            "(country, event, person, infrastructure…)?"
        )
        return {
            "intent": "chitchat",
            "agents": [],
            "instructions": {},
            "reply": reply or clarify,
            "widgets": widgets,
            "tokens": tokens,
            "relevant_memory_ids": relevant_memory_ids,
            "proposed_memories": proposed_memories,
            **_router_meta(),
        }

    if intent == "investigate":
        instructions = _ground_instructions(
            agents, instructions, text, history, agent_catalog, lang
        )

    return {
        "intent": intent,
        "agents": agents,
        "instructions": instructions,
        "reply": reply,
        "widgets": widgets,
        "tokens": tokens,
        "relevant_memory_ids": relevant_memory_ids,
        "proposed_memories": proposed_memories,
        **_router_meta(),
    }


def build_compaction_prompt(history: list[dict], lang: str = "en") -> str:
    """Compose le prompt de résumé de l'historique pour la compaction de contexte.

    `history` = [{role, text}, ...] (messages user/assistant).
    Le résumé demandé doit être dense et factuel, suffisant pour que les tours suivants
    aient du contexte sans le texte intégral des échanges passés.
    """
    lines = "\n".join(
        f"{'User' if m.get('role') == 'user' else 'Assistant'}: {(m.get('text') or '').strip()}"
        for m in history
        if (m.get("text") or "").strip()
    ) or ("(aucun message)" if lang == "fr" else "(no messages)")
    if lang == "fr":
        return (
            "Tu es l'archiviste d'un système de renseignement. Rédige un RÉSUMÉ dense et factuel "
            "de la conversation ci-dessous, en conservant les faits-clés, entités, décisions et "
            "contexte opérationnel. Le résumé remplacera l'historique complet dans les prochains "
            "tours : il doit être autonome. Maximum 300 mots. En français.\n\n"
            f"HISTORIQUE :\n{lines}"
        )
    return (
        "You are the archivist of an intelligence system. Write a dense, factual SUMMARY of the "
        "conversation below, preserving key facts, entities, decisions and operational context. "
        "The summary will replace the full history in the next turns: it must be self-contained. "
        "Maximum 300 words. In English.\n\n"
        f"HISTORY:\n{lines}"
    )
