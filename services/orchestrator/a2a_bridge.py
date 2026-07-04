"""Bridge A2A — consomme `agent.*.task` pour les agents A2A et appelle leur endpoint HTTP.

Pour les agents enregistrés avec `transport='a2a'`, il n'y a pas de worker
NATS interne : c'est l'orchestrateur (via ce bridge) qui agit comme client A2A.

Flux par message :
  1. Reçoit un TaskDispatch sur `agent.{id}.task` (queue group `a2a-bridge`).
  2. Vérifie le transport en base (skip si != 'a2a').
  3. Heartbeats `progress` en parallèle (lease renewal) le temps de l'appel.
  4. Appelle `A2AClient.send_and_stream(td.params['text'])` côté agent distant.
  5. Publie `Result` sur `agent.{id}.result.{corr_id}` (consommé par la FSM standard).

Le bridge tourne dans le process orchestrateur — un crash → la tâche expire
naturellement (lease/sweeper) puis est replanifiée par la FSM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

from nats.errors import TimeoutError as NatsTimeoutError

from hemicycle_shared import nats_io, s3_docs, subjects
from hemicycle_shared.a2a import A2AClient
from hemicycle_shared.artifact_store import get_artifact
from hemicycle_shared.config import settings
from hemicycle_shared.db import AgentType, Task
from hemicycle_shared.llm_client import complete
from hemicycle_shared.messages import Progress, Result, TaskDispatch

# Statuts terminaux d'une tâche : le bridge ne doit pas relancer un A2A pour
# une tâche déjà close (timeout, retry FSM, cancellation…). Aligné avec
# `fsm.TERMINAL`. On évite l'import circulaire en redéfinissant le set ici.
_TERMINAL_STATUSES = {"done", "failed", "timeout", "cancelled"}

# Limite de texte par pièce jointe repliée dans le prompt A2A. Plus généreux que
# les workers NATS (2000) : un agent A2A peut avoir l'INGESTION du document pour
# mission, où tronquer trop tôt vide la tâche de sa matière.
_ATTACH_PROMPT_CHARS = 8000

# Aperçu textuel d'une pièce jointe déjà poussée dans MinIO (s3_uri présent).
# Le document complet reste accessible via pull_doc_from_bucket(uri) ; cet aperçu
# donne juste assez de contexte pour que l'agent sache ce qu'il va récupérer.
_ATTACH_PREVIEW_CHARS = 800

# Budget caractères EXPLICITE pour la matière amont sérialisée dans le message A2A.
# Remplace le cap implicite ~4000 du preview (summary tronqué de `h_collect_result`) :
# le chaînage transporte désormais le payload RÉEL de l'amont, borné par ce budget
# généreux — et non plus retronqué à 4000 à chaque saut (cf. bug racine #2 : la sortie
# transformée n'atteignait pas l'aval). Global sur l'ensemble des blocs amont.
_UPSTREAM_BUDGET_CHARS = 24000

log = logging.getLogger("orchestrator.a2a_bridge")

# Registre des appels A2A en vol, indexé par corr_id → `stop` (asyncio.Event).
# Un cancel publié par l'orchestrateur (réorientation/annulation analyste) `set()` cet
# Event : le heartbeat s'arrête et la boucle de streaming sort à la prochaine itération
# (le cas « agent muet/hang » reste borné par le `asyncio.timeout` dur de `_handle`).
# La tâche étant déjà tombstonée en base, tout Result tardif serait de toute façon ignoré.
_INFLIGHT_A2A: dict[str, asyncio.Event] = {}


# Directive de langue préfixée au prompt A2A : un agent distant dont le system
# prompt n'impose pas de langue fixe la respectera. La langue de l'instruction
# (produite par le routeur dans la bonne langue) renforce le signal.
_LANG_DIRECTIVE = {
    "en": "Respond in English.",
    "fr": "Réponds en français.",
}

# Agents de TRANSFORMATION (traducteurs) : pour eux, la langue cible vient de la
# MISSION (`td.instruction`, p. ex. « traduire en anglais »), JAMAIS de la directive de
# langue UI — sinon « Réponds en français » écraserait la mission de traduction et la
# rendrait no-op (bug racine #1, confirmé EXEC 1 vs EXEC 3 du doc de tests).
# Garder aligné avec `fsm._TRANSLATOR_AGENTS`.
_TRANSFORM_AGENTS = {"translator-agent"}


def _payload_to_text(payload: dict) -> str:
    """Sérialise le payload structuré d'un artefact amont en texte exploitable par l'aval.

    Préfère un champ texte PRIMAIRE (traduction, résumé, réponse…) : c'est la matière
    propre que le consommateur doit traiter — pour un NER, la traduction elle-même, pas un
    blob JSON dont il extrairait les noms de clés comme entités. À défaut (producteur
    purement structuré : recherche, géo…), on sérialise le payload complet en JSON lisible
    plutôt que de perdre la matière. Retourne "" si rien d'exploitable.
    """
    if not isinstance(payload, dict) or not payload:
        return ""
    for k in ("text", "translation", "summary", "answer", "content"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


async def _resolve_a2a_upstream(js, td: TaskDispatch) -> list[str]:
    """Résout les sorties amont en blocs texte depuis l'Object Store (chaînage sans perte).

    Réplique `sdk._resolve_upstream` pour le bridge A2A : pour chaque `ArtifactRef` de
    `td.input_refs`, charge l'enveloppe via `get_artifact(js, ref.artifact_id)` et sérialise
    le PAYLOAD structuré RÉEL de l'amont — pas le `preview` tronqué à ~4000 chars qui
    transportait l'echo de l'instruction source vers l'aval (bug racine #2).

    Replis successifs (jamais d'exception : un Object Store indisponible ne doit pas casser
    la tâche) :
      1. payload structuré résolu → texte (`_payload_to_text`) ;
      2. `env.preview` si le payload est vide ;
      3. `ref.preview` (comportement historique) si l'artefact est absent/expiré ;
      4. à défaut de refs (dispatch legacy / retry sans refs), les previews déposés dans
         `params["upstream"]` par `fsm._dispatch_step`.

    L'ensemble est borné par `_UPSTREAM_BUDGET_CHARS` (budget global explicite, en
    remplacement du cap 4000 implicite du preview et du `[:_ATTACH_PROMPT_CHARS]` par bloc).
    """
    pairs: list[tuple[str, str]] = []  # (agent_type, texte amont)
    refs = list(td.input_refs or [])
    if refs:
        for ref in refs:
            text = ""
            try:
                env = await get_artifact(js, ref.artifact_id)
            except Exception:  # noqa: BLE001
                env = None
                log.warning(
                    "a2a-bridge: résolution artefact %s échouée → repli preview",
                    ref.artifact_id,
                    exc_info=True,
                )
            if env is not None:
                text = _payload_to_text(env.payload) or (env.preview or "")
            if not text.strip():
                text = ref.preview or ""  # repli ultime : preview de la ref
            if text.strip():
                pairs.append((ref.agent_type, text))
    else:
        for u in (td.params or {}).get("upstream") or []:
            txt = str(u.get("text") or "")
            if txt.strip():
                pairs.append((str(u.get("agent") or "?"), txt))

    # Budget GLOBAL explicite : on transporte le vrai contenu, borné, plutôt que chaque
    # saut retronqué à 4000.
    blocks: list[str] = []
    budget = _UPSTREAM_BUDGET_CHARS
    for agent, text in pairs[:5]:
        if budget <= 0:
            break
        chunk = text[:budget]
        budget -= len(chunk)
        blocks.append(f"[{agent}]\n{chunk}")

    # --- Survie des URIs s3 à travers la troncature amont ---
    # Si le budget a tronqué des passages qui contenaient des URIs de documents
    # (s3://hemicycle/...), on les ré-injecte afin que l'agent aval puisse récupérer
    # le document complet via pull_doc_from_bucket. Seules les URIs absentes du
    # texte tronqué sont ajoutées (idempotent : si déjà présentes, rien n'est émis).
    full_text = "\n".join(t for _, t in pairs[:5])
    all_uris = s3_docs.find_doc_uris(full_text)
    if all_uris:
        truncated_text = "\n".join(blocks)
        missing_uris = [u for u in all_uris if u not in truncated_text]
        if missing_uris:
            blocks.append("DOCUMENT URIS:\n" + "\n".join(missing_uris))

    return blocks


def _fmt_size(n: int | None) -> str:
    """Taille lisible en Ko/Mo pour l'affichage des métadonnées de pièce jointe."""
    if not n:
        return "?"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} Ko"
    return f"{n / (1024 * 1024):.1f} Mo"


async def _compose_a2a_text(td: TaskDispatch, js) -> str:
    """Compose le texte envoyé à l'agent A2A : [directive de langue] + matière à traiter + mission.

    Async : la résolution de la matière amont lit l'Object Store (`get_artifact`).

    Trois éléments structurants :
      1. Une directive de langue (`Respond in English.` / `Réponds en français.`) en tête,
         pour que l'agent distant produise sa sortie dans la langue de l'UI — SAUF pour les
         agents de TRANSFORMATION (`_TRANSFORM_AGENTS`, les traducteurs) : pour eux la langue
         cible vient de la MISSION (« traduire en anglais »), jamais de l'UI, sinon la
         directive écraserait la mission (bug racine #1).
      2. La MATIÈRE à traiter, qui dépend de la position de l'étape dans le plan :
         - **Étape racine** (aucune sortie amont) : le CONTENU fourni par l'analyste —
           pièces jointes (`params['attachments']`) et MATIÈRE brute (`params['text']`).
           Contrat amont (fsm.py) : à l'étape racine `params['text']` porte la MATIÈRE (texte
           analyste / attachments) et `td.instruction` la MISSION de discipline ; les deux
           sont transmis SÉPARÉMENT (jamais concaténés en un seul blob), sinon le traducteur
           traduirait la consigne d'orchestration et le NER aval extrairait « NER » comme
           organisation (bug #2b).
         - **Étape aval** (au moins une sortie amont) : UNIQUEMENT la sortie des étapes
           précédentes — résolue depuis l'Object Store en payload RÉEL (`_resolve_a2a_upstream`),
           plus le `preview` tronqué. L'entrée originale a déjà été consommée/transformée par
           l'amont ; la re-présenter contaminerait l'agent avec l'instruction de l'analyste et
           la langue source. C'est la matière de l'amont qui fait foi.
      3. La MISSION : `td.instruction` (consigne ciblée par discipline produite par le
         routeur), à défaut le texte brut. C'est elle qui porte l'action.

    Le texte brut n'est répété en CONTENU que s'il diffère de la mission, pour ne pas
    dupliquer une instruction laconique qui n'était déjà que le message utilisateur.
    """
    params = td.params or {}
    fr = td.lang == "fr"
    # Agents de transformation : pas de directive de langue UI — la cible vient de la mission.
    is_transform = td.agent_type in _TRANSFORM_AGENTS
    directive = (
        "" if is_transform else _LANG_DIRECTIVE.get(td.lang, _LANG_DIRECTIVE["en"])
    )
    user_text = str(params.get("text") or "").strip()
    mission = (td.instruction or "").strip() or user_text
    atts = params.get("attachments") or []

    content_label = (
        "CONTENU FOURNI PAR L'ANALYSTE :" if fr else "CONTENT PROVIDED BY THE ANALYST:"
    )
    doc_label = "Document joint" if fr else "Attachment"
    mission_label = "MISSION :" if fr else "MISSION:"
    up_label = (
        "RÉSULTATS DES ÉTAPES PRÉCÉDENTES (matière à traiter) :"
        if fr
        else "RESULTS FROM PREVIOUS STEPS (material to process):"
    )

    # Bloc de chaînage : sorties des agents amont résolues depuis l'Object Store (payload
    # RÉEL, plus le preview tronqué). Quand il y en a, C'EST la matière de cette étape —
    # l'entrée originale (texte analyste + pièces jointes) a déjà été consommée par l'amont,
    # la re-présenter contaminerait un agent de transformation.
    up_blocks = await _resolve_a2a_upstream(js, td)

    body_parts: list[str] = []
    if up_blocks:
        # Étape aval : la matière est la sortie amont, pas l'entrée originale.
        body_parts.append(f"{up_label}\n" + "\n\n".join(up_blocks))
    else:
        # Étape racine : on fournit le contenu analyste (pièces jointes + matière brute),
        # SÉPARÉ de la mission (`td.instruction`) — jamais concaténés (cf. bug #2b).
        blocks = []
        for a in atts[:5]:
            s3_uri = (a.get("s3_uri") or "").strip()
            fname = a.get("filename") or "?"
            if s3_uri:
                # Document poussé dans MinIO : on transmet l'URI courte + un aperçu.
                # L'agent récupère le document complet via pull_doc_from_bucket(uri).
                ctype = a.get("content_type") or "?"
                sz = _fmt_size(a.get("size_bytes"))
                apercu = (a.get("text") or "")[:_ATTACH_PREVIEW_CHARS]
                apercu_label = "APERÇU :" if fr else "PREVIEW:"
                fetch_note = (
                    "(Document complet disponible via pull_doc_from_bucket(uri).)"
                    if fr
                    else "(Full document available via pull_doc_from_bucket(uri).)"
                )
                blocks.append(
                    f"[{doc_label} : {fname}]\n"
                    f"DOCUMENT URI: {s3_uri}  (type: {ctype}, {sz})\n"
                    f"{apercu_label}\n{apercu}\n{fetch_note}"
                )
            else:
                # Comportement historique : texte inline tronqué (s3_uri absent/vide).
                blocks.append(
                    f"[{doc_label} : {fname}]\n{(a.get('text') or '')[:_ATTACH_PROMPT_CHARS]}"
                )
        if user_text and user_text != mission:
            blocks.append(user_text[:_ATTACH_PROMPT_CHARS])
        if blocks:
            body_parts.append(f"{content_label}\n" + "\n\n".join(blocks))

    # Détections géolocalisées fournies par un capteur (alerte IMINT) : positions FIABLES,
    # injectées en lignes parsables « 📍 <nom> — type=…, lat=…, lon=… » pour que l'agent C2
    # crée ses artefacts (observations) directement, sans réclamer de placement carte à
    # l'analyste (cf. règle « DÉTECTIONS GÉOLOCALISÉES FOURNIES » du c2_handler). Le format
    # 📍 …lat=…,lon=… satisfait déjà la règle de position de l'agent C2.
    locs = params.get("locations") or []
    det_lines: list[str] = []
    for loc in locs[:200]:
        try:
            lat = float(loc.get("lat"))
            lon = float(loc.get("lon"))
        except (TypeError, ValueError):
            continue
        name = str(loc.get("name") or "?")
        typ = str(loc.get("type") or "").strip()
        note = str(loc.get("note") or "").strip()
        line = f"📍 {name} — type={typ or '?'}, lat={lat:.7f}, lon={lon:.7f}"
        det_lines.append(f"{line} ({note})" if note else line)
    if det_lines:
        det_label = (
            "DÉTECTIONS GÉOLOCALISÉES (fournies par capteur — positions fiables) :"
            if fr
            else "GEOLOCATED DETECTIONS (sensor-provided — reliable positions):"
        )
        body_parts.append(f"{det_label}\n" + "\n".join(det_lines))

    head = f"{directive}\n\n" if directive else ""
    if not body_parts:
        return f"{head}{mission}"
    return head + "\n\n".join(body_parts) + f"\n\n{mission_label}\n{mission}"


def _extract_a2a_text(data: dict) -> str:
    """Extrait le texte lisible d'un snapshot Task A2A (spec v1.0 + wrapper scaffolded).

    Cherche, dans l'ordre :
      1. `output.text` — champ ajouté par le wrapper généré par mcp-hemicycle ;
      2. parts d'`artifacts` (`kind="text"` → texte direct, `kind="data"` → JSON-stringifié) ;
      3. parts du `status.message` — message d'état final côté serveur.
    Retourne "" si rien d'utile.
    """
    if not isinstance(data, dict):
        return ""
    out = data.get("output")
    if isinstance(out, dict) and out.get("text"):
        return str(out["text"])
    chunks: list[str] = []
    for art in data.get("artifacts") or []:
        for part in (art or {}).get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("kind") == "text" and part.get("text"):
                chunks.append(str(part["text"]))
            elif part.get("kind") == "data" and part.get("data") is not None:
                chunks.append(json.dumps(part["data"], ensure_ascii=False))
    if chunks:
        return "\n".join(chunks)
    status_msg = (data.get("status") or {}).get("message") or {}
    for part in status_msg.get("parts") or []:
        if isinstance(part, dict) and part.get("kind") == "text" and part.get("text"):
            return str(part["text"])
    return ""


# Champs structurés qu'un agent A2A peut exposer en plus du texte, et que
# Hémicycle sait rendre : `queries` (recherches brutes → panneau requêtes UI),
# `locations` (points géo → carte du dashboard). Génériques : tout agent qui
# remplit ces clés dans son `output` en bénéficie (mi_search aujourd'hui).
_FORWARDED_OUTPUT_KEYS = ("queries", "locations", "entities", "needs_form", "needs_fire_form", "tile_layers", "imagery_catalog", "aoi_bbox")


def _extract_a2a_input_required(data: dict) -> dict | None:
    """Si le snapshot Task A2A est en état `input-required`, remonte la question + contextId.

    Retourne `{"question": <texte>, "context_id": <contextId>}` quand l'agent
    distant demande un complément à l'analyste (spec A2A v1.0), sinon `None`.
    Le contextId permet de reprendre la MÊME tâche A2A à la réponse de l'analyste.
    """
    if not isinstance(data, dict):
        return None
    state = ((data.get("status") or {}).get("state") or "").strip()
    if state != "input-required":
        return None
    question = _extract_a2a_text(data) or "L'agent demande une précision."
    out: dict = {"question": question, "context_id": data.get("contextId") or ""}
    # `geo_request` (ex. agent C2) : signale qu'une POSITION est attendue → la FSM
    # spawn la carte en mode placement (cf. h_collect_result).
    geo = (data.get("output") or {}).get("geo_request")
    if isinstance(geo, dict):
        out["geo_request"] = geo
    return out


# Clés couramment utilisées par les agents A2A pour décrire un tool call dans un
# part `data`. Volontairement souple : chaque agent a sa propre convention.
_TOOL_NAME_KEYS = ("tool", "tool_name", "name", "function", "function_name")
_TOOL_ARGS_KEYS = ("params", "arguments", "args", "input", "parameters")


def _extract_tool_calls(data: dict) -> list[dict]:
    """Cherche dans `data` les parts qui ressemblent à un appel d'outil A2A.

    Inspecte les `parts` des artifacts ET du `status.message` à la recherche de
    parts `kind=data` dont le payload nomme un outil (clé `tool`/`name`/...) et
    éventuellement ses arguments. Retourne une liste `[{tool, params}, ...]` —
    vide si rien d'exploitable. Tolérant aux variations d'implémentations.
    """
    found: list[dict] = []
    if not isinstance(data, dict):
        return found
    sources: list[dict] = []
    for art in data.get("artifacts") or []:
        for part in (art or {}).get("parts") or []:
            if isinstance(part, dict):
                sources.append(part)
    status_msg = (data.get("status") or {}).get("message") or {}
    for part in status_msg.get("parts") or []:
        if isinstance(part, dict):
            sources.append(part)
    for part in sources:
        if part.get("kind") != "data":
            continue
        payload = part.get("data")
        if not isinstance(payload, dict):
            continue
        tool = next((str(payload[k]) for k in _TOOL_NAME_KEYS if payload.get(k)), None)
        if not tool:
            continue
        params = next(
            (payload[k] for k in _TOOL_ARGS_KEYS if payload.get(k) is not None), None
        )
        found.append(
            {"tool": tool, "params": params if isinstance(params, dict) else {}}
        )
    return found


def _extract_status_text(data: dict) -> str:
    """Texte « lisible humain » d'un event intermédiaire A2A.

    Distinct de `_extract_a2a_text` : on ne lit QUE le `status.message` (l'état
    transitoire émis par l'agent au fil de l'exécution) — pas les artifacts qui
    contiennent souvent la sortie finale. Sert à streamer la « réflexion » d'un
    agent A2A vers l'UI live, indépendamment de l'artefact final.
    """
    if not isinstance(data, dict):
        return ""
    status_msg = (data.get("status") or {}).get("message") or {}
    parts = status_msg.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("kind") == "text" and part.get("text"):
            chunks.append(str(part["text"]))
    # `rstrip` seulement : on PRÉSERVE le whitespace de tête. Le `strip()` complet
    # avalait l'espace/retour-ligne initial du segment → en delta-suffixe, les mots
    # se collaient en début de phrase (« caractères manquants » perçus dans l'Output
    # quand l'agent A2A ne stream pas de réponse dédiée et retombe sur ce canal).
    return "\n".join(chunks).rstrip()


def _extract_answer_delta(data: dict) -> str | None:
    """Delta du TEXTE FINAL de l'agent, streamé token-par-token (marqueur `hemicycle_stream=answer`).

    Distinct de `_extract_status_text` (status « humain » → buffer thinking) : ces
    deltas portent la RÉPONSE en cours de rédaction. Le wrapper A2A les émet en
    `status-update` avec `status.message.metadata.hemicycle_stream == "answer"` et le
    delta dans une part `text`. Le bridge les route vers le buffer `draft-{corr_id}`
    (bloc « Output » de l'AgentCard) — exactement comme un sous-agent NATS local.
    Retourne `None` si l'event n'est PAS un delta de réponse (≠ "" : un delta vide
    reste un delta, à ne pas confondre avec « pas un delta »).
    """
    if not isinstance(data, dict):
        return None
    status_msg = (data.get("status") or {}).get("message") or {}
    if not isinstance(status_msg, dict):
        return None
    meta = status_msg.get("metadata")
    if not (isinstance(meta, dict) and meta.get("hemicycle_stream") == "answer"):
        return None
    parts = status_msg.get("parts") or []
    return "".join(
        str(p["text"])
        for p in parts
        if isinstance(p, dict) and p.get("kind") == "text" and p.get("text")
    )


def _extract_a2a_struct(data: dict) -> dict:
    """Remonte les champs structurés exploitables depuis l'`output` d'un snapshot A2A.

    Ne fait AUCune supposition sur l'agent : on prend ce qui est présent parmi
    `_FORWARDED_OUTPUT_KEYS` et qui n'est pas vide. Le reste (texte) passe par
    `_extract_a2a_text`.
    """
    out = data.get("output") if isinstance(data, dict) else None
    if not isinstance(out, dict):
        return {}
    return {k: out[k] for k in _FORWARDED_OUTPUT_KEYS if out.get(k)}


# --- Géocodage des lieux extraits (NER & co.) -----------------------------------
# Les agents d'extraction d'entités (NER) renvoient des *noms* de lieux, sans
# coordonnées. Or le front ne place sur la carte que les points porteurs de
# `lat`/`lon` numériques (cf. GeoMap.extractLocations) : les noms seuls sont
# comptés mais jamais dessinés (« N points placés » sans aucun marqueur). On
# géocode donc ces noms via le LLM (raisonnement activé), en une seule passe.
def _has_coords(loc) -> bool:
    return (
        isinstance(loc, dict)
        and isinstance(loc.get("lat"), (int, float))
        and not isinstance(loc.get("lat"), bool)
        and isinstance(loc.get("lon"), (int, float))
        and not isinstance(loc.get("lon"), bool)
    )


def _geocode_prompt(names: list[str], lang: str) -> str:
    """Prompt de géocodage : noms de lieux → coordonnées, sortie JSON stricte.

    Volontairement archi-directif : pas de recherche internet, pas de texte autour,
    un exemple concret du format EXACT attendu — pour que le LLM renvoie un tableau
    JSON directement parsable par `_parse_geocode_json`.
    """
    listing = "\n".join(f"- {n}" for n in names)
    if lang == "fr":
        return (
            "Géocode chaque lieu ci-dessous vers ses coordonnées WGS84 en utilisant "
            "UNIQUEMENT tes connaissances internes. NE cherche PAS sur internet. "
            "N'explique RIEN. N'ajoute AUCUN texte avant ou après.\n\n"
            "Réponds par EXACTEMENT un tableau JSON et rien d'autre. Chaque élément "
            'doit avoir exactement ces clés : "name" (le nom du lieu EXACTEMENT tel '
            'que reçu), "lat" (nombre), "lon" (nombre).\n\n'
            'Exemple — pour les lieux "Paris" et "Berlin", réponds EXACTEMENT :\n'
            '[{"name": "Paris", "lat": 48.8566, "lon": 2.3522}, '
            '{"name": "Berlin", "lat": 52.52, "lon": 13.405}]\n\n'
            "Si tu ne connais pas un lieu, omets-le du tableau.\n\n"
            f"Lieux à géocoder :\n{listing}"
        )
    return (
        "Geocode each place below to its WGS84 coordinates using ONLY your own "
        "knowledge. Do NOT search the internet. Do NOT explain anything. Do NOT add "
        "any text before or after.\n\n"
        "Respond with EXACTLY one JSON array and nothing else. Each element must have "
        'exactly these keys: "name" (the place name EXACTLY as received), "lat" '
        '(number), "lon" (number).\n\n'
        'Example — for the places "Paris" and "Berlin", respond EXACTLY:\n'
        '[{"name": "Paris", "lat": 48.8566, "lon": 2.3522}, '
        '{"name": "Berlin", "lat": 52.52, "lon": 13.405}]\n\n'
        "If you do not know a place, omit it from the array.\n\n"
        f"Places to geocode:\n{listing}"
    )


def _parse_geocode_json(raw: str) -> dict[str, tuple[float, float]]:
    """Parse la réponse du LLM → {nom_normalisé: (lat, lon)}.

    Tolère un raisonnement <think>…</think> en tête et du texte autour du JSON :
    on isole le premier tableau et on ignore toute entrée sans lat/lon numériques.
    """
    if not raw:
        return {}
    s = raw.split("</think>")[-1]  # écarte le bloc de raisonnement éventuel
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end <= start:
        return {}
    try:
        arr = json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    out: dict[str, tuple[float, float]] = {}
    for item in arr if isinstance(arr, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        lat, lon = item.get("lat"), item.get("lon")
        if (
            name
            and isinstance(lat, (int, float))
            and not isinstance(lat, bool)
            and isinstance(lon, (int, float))
            and not isinstance(lon, bool)
        ):
            out[name.casefold()] = (float(lat), float(lon))
    return out


async def _geocode_locations(locations: list, lang: str) -> list:
    """Complète les `locations` sans coordonnées en géocodant leur nom via le LLM.

    Tolérant par construction : les points déjà géocodés (GEOINT, GeoJSON…) sont
    laissés tels quels ; en cas d'échec LLM/parse, on renvoie la liste inchangée
    (jamais d'exception — le bridge ne doit pas casser pour un géocodage manqué).
    """
    if not isinstance(locations, list) or not locations:
        return locations
    missing = [
        (i, str(loc.get("name") or "").strip())
        for i, loc in enumerate(locations)
        if isinstance(loc, dict)
        and not _has_coords(loc)
        and str(loc.get("name") or "").strip()
    ]
    if not missing:
        return locations
    try:
        # thinking OFF explicitement : un simple lookup nom→coordonnées n'exige pas
        # de raisonnement, et le <think> ajoute trop de latence (et épuise le budget
        # de tokens). On force le no-think quel que soit le défaut global du pool.
        raw = await complete(
            "subagent-reasoning",
            "low",
            _geocode_prompt([n for _, n in missing], lang),
            lang=lang,
            enable_thinking=False,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "a2a-bridge: géocodage LLM échoué (%s) — %d lieu(x) non placé(s)",
            e,
            len(missing),
        )
        return locations
    coords = _parse_geocode_json(raw)
    if not coords:
        log.info(
            "a2a-bridge: géocodage LLM sans coordonnées exploitables (%d lieu(x))",
            len(missing),
        )
        return locations
    enriched = list(locations)
    placed = 0
    for i, name in missing:
        hit = coords.get(name.casefold())
        if hit:
            enriched[i] = {**locations[i], "lat": hit[0], "lon": hit[1]}
            placed += 1
    log.info("a2a-bridge: géocodage LLM → %d/%d lieu(x) placé(s)", placed, len(missing))
    return enriched


# --- Extraction GeoJSON générique (emprises d'imagerie, GAOI, footprints…) -------
# Certains agents A2A (CSD) ne remplissent pas `output.locations` : ils renvoient
# du GeoJSON (FeatureCollection), parfois enfoui dans une enveloppe MCP où le
# document est une *string* JSON (`result.content[].text`). On le déterre et on le
# traduit dans le langage que le front sait déjà rendre : `locations` (points) +
# `polygons` (emprises, anneaux en [lat, lon] prêts pour Leaflet). Générique :
# tout agent renvoyant du GeoJSON en bénéficie, sans contrat dédié.
_GEOJSON_GEOM_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}


def _iter_geojson_features(obj, depth: int = 0):
    """Parcourt récursivement `obj` et yield les Features GeoJSON rencontrées.

    Traverse dicts/listes et parse les *strings* qui contiennent du GeoJSON
    (enveloppe MCP `content[].text`). Profondeur bornée pour ne pas boucler sur
    une structure pathologique.
    """
    if depth > 10:
        return
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "{[" and (
            "FeatureCollection" in s or '"coordinates"' in s or '"geometry"' in s
        ):
            try:
                parsed = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                return
            yield from _iter_geojson_features(parsed, depth + 1)
        return
    if isinstance(obj, dict):
        t = obj.get("type")
        if not isinstance(t, str):
            for v in obj.values():
                yield from _iter_geojson_features(v, depth + 1)
            return
        if t == "FeatureCollection":
            for f in obj.get("features") or []:
                yield from _iter_geojson_features(f, depth + 1)
            return
        if t == "Feature":
            yield obj
            return
        if t in _GEOJSON_GEOM_TYPES:
            yield {"type": "Feature", "geometry": obj, "properties": {}}
            return
        for v in obj.values():
            yield from _iter_geojson_features(v, depth + 1)
        return
    if isinstance(obj, list):
        for v in obj:
            yield from _iter_geojson_features(v, depth + 1)


def _prop(props: dict, *suffixes: str) -> str:
    """Première valeur de prop dont la clé se termine par l'un des suffixes (casse ignorée).

    Les propriétés NSIL sont des clés pointées (`NSIL_FILE.title`) — on matche par
    suffixe pour rester agnostique au préfixe exact.
    """
    if not isinstance(props, dict):
        return ""
    for suf in (s.lower() for s in suffixes):
        for k, v in props.items():
            kl = k.lower()
            if (kl == suf or kl.endswith("." + suf) or kl.endswith(suf)) and v not in (
                None,
                "",
            ):
                return str(v)
    return ""


def _feature_label(props: dict) -> tuple[str, str]:
    """Nom + note lisibles d'une Feature (titre, classification, source)."""
    name = _prop(props, "title", "name", "identifier") or "Zone"
    cls = _prop(props, "security.classification", "classification")
    src = _prop(props, "common.source", "sourcelibrary", "source")
    note = " · ".join(p for p in (cls, src) if p)
    return name[:120], note[:200]


def _to_latlon_ring(ring) -> list[list[float]]:
    """Convertit un anneau GeoJSON ([lon,lat]…) en [lat,lon] (ordre Leaflet)."""
    out: list[list[float]] = []
    for pt in ring or []:
        if (
            isinstance(pt, (list, tuple))
            and len(pt) >= 2
            and isinstance(pt[0], (int, float))
            and isinstance(pt[1], (int, float))
        ):
            out.append([float(pt[1]), float(pt[0])])
    return out if len(out) >= 3 else []


def _bbox_rings(props: dict) -> list[list[list[float]]]:
    """Rectangle [lat,lon] depuis une bbox `spatialGeographicReferenceBox`, sinon []."""
    if not isinstance(props, dict):
        return []
    box = next(
        (
            v
            for v in props.values()
            if isinstance(v, dict)
            and {"latmin", "lonmin", "latmax", "lonmax"} <= set(v)
        ),
        None,
    )
    if not box:
        return []
    try:
        a, b = float(box["latmin"]), float(box["lonmin"])
        c, d = float(box["latmax"]), float(box["lonmax"])
    except (TypeError, ValueError):
        return []
    return [[[a, b], [a, d], [c, d], [c, b], [a, b]]]


def _extract_geojson(data) -> dict:
    """Normalise tout GeoJSON présent dans `data` en `{locations, polygons}`.

    - Polygon/MultiPolygon → `polygons` (emprises, anneaux en [lat,lon]).
    - Point → `locations` (mêmes champs que mi_search).
    - Feature sans géométrie exploitable mais avec bbox → rectangle d'emprise.
    Ne renvoie que les clés non vides.
    """
    polygons: list[dict] = []
    points: list[dict] = []
    for feat in _iter_geojson_features(data):
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        name, note = _feature_label(props)
        geom = feat.get("geometry") if isinstance(feat.get("geometry"), dict) else {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if gtype == "Polygon":
            rings = [r for r in (_to_latlon_ring(r) for r in coords or []) if r]
            if rings:
                polygons.append({"name": name, "note": note, "rings": rings})
        elif gtype == "MultiPolygon":
            for poly in coords or []:
                rings = [r for r in (_to_latlon_ring(r) for r in poly or []) if r]
                if rings:
                    polygons.append({"name": name, "note": note, "rings": rings})
        elif (
            gtype == "Point" and isinstance(coords, (list, tuple)) and len(coords) >= 2
        ):
            try:
                points.append(
                    {
                        "lat": float(coords[1]),
                        "lon": float(coords[0]),
                        "name": name,
                        "note": note,
                    }
                )
            except (TypeError, ValueError):
                pass
        else:
            box = _bbox_rings(props)
            if box:
                polygons.append({"name": name, "note": note, "rings": box})
        if len(polygons) >= 100 or len(points) >= 500:
            break
    out: dict = {}
    if polygons:
        out["polygons"] = polygons[:100]
    if points:
        out["locations"] = points[:500]
    return out


def _looks_like_raw_json(text: str) -> bool:
    """Le « résumé » n'est qu'un blob JSON brut (pas une vraie réponse) ?"""
    s = (text or "").strip()
    return s[:1] in "{[" and (
        '"jsonrpc"' in s
        or "FeatureCollection" in s
        or '"coordinates"' in s
        or '"content"' in s
    )


def _geo_summary(geo: dict, lang: str) -> str:
    """Résumé lisible quand un agent ne renvoie que du GeoJSON brut."""
    n_poly = len(geo.get("polygons") or [])
    n_pt = len(geo.get("locations") or [])
    names = [p.get("name") for p in (geo.get("polygons") or []) if p.get("name")][:5]
    if lang == "fr":
        bits = ([f"{n_poly} emprise(s)"] if n_poly else []) + (
            [f"{n_pt} point(s)"] if n_pt else []
        )
        head = " et ".join(bits) or "des données géographiques"
        tail = f" : {', '.join(names)}" if names else ""
        return f"{head} tracée(s) sur la carte{tail}."
    bits = ([f"{n_poly} footprint(s)"] if n_poly else []) + (
        [f"{n_pt} point(s)"] if n_pt else []
    )
    head = " and ".join(bits) or "geographic data"
    tail = f": {', '.join(names)}" if names else ""
    return f"{head} drawn on the map{tail}."


async def _handle(msg, js, sm) -> None:
    """Traite un TaskDispatch en appelant l'agent A2A distant."""
    try:
        td = TaskDispatch.from_bytes(msg.data)
    except Exception as e:  # noqa: BLE001
        log.warning("a2a-bridge: message illisible (%s) → term", e)
        await msg.term()
        return

    # Lookup transport en base — skip si l'agent n'est PAS a2a (queue group partagée
    # avec d'éventuels workers NATS internes). On profite de la même session
    # courte pour vérifier que la Task n'est pas déjà close : un message
    # redélivré (FSM passée en FAILED, sweeper, max_deliver) ne doit PAS
    # déclencher un nouvel appel A2A — il sature le bridge et fait timeouter
    # les vraies tâches en attente.
    async with sm() as s:
        agent = await s.get(AgentType, td.agent_type)
        task = await s.get(Task, td.corr_id)
    if agent is None:
        log.warning("a2a-bridge: agent_type inconnu %s → term", td.agent_type)
        await msg.term()
        return
    if agent.transport != "a2a":
        # Le tag NATS standard veut un worker — pas notre boulot.
        await msg.nak(delay=0.2)
        return
    if not agent.card_url:
        log.error("a2a-bridge: agent %s sans card_url → fail", td.agent_type)
        await _publish_failure(js, td, "agent A2A sans card_url enregistrée")
        await msg.term()
        return
    if task is not None and task.status in _TERMINAL_STATUSES:
        log.info(
            "a2a-bridge: tâche %s déjà %s → ack sans appel A2A", td.corr_id, task.status
        )
        await msg.ack()
        return

    base_url = agent.card_url.rsplit("/.well-known/agent-card.json", 1)[0]
    text = await _compose_a2a_text(td, js)
    # contextId A2A : présent quand ce dispatch est la RÉPONSE de l'analyste à une
    # demande `input-required` — il reprend la même tâche côté agent distant.
    context_id = (td.params or {}).get("context_id") or None
    n_atts = len((td.params or {}).get("attachments") or [])
    log.info(
        "a2a-bridge: dispatch %s → %s (pièces=%d, ctx=%s)",
        td.corr_id,
        base_url,
        n_atts,
        context_id or "-",
    )

    # Étape « dispatch » immédiate côté UI : sort de l'écran « initializing… »
    # avant même le premier event SSE de l'agent distant. Annonce ce qui se passe.
    _agent_label = td.agent_type

    # Heartbeat parallèle : prolonge le lease orchestrateur (via Progress NATS)
    # ET le ack_wait du message (via msg.in_progress) pendant tout l'appel A2A.
    stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat(js, td, stop, msg))
    # Enregistre l'appel en vol : la souscription cancel pourra `stop.set()` ce corr_id.
    _INFLIGHT_A2A[td.corr_id] = stop

    hard_timeout = float(agent.hard_timeout_s or 120)
    client = A2AClient(base_url, timeout_s=hard_timeout)
    output_text = ""
    output_struct: dict = {}
    input_required: dict | None = None
    result_data: dict = {}
    events_collected: list[dict] = []
    worker_id = f"a2a-bridge-{settings.instance_id}"
    # État de streaming : on dédoublonne les tool_calls relayés et on émet en delta
    # le texte de status (un agent réémet souvent le message complet à chaque update).
    seen_tool_keys: set[str] = set()
    last_status_text = ""
    think_stream_id = f"a2a-{td.corr_id}"
    # Buffer « Output » de l'AgentCard : la réponse finale s'y écrit token-par-token
    # (kind=draft_chunk), comme un sous-agent NATS local. Alimenté par les events
    # answer-stream du wrapper A2A ; le widget recule sur le résultat canonique au `done`.
    answer_stream_id = f"draft-{td.corr_id}"
    answer_streamed = False

    async def _relay_progress(kind: str, trace_data: dict) -> None:
        """Pousse un Progress riche (thinking/tool_call/tool_result) sur le bus.

        Best-effort : si la publication échoue, on log et on continue le streaming —
        un trace perdu ne doit JAMAIS casser la tâche elle-même.

        draft_chunk (N1) : court-circuite le stream JetStream PROGRESS et publie
        directement en core NATS éphémère sur `orchestrator.progress.*`. Ces deltas
        sont purement live — aucun replay, aucune persistance DB (cf. _EPHEMERAL_TRACE_KINDS
        dans consumers.py) ; le consumer orch-lease les aurait de toute façon relayés
        via nc.publish vers ce même subject sans jamais les stocker. Leur présence dans
        PROGRESS représentait ~70 % du volume de rétention (un message durable par token
        d'agent) et constituait la cause principale du gel certain du serveur NATS.
        js._nc : connexion NATS sous-jacente, usage établi dans ce module (cf. cancel_sub).
        """
        try:
            prog = Progress(
                corr_id=td.corr_id,
                conv_id=td.conv_id,
                agent_id=td.agent_type,
                worker_id=worker_id,
                kind=kind,
                trace_data=trace_data,
            )
            if kind == "draft_chunk":
                # Éphémère pur : delta live token-par-token, aucun replay, pas de
                # persistance en execution_trace. Court-circuite PROGRESS (JetStream
                # durable) → publie directement en core NATS sur le subject que
                # orch-lease aurait choisi comme destination finale (idempotent).
                # Élimine le principal vecteur de volume de PROGRESS (cf. N1).
                await js._nc.publish(
                    subjects.orch_progress(td.conv_id), prog.to_bytes()
                )
            else:
                await js.publish(
                    subjects.agent_progress(td.agent_type, td.corr_id), prog.to_bytes()
                )
        except Exception:  # noqa: BLE001
            log.debug(
                "a2a-bridge: relai progress %s échoué (corr=%s)",
                kind,
                td.corr_id,
                exc_info=True,
            )

    # Trace « connexion » : l'UI sort d'« initializing… » dès qu'on bascule sur l'agent.
    await _relay_progress(
        "thinking",
        {"text": f"Connected to {_agent_label} — waiting for the first step…"},
    )

    # Dédup des artefacts texte déjà annoncés (un agent peut réémettre le même).
    seen_artifact_keys: set[str] = set()

    try:
        # Plafond DUR wall-clock sur tout l'appel : un agent qui stream des events
        # sans jamais émettre de `result` (ou qui hang) renouvelle sinon le lease via
        # le heartbeat → la tâche reste `running` à vie (le sweeper de timeout ne la
        # réclame jamais). asyncio.timeout borne l'ensemble du streaming.
        async with asyncio.timeout(hard_timeout):
            async for evt in client.send_and_stream(
                text, context_id=context_id, lang=td.lang
            ):
                # Annulation coopérative : un cancel a `set()` le stop → on arrête le
                # streaming proprement (le post-traitement détecte l'annulation plus bas).
                if stop.is_set():
                    log.info("a2a-bridge: cancel reçu pour %s → arrêt du stream", td.corr_id)
                    break
                events_collected.append(evt)
                evt_name = evt.get("event") or ""
                data = evt.get("data") or {}

                # Streaming intermédiaire (avant le `result` final). On extrait tout
                # ce qui ressemble à un tool call ou à un message de statut, et on le
                # relaie en Progress pour que l'UI voie l'agent travailler en direct.
                if evt_name != "result" and isinstance(data, dict):
                    # Delta du texte FINAL streamé (answer-stream) : on le relaie tel
                    # quel dans le buffer draft de l'AgentCard et on SAUTE le reste du
                    # traitement (ce n'est ni un tool_call, ni un status « humain »).
                    answer_delta = _extract_answer_delta(data)
                    if answer_delta is not None:
                        if answer_delta:
                            answer_streamed = True
                            await _relay_progress(
                                "draft_chunk",
                                {
                                    "text": answer_delta,
                                    "stream_id": answer_stream_id,
                                    "done": False,
                                },
                            )
                        continue
                    for tc in _extract_tool_calls(data):
                        key = f"{tc['tool']}|{json.dumps(tc.get('params') or {}, sort_keys=True, default=str)}"
                        if key in seen_tool_keys:
                            continue
                        seen_tool_keys.add(key)
                        await _relay_progress(
                            "tool_call",
                            {"tool": tc["tool"], "params": tc.get("params") or {}},
                        )
                    status_text = _extract_status_text(data)
                    if status_text and status_text != last_status_text:
                        # Delta : si l'agent réémet le message complet, on n'envoie
                        # que le suffixe nouveau ; sinon c'est un message tout neuf.
                        if last_status_text and status_text.startswith(
                            last_status_text
                        ):
                            delta = status_text[len(last_status_text) :]
                        else:
                            delta = ("\n" if last_status_text else "") + status_text
                        # Détecte un « nouveau pas logique » (≠ extension de prefix) :
                        # mérite alors une trace DISCRETE dans la timeline de l'agent
                        # (rendu en ligne séparée dans ToolsList côté front).
                        is_new_step = not (
                            last_status_text
                            and status_text.startswith(last_status_text)
                        )
                        last_status_text = status_text
                        if delta:
                            # Buffer accumulé (vu en bloc continu sous la card). On
                            # émet TOUT delta non vide — y compris un delta d'espaces/
                            # retour-ligne — sinon le séparateur entre deux phrases
                            # était perdu et le texte se collait.
                            await _relay_progress(
                                "thinking",
                                {
                                    "text": delta,
                                    "stream_id": think_stream_id,
                                    "done": False,
                                },
                            )
                        if is_new_step:
                            preview = status_text.strip().splitlines()[0][:200]
                            await _relay_progress("thinking", {"text": preview})
                    # Artefacts intermédiaires : chaque part `text` non encore vu est
                    # annoncé comme une étape pour visualiser ce que l'agent livre au
                    # fil de l'eau (et pas seulement à la fin).
                    for art in data.get("artifacts") or []:
                        for part in (art or {}).get("parts") or []:
                            if not isinstance(part, dict):
                                continue
                            if part.get("kind") != "text" or not part.get("text"):
                                continue
                            txt = str(part["text"])
                            key = txt[:80]
                            if key in seen_artifact_keys:
                                continue
                            seen_artifact_keys.add(key)
                            preview = txt.strip().splitlines()[0][:200]
                            await _relay_progress("thinking", {"text": f"📎 {preview}"})

                if evt_name == "result":
                    result_data = data
                    # Priorité à input-required : l'agent réclame un complément → on
                    # ne fabrique PAS un résultat « done », on remonte la question.
                    input_required = _extract_a2a_input_required(data)
                    output_text = _extract_a2a_text(data)
                    output_struct = _extract_a2a_struct(data)
                    # Tool calls éventuellement portés par l'event final aussi.
                    for tc in _extract_tool_calls(data):
                        key = f"{tc['tool']}|{json.dumps(tc.get('params') or {}, sort_keys=True, default=str)}"
                        if key in seen_tool_keys:
                            continue
                        seen_tool_keys.add(key)
                        await _relay_progress(
                            "tool_call",
                            {"tool": tc["tool"], "params": tc.get("params") or {}},
                        )
    except (TimeoutError, asyncio.TimeoutError):
        log.warning(
            "a2a-bridge: %s dépasse le hard_timeout %.0fs → échec",
            td.corr_id,
            hard_timeout,
        )
        stop.set()
        await hb_task
        _INFLIGHT_A2A.pop(td.corr_id, None)
        await _publish_failure(js, td, f"hard_timeout dépassé ({hard_timeout:.0f}s)")
        await msg.ack()
        return
    except Exception as e:  # noqa: BLE001
        log.exception("a2a-bridge: appel A2A échoué pour %s", td.corr_id)
        stop.set()
        await hb_task
        _INFLIGHT_A2A.pop(td.corr_id, None)
        await _publish_failure(js, td, f"appel A2A: {e}")
        await msg.ack()
        return

    # Annulation reçue pendant le streaming (`stop` set par la souscription cancel, hors
    # chemin timeout/exception qui ont déjà `return`). La tâche est tombstonée en base :
    # on n'enrichit pas, on ne publie pas de Result (il serait ignoré), on ACK pour ne
    # PAS redélivrer — et on évite de gaspiller le GPU partagé en géocodage/synthèse.
    if stop.is_set():
        await hb_task
        _INFLIGHT_A2A.pop(td.corr_id, None)
        log.info("a2a-bridge: tâche %s annulée → ack sans publier de Result", td.corr_id)
        await msg.ack()
        return

    # Enrichissement géo AVANT d'arrêter le heartbeat : le géocodage LLM peut
    # durer quelques secondes, et sans heartbeat le lease orchestrateur / l'ack_wait
    # du message expireraient en plein appel.
    if input_required is None:
        # GeoJSON éventuel (CSD & co.) → emprises/points pour la carte du dashboard.
        # Le document complet vit dans `result_data` (non tronqué, contrairement à
        # l'artifact texte) ; on le déterre quelle que soit sa profondeur.
        geo = _extract_geojson(result_data)
        if geo.get("polygons"):
            output_struct["polygons"] = geo["polygons"]
        if geo.get("locations"):
            output_struct["locations"] = (
                list(output_struct.get("locations") or []) + geo["locations"]
            )
        # Si l'agent n'a renvoyé qu'un blob JSON brut comme « texte », on le remplace
        # par un résumé lisible — le détail géo est porté par les widgets/carte.
        if geo and _looks_like_raw_json(output_text):
            output_text = _geo_summary(geo, td.lang)
        # Géocode les lieux sans coordonnées (typiquement la sortie d'un agent NER) :
        # sinon le front les compte mais ne les place jamais sur la carte.
        if output_struct.get("locations"):
            output_struct["locations"] = await _geocode_locations(
                output_struct["locations"], td.lang
            )

    stop.set()
    await hb_task
    _INFLIGHT_A2A.pop(td.corr_id, None)

    # Ferme le buffer thinking de cet agent côté UI (le widget recule sur l'état canonique).
    if last_status_text:
        await _relay_progress(
            "thinking", {"text": "", "stream_id": think_stream_id, "done": True}
        )
    # Idem pour le buffer draft (texte final streamé) : on signale `done` pour que
    # l'AgentCard bascule du brouillon live vers le résumé canonique du `result`.
    if answer_streamed:
        await _relay_progress(
            "draft_chunk", {"text": "", "stream_id": answer_stream_id, "done": True}
        )

    if input_required is not None:
        # L'agent distant demande un complément (state=input-required). On relaie la
        # question + le contextId : la FSM passe en AWAITING_USER_INPUT et reprendra
        # la tâche avec ce context_id à la réponse de l'analyste.
        result = Result(
            corr_id=td.corr_id,
            conv_id=td.conv_id,
            agent_id=td.agent_type,
            worker_id=f"a2a-bridge-{settings.instance_id}",
            status="input_required",
            output=input_required,
        )
    else:
        result = Result(
            corr_id=td.corr_id,
            conv_id=td.conv_id,
            agent_id=td.agent_type,
            worker_id=f"a2a-bridge-{settings.instance_id}",
            status="done",
            # `**output_struct` ajoute queries/locations si l'agent les a fournis ;
            # la FSM étale tout dans le widget (`{**res.output, ...}`), donc le front
            # reçoit summary + requêtes + points sans nouveau type de message.
            output={
                "summary": output_text or "(agent A2A sans texte)",
                "events_count": len(events_collected),
                **output_struct,
            },
        )
    await js.publish(
        subjects.agent_result(td.agent_type, td.corr_id), result.to_bytes()
    )
    await msg.ack()


async def _heartbeat(js, td: TaskDispatch, stop: asyncio.Event, msg=None) -> None:
    """Renouvelle le lease tant que l'appel A2A tourne.

    Deux choses à étendre, sinon la tâche est tuée alors qu'elle travaille :
      1. Le lease orchestrateur (table `tasks.lease_until`) — alimenté par les
         messages `Progress` sur le bus, consommés par `run_lease_consumer`.
         Progress requiert `worker_id` ; sans ce champ, ValidationError silencieuse.
      2. Le `ack_wait` NATS du message JetStream — si on dépasse `ack_wait_s`
         sans ack/in_progress, NATS redélivre le message → traitement en double.
         `msg.in_progress()` repousse la deadline.
    """
    interval = max(2.0, settings.lease_ttl_s / 3.0)
    worker_id = f"a2a-bridge-{settings.instance_id}"
    pct = 0.05
    while not stop.is_set():
        try:
            prog = Progress(
                corr_id=td.corr_id,
                conv_id=td.conv_id,
                agent_id=td.agent_type,
                worker_id=worker_id,
                pct=min(pct, 0.95),
                note="A2A streaming…",
            )
            await js.publish(
                subjects.agent_progress(td.agent_type, td.corr_id), prog.to_bytes()
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "a2a-bridge: échec heartbeat (corr=%s)", td.corr_id, exc_info=True
            )
        if msg is not None:
            try:
                await msg.in_progress()
            except Exception:  # noqa: BLE001
                log.debug(
                    "a2a-bridge: in_progress() échoué (msg déjà ack/redélivré)",
                    exc_info=True,
                )
        pct = min(pct + 0.05, 0.95)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _publish_failure(js, td: TaskDispatch, error: str) -> None:
    """Publie un Result status=failed pour qu'orchestrateur referme la boucle."""
    result = Result(
        corr_id=td.corr_id,
        conv_id=td.conv_id,
        agent_id=td.agent_type,
        worker_id=f"a2a-bridge-{settings.instance_id}",
        status="failed",
        error=error[:500],
    )
    await js.publish(
        subjects.agent_result(td.agent_type, td.corr_id), result.to_bytes()
    )


async def _on_cancel(msg) -> None:
    """Callback core NATS : interrompt l'appel A2A dont le corr_id est dans le subject.

    Subject reçu : `agent.{id}.cancel.{corr_id}` (payload vide). On `set()` le `stop`
    Event de l'appel en vol → le heartbeat s'arrête et la boucle de streaming sort à la
    prochaine itération (post-traitement = ack sans Result). corr_id inconnu (tâche déjà
    terminée / non servie par ce bridge) → ignoré silencieusement.
    """
    corr_id = msg.subject.rsplit(".", 1)[-1]
    stop = _INFLIGHT_A2A.get(corr_id)
    if stop is not None and not stop.is_set():
        log.info("a2a-bridge: cancel reçu pour %s → arrêt de l'appel A2A", corr_id)
        stop.set()


async def run_a2a_bridge(js, sm, concurrency: int = 16) -> None:
    """Boucle du bridge : un consumer durable sur `agent.*.task` en queue group.

    IMPORTANT — concurrence par tâche : un appel A2A peut durer plusieurs
    minutes (LLM + tools), donc on lance chaque `_handle` dans une task
    asyncio plutôt qu'en séquentiel. Sans ça, une tâche lente bloque toutes
    les suivantes ; leurs heartbeats ne partent jamais → `lease_expired` →
    retry → re-bloqué → FAIL en boucle. Un sémaphore limite l'inflight pour
    éviter d'OOM si beaucoup de conv tirent dessus en même temps.
    """
    sub = await nats_io.durable_pull(
        js,
        "TASKS",
        "a2a-bridge",
        subjects.AGENT_TASK_ALL,
    )
    # Souscription d'annulation en CORE NATS (`js._nc` : même connexion sous-jacente que
    # la FSM, qui publie le cancel). Wildcard global `agent.*.cancel.>` : le bridge sert
    # plusieurs agent_ids A2A, on couvre tous les corr_id en vol d'un seul subscriber.
    cancel_sub = await js._nc.subscribe(subjects.AGENT_CANCEL_ALL, cb=_on_cancel)
    sem = asyncio.Semaphore(concurrency)
    inflight: set[asyncio.Task] = set()
    log.info(
        "a2a-bridge démarré (durable=a2a-bridge, concurrence=%d, cancel=%s)",
        concurrency,
        subjects.AGENT_CANCEL_ALL,
    )

    async def _bounded(msg):
        """Wrapper : sémaphore + log d'erreur, lance _handle dans la task."""
        async with sem:
            try:
                await _handle(msg, js, sm)
            except Exception:
                log.exception("a2a-bridge: erreur non gérée")
                try:
                    await msg.nak(delay=random.uniform(1, 3))
                except Exception:  # noqa: BLE001
                    pass

    while True:
        try:
            msgs = await sub.fetch(4, timeout=2)
        except (NatsTimeoutError, asyncio.TimeoutError):
            continue
        for msg in msgs:
            t = asyncio.create_task(_bounded(msg))
            inflight.add(t)
            t.add_done_callback(inflight.discard)
