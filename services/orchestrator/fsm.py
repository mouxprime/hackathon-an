"""Machine à états de l'orchestrateur — le contrat de la FSM.

Deux tables déclaratives :
- `LLM_PREP[(state, event)]`   → fonction de *préparation* (appels LLM / lectures
  hors-transaction), exécutée HORS transaction Postgres (on ne tient jamais une
  transaction pendant un LLM).
- `TRANSITIONS[(state, event)]` → fonction d'*application*, exécutée DANS la
  transaction d'écriture courte et fencée.

Chaque transition : émet des événements UI (event_seq sans race car la conv est
verrouillée), met en file les dispatches et événements internes pour publication
APRÈS commit, et retourne l'état suivant.

Boucle humaine + exécution séquentielle (itér. 4) :
  IDLE → PLANNING → AWAITING_PLAN → AWAITING_SUBAGENTS → WRITING_REPORT → DONE
Sur une requête « investigate », l'orchestrateur **propose** un plan ordonné
(widget `plan_proposal`) et attend la décision de l'analyste (Approuver/Modifier,
événement `plan_submit`) sans bloquer sa boucle. À l'approbation, les étapes sont
dispatchées **une par une** : l'étape N+1 ne part que lorsque l'étape N est
terminale → l'ordre du plan a un sens réel à l'exécution.
"""

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

import llm
import memory
from graphit_icons import ICONS
from hemicycle_shared import subjects
from hemicycle_shared.artifacts import ArtifactEnvelope
from hemicycle_shared.artifact_store import get_artifact, put_artifact
from hemicycle_shared.config import settings
from hemicycle_shared.db import (
    AgentType,
    Attachment,
    Channel,
    Conversation,
    Event,
    LtmMemory,
    Task,
)
from hemicycle_shared.messages import (
    ArtifactRef,
    Progress,
    Result,
    TaskDispatch,
    TaskTimeout,
    UIEvent,
    WriteReport,
)

log = logging.getLogger("orchestrator.fsm")

# Catalogue d'agents — peuplé au démarrage et après chaque mutation par main.py
# depuis la table `agent_types`. Vide par défaut : tant qu'aucun agent n'a été
# créé (via OpenCode ou l'API), le planner refuse de dispatcher.
AGENT_MAP: dict[str, dict] = {}

# Handle KV `tools_config` — injecté par main.py au démarrage. Permet à
# l'orchestrateur d'injecter la config pause/enabled dans `TaskDispatch.params`
# sans toucher à Postgres côté worker (invariant n°7).
TOOLS_KV = None

# Connexion NATS — injectée par main.py au démarrage. Sert au relais LIVE du
# raisonnement de planification (mode « thinking ») sur le canal éphémère
# `orchestrator.progress.{conv_id}`, hors de la boucle de transition.
NC = None


async def emit_live_thinking(
    conv_id: str, text: str, stream_id: str, done: bool = False
) -> None:
    """Relaie un chunk de raisonnement de l'orchestrateur en live (canal éphémère).

    Publie un `Progress(kind="thinking")` sur `orchestrator.progress.{conv_id}` — non
    journalisé, non rejoué : c'est l'effet « streaming » (le frontend l'accumule par
    `stream_id` dans un panneau Réflexion qui se replie une fois `done`).
    """
    if NC is None:
        return
    prog = Progress(
        corr_id=stream_id,
        conv_id=conv_id,
        agent_id="orchestrator",
        worker_id="orchestrator",
        kind="thinking",
        trace_data={"text": text, "stream_id": stream_id, "done": done},
    )
    try:
        await NC.publish(subjects.orch_progress(conv_id), prog.to_bytes())
    except Exception:  # relais best-effort, jamais bloquant
        pass


async def emit_message_chunk(
    conv_id: str, stream_id: str, text: str, done: bool = False
) -> None:
    """Relaie un chunk d'une réponse d'assistant en cours de rédaction (canal éphémère).

    Même mécanique que `emit_report_chunk` mais avec `kind="message_chunk"` : sert à
    la synthèse de chat (tour SANS rapport). Le frontend accumule par `stream_id` et
    rend un bubble assistant qui se rédige en direct, remplacé par le message canonique
    durable dès que la transition `h_write_report` est commitée.
    """
    if NC is None:
        return
    prog = Progress(
        corr_id=stream_id,
        conv_id=conv_id,
        agent_id="orchestrator",
        worker_id="orchestrator",
        kind="message_chunk",
        trace_data={"text": text, "stream_id": stream_id, "done": done},
    )
    try:
        await NC.publish(subjects.orch_progress(conv_id), prog.to_bytes())
    except Exception:  # relais best-effort, jamais bloquant
        pass


async def emit_report_chunk(
    conv_id: str, stream_id: str, text: str, done: bool = False
) -> None:
    """Relaie un chunk du rapport en cours de rédaction (canal éphémère).

    Même mécanique que `emit_live_thinking` mais avec `kind="report_chunk"` : le
    frontend accumule par `stream_id` (= widget_id du futur widget `report`) et
    rend la synthèse au fil de l'eau dans le widget rapport. Le widget canonique
    final remplace le buffer streamé une fois la transition `h_write_report` commitée.
    """
    if NC is None:
        return
    prog = Progress(
        corr_id=stream_id,
        conv_id=conv_id,
        agent_id="orchestrator",
        worker_id="orchestrator",
        kind="report_chunk",
        trace_data={"text": text, "stream_id": stream_id, "done": done},
    )
    try:
        await NC.publish(subjects.orch_progress(conv_id), prog.to_bytes())
    except Exception:  # relais best-effort, jamais bloquant
        pass


async def _publish_agent_cancel(agent_type: str, corr_id: str) -> None:
    """Propage l'annulation d'une tâche en vol vers le worker / le bridge A2A (cf. P8).

    Best-effort en core NATS (pas de JetStream) : le subject `agent.{agent}.cancel.{corr}`
    porte le corr_id, payload vide. Le tombstone DB (`tasks.status="cancelled"`) reste
    l'autorité — les résultats tardifs sont déjà ignorés via `tasks.status` ; ce signal
    sert juste à rendre le pool 35B partagé plus tôt. Même robustesse que les autres
    publish du fichier : garde `NC is None` + try/except (le subject est fourni par
    `subjects.agent_cancel`, ajouté côté plomberie).
    """
    if NC is None:
        return
    try:
        await NC.publish(subjects.agent_cancel(agent_type, corr_id), b"")
    except Exception:  # propagation best-effort, jamais bloquante
        pass


# Schéma logique de l'enveloppe Object Store servant de checkpoint de rédaction (A5).
_REPORT_CHECKPOINT_SCHEMA = "report_checkpoint"


async def _read_report_checkpoint(artifact_id: str) -> str | None:
    """Relit un rapport/synthèse déjà rédigé (checkpoint idempotent, clé = msg_id).

    Sur LostLock/nak/crash entre la prep coûteuse et le commit, on réutilise le texte
    déjà produit plutôt que de rappeler le LLM : même contenu re-streamé à l'UI (pas de
    texte divergent qui remplace le premier) et pool 35B épargné. Best-effort : toute
    erreur Object Store (ou `NC` absent) → None → génération normale.
    """
    if NC is None:
        return None
    try:
        env = await get_artifact(NC.jetstream(), artifact_id)
    except Exception:
        return None
    if env is None:
        return None
    text = (env.payload or {}).get("text")
    return text if isinstance(text, str) and text.strip() else None


async def _write_report_checkpoint(artifact_id: str, regime: str, text: str) -> None:
    """Persiste le rapport/synthèse rédigé sous la clé déterministe (msg_id) — cf. A5.

    Cache idempotent autour de la prep (pas de changement de FSM ni du pattern Phase A/B).
    Best-effort : toute erreur Object Store est avalée (jamais de crash de la prep).
    """
    if NC is None or not (text and text.strip()):
        return
    try:
        env = ArtifactEnvelope(
            artifact_id=artifact_id,
            agent_type="orchestrator",
            schema_name=_REPORT_CHECKPOINT_SCHEMA,
            payload={"text": text, "regime": regime},
            preview=text[:4000],
        )
        await put_artifact(NC.jetstream(), env)
    except Exception:  # noqa: BLE001
        log.warning(
            "checkpoint rédaction: persistance Object Store échouée (ignoré)",
            exc_info=True,
        )


# Statuts terminaux d'une tâche (plus aucune transition attendue).
TERMINAL = {"done", "failed", "timeout", "cancelled"}

# --- GraphIt JSON builder ---
# Produit le JSON { dataType:"graph-it", nodes, edges } depuis les steps du plan.
# Émis comme widget `graphit_flow` à la fin de chaque investigation (h_write_report).

# Couleur du disque par type d'agent — IDENTIQUE à la couleur des avatars de
# graphit_icons (ICONS), pour que le fond du nœud prolonge l'icône sans rupture.
_AGENT_COLORS: dict[str, list[int]] = {
    "agent_votes":   [34, 48, 97],     # bleu Assemblée
    "agent_deputes": [138, 100, 32],   # or hémicycle
    "agent_lois":    [5, 150, 105],
    "report":        [180, 83, 9],
}
_DEFAULT_AGENT_COLOR: list[int] = [71, 85, 105]

# Type d'agent → clé d'icône dans ICONS. Tout type inconnu retombe sur l'avatar
# générique "agent".
_AGENT_ICON: dict[str, str] = {
    "mi_search": "mi_search", "imint": "imint", "geoint": "geoint",
    "translation": "translation", "report": "report", "nlp": "nlp",
    "csd": "csd", "relegsim": "relegsim",
}


def _status_stroke(status: str) -> list[int]:
    """Couleur de l'anneau du nœud selon le statut de la tâche (vert/rouge/ambre/gris)."""
    if status == "done":
        return [16, 185, 129]
    if status in ("failed", "timeout"):
        return [239, 68, 68]
    if status == "running":
        return [251, 191, 36]
    return [148, 163, 184]


def build_graphit_json(plan: dict) -> dict:
    """Construit le JSON GraphIt depuis les steps du plan de la conversation.

    Chaque nœud est un avatar circulaire (shape Circle + category withMedia +
    icône PNG par type) cerclé d'un anneau de statut — cf. graphit_icons.ICONS.
    """
    steps = plan.get("steps") or []
    nodes: list[dict] = []
    edges: list[dict] = []

    # Registre d'images : on ne pousse dans `images[]` que les icônes réellement
    # utilisées, et on mémorise leur index pour le champ `image` des nœuds.
    images: list[str] = []
    _icon_idx: dict[str, int] = {}

    def use_icon(key: str) -> int:
        k = key if key in ICONS else "agent"
        if k not in _icon_idx:
            _icon_idx[k] = len(images)
            images.append(ICONS[k])
        return _icon_idx[k]

    def node(
        nid: str, ntype: str, label: str, tooltip: str,
        color: list[int], stroke: list[int], scale: float,
        x: float, y: float, image_idx: int,
        extra: dict | None = None, badge: int = 0, neighbors: int = 0,
    ) -> dict:
        return {
            "data": {
                "coreSource": "hemicycle", "coreId": nid,
                "coreType": ntype, "coreRefType": ntype,
                "hierarchy": [
                    {"type": "HémicycleExecution", "label": "Hémicycle Execution"},
                    {"type": ntype, "label": ntype},
                ],
                "label": label, "nbNeighbors": neighbors, "tooltip": tooltip,
                "style": {
                    "shape": "Circle", "color": color, "labelColor": [30, 41, 59],
                    "shapeScale": scale, "strokeColor": stroke, "strokeWidth": 6,
                },
                "externalRefs": [], "tags": [], "data": extra or {},
                "summary": {
                    "attributes": [{"label": k, "value": str(v)} for k, v in (extra or {}).items()]
                },
                "status": {"ghosted": False, "hidden": False},
                "neighbors": [], "linkTypes": [], "commentText": "",
                "visible": True, "allExpanded": False, "category": "withMedia",
                "graphDataType": "node", "editable": False,
                "expandNb": 0, "fixed": True, "image": image_idx, "badge": badge,
            },
            "location": {"x": x, "y": y},
        }

    def edge(eid: str, src: str, tgt: str, label: str, etype: str) -> dict:
        return {
            "data": {
                "coreSource": "hemicycle", "coreId": eid,
                "coreType": etype, "coreRefType": etype,
                "hierarchy": [
                    {"type": "HémicycleExecution", "label": "Hémicycle Execution"},
                    {"type": etype, "label": etype},
                ],
                "label": label, "sourceId": src, "targetId": tgt, "tooltip": "",
                "style": {"color": [148, 163, 184], "labelColor": [71, 85, 105], "width": 2},
                "externalRefs": [], "tags": [], "data": {},
                "summary": {"attributes": []},
                "status": {"ghosted": False, "hidden": False},
                "graphDataType": "edge", "editable": False, "visible": True,
                "image": 0, "badge": 0,
            },
            "source": src,
            "target": tgt,
        }

    ORCH = "hemicycle_orchestrator"
    nodes.append(node(
        ORCH, "Orchestrator", "Orchestrateur", "Hémicycle Orchestrator",
        [0, 32, 91], [100, 140, 220], 1.4, 0, 0, use_icon("orchestrator"),
        {"tasks": len(steps)}, badge=len(steps), neighbors=len(steps),
    ))

    AGENT_X = 360
    AGENT_GAP = 200
    agent_y_origin = -((len(steps) - 1) * AGENT_GAP) / 2

    for i, step in enumerate(steps):
        aid = f"hemicycle_agent_{step.get('corr_id') or i}"
        atype = step.get("agent_type", "agent")
        status = step.get("status", "pending")
        color = _AGENT_COLORS.get(atype, _DEFAULT_AGENT_COLOR)
        stroke = _status_stroke(status)
        ay = agent_y_origin + i * AGENT_GAP

        nodes.append(node(
            aid, "Agent", atype, f"{atype} — {status}",
            color, stroke, 1.0, AGENT_X, ay, use_icon(_AGENT_ICON.get(atype, "agent")),
            {"status": status, "instruction": (step.get("instruction") or "")[:80]},
            badge=0, neighbors=1,
        ))

        prev = ORCH if i == 0 else f"hemicycle_agent_{steps[i - 1].get('corr_id') or (i - 1)}"
        edges.append(edge(f"e_{prev}_{aid}", prev, aid, "dispatche" if i == 0 else "puis", "ExecutionFlow"))

    all_x = [n["location"]["x"] for n in nodes]
    all_y = [n["location"]["y"] for n in nodes]
    min_x = min(all_x) - 100
    min_y = min(all_y) - 100
    w = max(all_x) - min_x + 300
    h = max(all_y) - min_y + 200

    return {
        "dataType": "graph-it",
        "layout": "normal",
        "NPM_VERSION": "3.0.0",
        "nodes": nodes,
        "draw": [],
        "edges": edges,
        "viewport": {
            "scale": 0.85,
            "bounds": {"x": min_x, "y": min_y, "width": w, "height": h},
            "position": {"x": -min_x * 0.85, "y": -min_y * 0.85},
        },
        "images": images,
    }

# Discipline → type de widget de résultat affiché dans le chat.
# Le front rend ces types via sa registry (repli markdown + footer sources si inconnu).
_RESULT_WIDGET = {"agent_votes": "vote_chart", "agent_deputes": "fiche_depute",
                  "agent_lois": "timeline_loi"}

# Messages utilisateur de la FSM, par langue. Anglais par défaut (cf. invariant UI).
# Les valeurs sont des gabarits `str.format` ; les clés sont stables (≠ texte affiché).
_MSG: dict[str, dict[str, str]] = {
    "en": {
        "investigation_failed": "Investigation failed: no lead panned out.",
        "no_agents": (
            "Error: no agent available in the registry. Create an agent via OpenCode "
            "(`mcp__hemicycle__create_agent`) or via POST /api/agents before launching an "
            "investigation."
        ),
        "ask_subject": (
            "What subject should I launch the investigation on "
            "(country, event, person, infrastructure…)?"
        ),
        "empty_plan": "Empty plan or unknown agents — investigation aborted.",
        "cancelled": "Investigation interrupted by the analyst.",
        "reorient_clarify": (
            "Investigation in progress — refine your question or name a new subject to redirect."
        ),
        "reorient_cancelled": "Redirecting — current task cancelled.",
        "deferred_during_report": (
            "Noted — I'm finishing the report, I'll take your message into account "
            "right after."
        ),
        "task_retry": "{agent} task timed out — retrying ({n}/{max}).",
        "task_abandoned": "{agent} task abandoned ({reason}).",
        "task_skipped_upstream": (
            "{agent} step skipped: its upstream input ({upstream}) failed — "
            "not run on the raw input to avoid a wrong-language result."
        ),
        "compact_nothing": "Nothing to compact: the conversation history is empty.",
        "memory_proposed": (
            "I extracted a few facts to memorize — confirm or reject them on the right."
        ),
    },
    "fr": {
        "investigation_failed": "Investigation échouée : aucune piste n'a abouti.",
        "no_agents": (
            "Erreur : aucun agent disponible dans la registry. "
            "Crée un agent via OpenCode (`mcp__hemicycle__create_agent`) "
            "ou via POST /api/agents avant de relancer une investigation."
        ),
        "ask_subject": (
            "Sur quel sujet veux-tu que je lance l'investigation "
            "(pays, événement, personne, infrastructure…) ?"
        ),
        "empty_plan": "Plan vide ou agents inconnus — investigation abandonnée.",
        "cancelled": "Investigation interrompue par l'analyste.",
        "reorient_clarify": (
            "Investigation en cours — précise ta question ou indique un nouveau sujet "
            "pour réorienter."
        ),
        "reorient_cancelled": "Réorientation — tâche en cours annulée.",
        "deferred_during_report": (
            "Bien noté — je termine le rapport, je prends ton message en compte "
            "juste après."
        ),
        "task_retry": "Tâche {agent} expirée — nouvelle tentative ({n}/{max}).",
        "task_abandoned": "Tâche {agent} abandonnée ({reason}).",
        "task_skipped_upstream": (
            "Étape {agent} ignorée : son entrée amont ({upstream}) a échoué — "
            "non exécutée sur l'entrée brute pour éviter un résultat dans la mauvaise langue."
        ),
        "compact_nothing": "Rien à compacter : l'historique de la conversation est vide.",
        "memory_proposed": (
            "J'ai extrait quelques faits à mémoriser — valide-les ou rejette-les à droite."
        ),
    },
}


def _msg(lang: str, key: str, **kw) -> str:
    """Rend un message FSM localisé (repli anglais si langue/clé inconnue)."""
    table = _MSG.get(lang) or _MSG["en"]
    template = table.get(key) or _MSG["en"].get(key, key)
    return template.format(**kw) if kw else template


def _now() -> datetime:
    """Horodatage UTC courant."""
    return datetime.now(timezone.utc)


@dataclass
class ConvSnapshot:
    """Vue en lecture seule de la conversation, prise en phase A (avant les appels LLM)."""

    conv_id: str
    user_id: str
    fsm_state: str
    version: int
    plan: dict
    lang: str = "fr"  # langue courante de la conv ("en" | "fr")


@dataclass
class TransitionContext:
    """Contexte d'une transition (phase B) : session DB + files de publication post-commit."""

    session: AsyncSession
    conv: Conversation
    event: Any  # message parsé (UserInput / Result / ...)
    event_type: str
    pending_ui: list[UIEvent] = field(default_factory=list)  # → orchestrator.events
    pending_dispatches: list[TaskDispatch] = field(
        default_factory=list
    )  # → agent.{id}.task
    pending_internal: list[Any] = field(
        default_factory=list
    )  # → orchestrator.internal.*
    pending_artifacts: list[ArtifactEnvelope] = field(
        default_factory=list
    )  # → Object Store (post-commit)

    def emit(self, type_: str, payload: dict) -> None:
        """Émet un événement UI : incrémente event_seq, journalise, met en file pour publication."""
        self.conv.event_seq += 1
        ev = UIEvent(
            conv_id=self.conv.conv_id,
            seq=self.conv.event_seq,
            type=type_,
            payload=payload,
        )
        self.session.add(
            Event(conv_id=ev.conv_id, seq=ev.seq, type=ev.type, payload=ev.payload)
        )
        self.pending_ui.append(ev)


# --------------------------------------------------------------------------
# Phase A — préparation (appels LLM / lectures, HORS transaction)
# --------------------------------------------------------------------------


async def _recent_chat_history(
    session: AsyncSession, conv_id: str, limit: int = 8, compacted_seq: int = 0
) -> list[dict]:
    """Récupère les derniers messages user/assistant de la conv (ordre chronologique).

    Le routeur LLM en a besoin pour interpréter les suivis. On ne retient que
    `type='message'`. Si `compacted_seq > 0`, les événements antérieurs à cette
    borne sont exclus : le message résumé (à `compacted_seq`) est inclus en premier,
    réduisant réellement la taille du contexte transmis au LLM.
    """
    stmt = (
        select(Event)
        .where(Event.conv_id == conv_id, Event.type == "message")
        .order_by(desc(Event.seq))
        .limit(limit)
    )
    if compacted_seq > 0:
        stmt = (
            select(Event)
            .where(
                Event.conv_id == conv_id,
                Event.type == "message",
                Event.seq >= compacted_seq,
            )
            .order_by(desc(Event.seq))
            .limit(limit)
        )
    rows = (await session.execute(stmt)).scalars().all()
    out: list[dict] = []
    for ev in reversed(rows):
        payload = ev.payload or {}
        role = payload.get("role")
        body = payload.get("text") or ""
        if role in ("user", "assistant") and body:
            out.append({"role": role, "text": body})
    return out


async def _read_tools_config(agent_id: str) -> dict:
    """Lit l'état pause/enabled des outils d'un agent depuis le KV NATS (idempotent, repli vide)."""
    if TOOLS_KV is None:
        return {}
    try:
        entry = await TOOLS_KV.get(agent_id)
        return json.loads(entry.value)
    except Exception:
        return {}  # bucket vide / agent sans tools


async def _load_attachments(session: AsyncSession, meta: list[dict]) -> list[dict]:
    """Charge le texte des pièces jointes référencées (par id) pour l'injecter au dispatch.

    L'orchestrateur PEUT lire Postgres ; il recharge ici le contenu extrait par le
    gateway pour le placer dans `TaskDispatch.params` — les workers reçoivent le
    contenu par le bus, jamais en lisant Postgres (invariant n°7).
    """
    ids = [a.get("id") for a in (meta or []) if a.get("id")]
    if not ids:
        return []
    rows = (
        (await session.execute(select(Attachment).where(Attachment.id.in_(ids))))
        .scalars()
        .all()
    )
    return [
        {
            "filename": r.filename,
            "text": r.text_content,
            "s3_uri": r.s3_uri,
            "content_type": r.content_type,
            "size_bytes": r.size_bytes,
        }
        for r in rows
    ]


def _format_context_refs(refs: list[dict]) -> str:
    """Formate les conversations référencées (@ mention) en blocs de contexte pour le prompt LLM.

    Chaque ref porte `title`, `conv_id` et `events` (liste d'UIEvent type=message).
    Produit des blocs balisés lisibles par le routeur LLM pour le raccordement de contexte.
    """
    blocks = []
    for ref in refs:
        title = ref.get("title") or ref.get("conv_id", "?")
        events = ref.get("events") or []
        lines = []
        for ev in events:
            payload = ev.get("payload") or {}
            ev_type = ev.get("type", "")
            seq = ev.get("seq", "?")
            if ev_type == "message":
                role = payload.get("role", "?")
                text = (payload.get("text") or "").strip()
                if text:
                    lines.append(f"[seq {seq}] {role} : {text}")
            elif ev_type == "widget":
                data = payload.get("data") or {}
                # `summary` = synthèse des sous-agents (mi_search/imint/geoint) ; `text` =
                # rapport final ; `reply` = réponse directe. Sans `summary`, la RÉPONSE
                # d'une investigation (portée par le widget résultat) serait perdue du
                # contexte → l'orch ré-investiguerait une question déjà répondue.
                text = (
                    data.get("text") or data.get("reply") or data.get("summary") or ""
                ).strip()
                if text:
                    agent_type = data.get("agent_type", "")
                    label = f"résultat {agent_type}" if agent_type else "résultat agent"
                    lines.append(
                        f"[seq {seq}] {label} : {text[:2000]}"
                    )  # cap à 2000 chars par widget
        if lines:
            body = "\n".join(lines)
            blocks.append(f"--- Contexte : {title} ---\n{body}\n---")
    return "\n\n".join(blocks)


async def _recent_results_context(
    session: AsyncSession, conv_id: str, limit: int = 8, compacted_seq: int = 0
) -> str:
    """Retourne le corps des résultats (widgets) déjà produits dans la conversation courante.

    Même logique de fenêtre que `_recent_chat_history` mais sur les events `type='widget'` :
    extrait `data.text`, `data.reply` ou `data.summary` pour re-présenter au routeur LLM
    les réponses déjà fournies — évite de re-spawner un sous-agent pour une question déjà
    répondue dans la même conversation.
    """
    stmt = (
        select(Event)
        .where(Event.conv_id == conv_id, Event.type == "widget")
        .order_by(desc(Event.seq))
        .limit(limit)
    )
    if compacted_seq > 0:
        stmt = (
            select(Event)
            .where(
                Event.conv_id == conv_id,
                Event.type == "widget",
                Event.seq >= compacted_seq,
            )
            .order_by(desc(Event.seq))
            .limit(limit)
        )
    rows = (await session.execute(stmt)).scalars().all()
    lines: list[str] = []
    for ev in reversed(rows):
        data = (ev.payload or {}).get("data") or {}
        text = (
            data.get("text") or data.get("reply") or data.get("summary") or ""
        ).strip()
        if not text:
            continue
        agent_type = data.get("agent_type", "")
        label = f"résultat {agent_type}" if agent_type else "résultat agent"
        lines.append(f"[seq {ev.seq}] {label} : {text[:2000]}")
    return "\n".join(lines)


async def _refresh_agent_map(session: AsyncSession) -> None:
    """Recharge le catalogue d'agents depuis Postgres (un agent créé via API devient routable)."""
    global AGENT_MAP
    agents = (await session.execute(select(AgentType))).scalars().all()
    AGENT_MAP = {
        a.id: {
            "hard_timeout_s": a.hard_timeout_s,
            "discipline": a.discipline,
            "description": a.description,
            "when_to_use": a.when_to_use,
            "transport": a.transport,
        }
        for a in agents
    }


async def _prep_planning(snapshot: ConvSnapshot, event: Any, sm) -> dict:
    """Planification : mémoire + routage LLM (ou workflow imposé) + pièces jointes.

    La mémoire LT est consultée AVANT le LLM pour la montrer à l'analyste via un
    widget. Le **routeur LLM** décide ensuite si la requête mérite une investigation
    (proposer un plan d'agents) ou une réponse directe (chitchat). Si l'analyste a
    pré-sélectionné un **workflow** (`payload.workflow_steps`), on court-circuite le
    routage et on propose directement ses étapes. Aucune config tools ici : elle est
    lue au moment du dispatch (cf. `_prep_dispatch`).
    Retourne `tokens` pour l'accumulation dans `_run_transition`.
    """
    text = event.text
    payload = getattr(event, "payload", {}) or {}
    # Positions structurées issues d'une alerte capteur (cf. gateway._trigger_hemicycle_investigation) :
    # transportées telles quelles jusqu'au dispatch pour être rendues en lignes 📍 à l'agent C2.
    locations = payload.get("locations") or []
    thinking_on = bool(payload.get("thinking"))
    force_report = bool(payload.get("force_report"))
    context_refs = payload.get("context_refs") or []
    ref_context = (_format_context_refs(context_refs) or None) if context_refs else None
    # Titres légers des convs référencées (pour le widget mémoire : « contexte récupéré depuis X »).
    ref_titles = [
        {
            "conv_id": r.get("conv_id"),
            "title": (r.get("title") or r.get("conv_id") or "?"),
        }
        for r in context_refs
    ]
    # Salon d'état-major : `conv_id = "chan-<id>"` → on dérive l'id de cellule pour
    # (1) scoper la mémoire à la cellule et (2) charger son prompt système.
    channel_id = (
        snapshot.conv_id[len("chan-"):] if snapshot.conv_id.startswith("chan-") else None
    )
    async with sm() as s:
        # Mémoire de cellule (+ globale) si salon, sinon mémoire user/globale.
        memories = await memory.recall(
            s, snapshot.user_id, text, limit=5 if channel_id else 3, channel_id=channel_id
        )
        await _refresh_agent_map(s)
        # Chargement du compacted_seq pour filtrer l'historique au-delà de la dernière compaction.
        conv_row = await s.get(Conversation, snapshot.conv_id)
        compacted_seq = (conv_row.compacted_seq if conv_row else 0) or 0
        # Prompt système de la cellule (= `Channel.description`) pour orienter la
        # planification. Dérivé du conv_id (jamais du client) → robuste à travers les
        # tours et reconnexions. Réutilise un champ DB existant.
        channel_prompt = None
        if channel_id:
            ch = await s.get(Channel, channel_id)
            if ch:
                channel_prompt = (ch.description or "").strip() or None
            log.info(
                "planning salon=%s : prompt cellule %s (%d mémoires)",
                channel_id,
                "appliqué" if channel_prompt else "vide",
                len(memories),
            )
        history = await _recent_chat_history(
            s, snapshot.conv_id, limit=8, compacted_seq=compacted_seq
        )
        self_context = (
            await _recent_results_context(
                s, snapshot.conv_id, limit=8, compacted_seq=compacted_seq
            )
            or None
        )
        attachments = await _load_attachments(s, payload.get("attachments"))

    lang = snapshot.lang
    tokens = 0
    workflow_steps = payload.get("workflow_steps")
    if workflow_steps:
        # Workflow imposé : on propose ses étapes (skip des agents inconnus de la registry).
        steps = [
            {
                "plan_step_id": f"step-{i}",
                "agent_type": st["agent_type"],
                "instruction": (st.get("instruction") or "").strip()
                or llm.default_instruction(
                    text, AGENT_MAP.get(st["agent_type"], {}), lang
                ),
            }
            for i, st in enumerate(
                [s for s in workflow_steps if s.get("agent_type") in AGENT_MAP], 1
            )
        ]
        routing = {
            "intent": "investigate" if steps else "chitchat",
            "agents": [s["agent_type"] for s in steps],
            "reply": "",
            "tokens": 0,
        }
    else:
        # Mode « thinking » : on relaie le raisonnement de planification en live.
        async def _on_think(chunk: str, done: bool) -> None:
            await emit_live_thinking(snapshot.conv_id, chunk, "planning", done)

        routing = await llm.route_request(
            text,
            AGENT_MAP,
            history=history,
            lang=lang,
            memories=memories,
            ref_context=ref_context,
            self_context=self_context,
            attachments=attachments,
            max_tokens=payload.get("max_tokens"),
            channel_prompt=channel_prompt,
            on_thinking=_on_think if thinking_on else None,
            # Bouton « think » de l'UI : ON → l'orchestrateur (Hémicycle) raisonne en
            # <think> ; OFF → pas de raisonnement. Prioritaire sur le défaut global du
            # stub. Les sous-agents, eux, ne pensent JAMAIS (réglé côté agents).
            enable_thinking=thinking_on,
        )
        tokens = routing.get("tokens") or 0
        if routing.get("intent") == "investigate":
            steps = llm._build_steps(
                text,
                routing.get("agents") or [],
                AGENT_MAP,
                routing.get("instructions"),
            )
        else:
            steps = []
    return {
        "text": text,
        "steps": steps,
        "memories": memories,
        "attachments": attachments,
        "routing": routing,
        "force_report": force_report,
        "context_refs": ref_titles,
        "tokens": tokens,
        "max_tokens": payload.get("max_tokens"),
        "locations": locations,
    }


async def _prep_dispatch(snapshot: ConvSnapshot, event: Any, sm) -> dict:
    """Approbation du plan : recharge le catalogue + la config tools des agents retenus.

    Le plan final (Approuvé tel quel ou Modifié dans l'éditeur) arrive dans
    `event.payload["steps"]`. On lit ici la config pause/enabled de chaque agent
    depuis le KV pour l'injecter au dispatch (les workers ne touchent jamais Postgres).
    """
    steps = event.payload.get("steps") or []
    async with sm() as s:
        await _refresh_agent_map(s)
    tools_by_agent: dict[str, dict] = {}
    for agent_id in {
        st.get("agent_type") for st in steps if st.get("agent_type") in AGENT_MAP
    }:
        tools_by_agent[agent_id] = await _read_tools_config(agent_id)
    return {"steps": steps, "tools_by_agent": tools_by_agent}


async def _prep_report(snapshot: ConvSnapshot, event: Any, sm) -> dict:
    """Rédaction de la réponse finale — deux régimes (rapport ou synthèse chat).

    `force_report=True`  → rapport long, streamé live dans le widget TipTap.
    `force_report=False` → synthèse courte (~3-5 phrases) non-streamée, retournée
                           comme message d'assistant direct dans le chat. Permet
                           de TOUJOURS clore le tour par un mot de l'orchestrateur,
                           jamais sur les seuls widgets de résultat.

    Plus de prompt vide (qui produisait un rapport bidon) : on reprend le sujet
    de l'investigation et les sorties structurées des agents `done` (summary, sources,
    localisations, détections). Priorité basse (plancher anti-famine du pool LLM).
    """
    async with sm() as s:
        conv = await s.get(Conversation, snapshot.conv_id)
        lang = conv.lang if conv else snapshot.lang
        plan = (conv.plan if conv else {}) or {}
        text = plan.get("text", "")
        # Salon d'état-major : on charge le prompt système de la cellule pour orienter
        # la réponse finale (synthèse chat ou rapport), pas seulement la planification.
        channel_prompt = None
        if snapshot.conv_id.startswith("chan-"):
            ch = await s.get(Channel, snapshot.conv_id[len("chan-"):])
            if ch:
                channel_prompt = (ch.description or "").strip() or None
        corr_ids = [
            st.get("corr_id") for st in plan.get("steps", []) if st.get("corr_id")
        ]
        results: list[dict] = []
        if corr_ids:
            rows = (
                (await s.execute(select(Task).where(Task.corr_id.in_(corr_ids))))
                .scalars()
                .all()
            )
            for t in rows:
                if t.status == "done" and t.result_payload:
                    results.append(
                        {"agent_type": t.agent_type, "output": t.result_payload}
                    )

    force_report = bool(plan.get("force_report"))
    turn = plan.get("turn", 1)

    # Checkpoint idempotent (A5) : la clé est le msg_id déterministe du WriteReport
    # (`write_report:{conv}:{turn}`). Sur rejeu (LostLock/nak/crash entre cette prep
    # coûteuse et le commit), on réutilisera le texte déjà rédigé au lieu de re-appeler
    # le LLM ET de re-streamer un texte divergent à l'UI.
    checkpoint_id = f"write_report:{snapshot.conv_id}:{turn}"
    cached_text = await _read_report_checkpoint(checkpoint_id)

    # Dégradation du pipeline (cf. P6) : étapes sautées faute d'amont valide, ou
    # producteurs en échec. On la fait REMONTER dans la rédaction (pas de dégradation
    # silencieuse) en préfixant une consigne explicite au prompt — le rédacteur doit
    # mentionner la limite plutôt que présenter un résultat partiel comme complet.
    degraded = [
        st
        for st in plan.get("steps", [])
        if st.get("degraded")
        or st.get("status") in ("skipped", "timeout", "failed", "cancelled")
    ]
    degradation_note = ""
    if degraded:
        agents = ", ".join(
            sorted({(st.get("agent_type") or "?").upper() for st in degraded})
        )
        if lang == "fr":
            degradation_note = (
                f"\n\nNOTE DE DÉGRADATION — certaines étapes n'ont pas abouti et ont été "
                f"ignorées (amont en échec) : {agents}. Mentionne explicitement cette "
                f"limite dans la réponse ; ne présente pas le résultat comme complet."
            )
        else:
            degradation_note = (
                f"\n\nDEGRADATION NOTE — some steps did not complete and were skipped "
                f"(upstream failure): {agents}. Explicitly mention this limitation in the "
                f"reply; do not present the result as complete."
            )

    # --- Régime « synthèse de chat » (pas de bouton Rapport coché) -----------
    # On rédige un court message d'assistant qui résume ce que les agents ont fait
    # et répond à la query, STREAMÉ token-par-token dans le chat (canal éphémère
    # message_chunk) pour le dynamisme. Le message canonique durable est émis par
    # h_write_report (phase B) et remplace le bubble streamé.
    if not force_report:
        stream_id = f"{snapshot.conv_id}:chat-summary-t{turn}"
        if cached_text is not None:
            # Rejeu : on ré-émet le MÊME texte (déterminisme perçu, pas de re-stream
            # divergent) en un chunk, sans rappeler le LLM.
            await emit_message_chunk(snapshot.conv_id, stream_id, "", done=False)
            await emit_message_chunk(snapshot.conv_id, stream_id, cached_text, done=False)
            await emit_message_chunk(snapshot.conv_id, stream_id, "", done=True)
            return {"force_report": False, "summary_text": cached_text, "tokens": 0}
        prompt = llm.build_chat_summary_prompt(text, results, lang, channel_prompt=channel_prompt)
        prompt += degradation_note
        # Ouverture du buffer côté front : un chunk vide « start » fait apparaître
        # le bubble assistant live avant même le premier token.
        await emit_message_chunk(snapshot.conv_id, stream_id, "", done=False)
        summary_text = ""
        tokens = 0
        try:
            stream_usage: dict = {}
            stream_clean = False  # True seulement si done avec finish_reason="stop"
            async for chunk in llm.complete_stream(
                "report-writing", "low", prompt, lang=lang
            ):
                ctype, ctext = chunk.get("type"), chunk.get("text", "")
                if ctype == "token" and ctext:
                    summary_text += ctext
                    await emit_message_chunk(
                        snapshot.conv_id, stream_id, ctext, done=False
                    )
                elif ctype == "thinking" and ctext:
                    # Raisonnement du rédacteur sur un stream séparé (ne pollue pas
                    # le buffer du message — le front rend les deux distinctement).
                    await emit_live_thinking(
                        snapshot.conv_id, ctext, f"{stream_id}-think", done=False
                    )
                elif ctype == "done":
                    stream_usage = chunk.get("usage") or {}
                    stream_clean = True
            tokens = (stream_usage.get("prompt_tokens") or 0) + (
                stream_usage.get("completion_tokens") or 0
            )
            # Repli si texte vide OU si le flux ne s'est pas terminé proprement
            # (hoquet vLLM à mi-rapport → texte tronqué non vide mais incomplet).
            # llm.complete_stream lève déjà RuntimeError sur fin non-propre ; cette
            # garde est une défense en profondeur pour les chemins qui contournent
            # le wrapper (appel direct à _complete_stream_raw, replay de tests…).
            if not summary_text.strip() or not stream_clean:
                raise RuntimeError("synthèse LLM: flux vide ou terminaison non-propre")
        except Exception:  # noqa: BLE001
            log.warning(
                "report (chat): rédaction LLM indisponible → repli déterministe",
                exc_info=True,
            )
            summary_text = llm.build_chat_summary_fallback(text, results, lang)
            tokens = 0
            # On pousse le repli en un seul chunk pour finir le stream propre côté front.
            await emit_message_chunk(snapshot.conv_id, stream_id, summary_text, done=False)
        # Ferme les buffers côté front : la source de vérité devient le message canonique.
        await emit_live_thinking(snapshot.conv_id, "", f"{stream_id}-think", done=True)
        await emit_message_chunk(snapshot.conv_id, stream_id, "", done=True)
        # Checkpoint (A5) : on persiste le texte final pour un rejeu idempotent.
        await _write_report_checkpoint(checkpoint_id, "chat_summary", summary_text)
        return {
            "force_report": False,
            "summary_text": summary_text,
            "tokens": tokens,
        }

    # --- Régime « rapport complet » (bouton Rapport coché) -------------------
    stream_id = f"{snapshot.conv_id}:report-t{turn}"
    if cached_text is not None:
        # Rejeu : on ré-émet le MÊME rapport (déterminisme perçu, pas de re-stream
        # divergent dans le widget TipTap), sans rappeler le LLM.
        await emit_report_chunk(snapshot.conv_id, stream_id, "", done=False)
        await emit_report_chunk(snapshot.conv_id, stream_id, cached_text, done=False)
        await emit_report_chunk(snapshot.conv_id, stream_id, "", done=True)
        return {"force_report": True, "report_text": cached_text, "tokens": 0}
    prompt = llm.build_report_prompt(
        text,
        results,
        lang,
        attachments=plan.get("attachments") or [],
        max_tokens=plan.get("max_tokens"),
        channel_prompt=channel_prompt,
    )
    prompt += degradation_note
    # Ouverture du buffer streaming côté front : un chunk vide « start » fait
    # apparaître le widget rapport dans l'UI avant même le premier token.
    await emit_report_chunk(snapshot.conv_id, stream_id, "", done=False)
    report_text = ""
    tokens = 0
    try:
        stream_usage: dict = {}
        stream_clean = False  # True seulement si done avec finish_reason="stop"
        async for chunk in llm.complete_stream(
            "report-writing", "low", prompt, lang=lang
        ):
            ctype, ctext = chunk.get("type"), chunk.get("text", "")
            if ctype == "token" and ctext:
                report_text += ctext
                await emit_report_chunk(snapshot.conv_id, stream_id, ctext, done=False)
            elif ctype == "thinking" and ctext:
                # Le raisonnement du rédacteur passe sur un autre stream pour ne pas
                # polluer le buffer du rapport (le front rend les deux séparément).
                await emit_live_thinking(
                    snapshot.conv_id, ctext, f"{stream_id}-think", done=False
                )
            elif ctype == "done":
                stream_usage = chunk.get("usage") or {}
                stream_clean = True
        tokens = (stream_usage.get("prompt_tokens") or 0) + (
            stream_usage.get("completion_tokens") or 0
        )
        # Repli si texte vide OU si le flux ne s'est pas terminé proprement (hoquet
        # vLLM à 80 % → rapport tronqué non-vide passant pour complet — pire cas pour
        # du renseignement). llm.complete_stream lève déjà RuntimeError sur fin
        # non-propre ; cette garde est une défense en profondeur pour tout chemin
        # qui contournerait le wrapper.
        if not report_text.strip() or not stream_clean:
            raise RuntimeError("rédaction LLM: flux vide ou terminaison non-propre")
    except Exception:  # noqa: BLE001
        # L'appel LLM de rédaction a échoué (ReadTimeout, pool indispo, flux tronqué…).
        # On NE laisse PAS la conv coincée en WRITING_REPORT (le sweeper la re-tenterait
        # à l'infini) : synthèse déterministe de repli à partir des sorties déjà
        # collectées → la transition aboutit toujours, la conv passe à DONE.
        log.warning(
            "report: rédaction LLM indisponible → synthèse de repli", exc_info=True
        )
        report_text = llm.build_report_fallback(text, results, lang)
        tokens = 0
        # On pousse le repli en un seul chunk pour que le streaming finisse propre côté front.
        await emit_report_chunk(snapshot.conv_id, stream_id, report_text, done=False)
    # Ferme le buffer côté front : la prochaine source de vérité sera le widget canonique.
    await emit_live_thinking(snapshot.conv_id, "", f"{stream_id}-think", done=True)
    await emit_report_chunk(snapshot.conv_id, stream_id, "", done=True)
    # Checkpoint (A5) : on persiste le rapport final pour un rejeu idempotent.
    await _write_report_checkpoint(checkpoint_id, "report", report_text)
    return {"force_report": True, "report_text": report_text, "tokens": tokens}


async def _prep_compact(snapshot: ConvSnapshot, event: Any, sm) -> dict:
    """Compaction : lit l'historique et demande un résumé au LLM (Phase A, hors transaction).

    Si l'historique est vide (après `compacted_seq`) → retourne `{"summary": "", "tokens": 0}`.
    Sinon → résumé LLM + usage de tokens. Priorité basse (même que `report-writing`).
    """
    async with sm() as s:
        conv_row = await s.get(Conversation, snapshot.conv_id)
        compacted_seq = (conv_row.compacted_seq if conv_row else 0) or 0
        lang = snapshot.lang
        history = await _recent_chat_history(
            s, snapshot.conv_id, limit=50, compacted_seq=compacted_seq
        )
    if not history:
        return {"summary": "", "tokens": 0, "lang": lang}
    prompt = llm.build_compaction_prompt(history, lang)
    summary, usage = await llm.complete_usage(
        "report-writing", "low", prompt, lang=lang
    )
    tokens = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
    return {"summary": summary, "tokens": tokens, "lang": lang}


async def h_compact(ctx: TransitionContext, prepared: dict) -> str:
    """Compaction de contexte : émet un message résumé et pose `compacted_seq`.

    Si pas de résumé (historique vide) → message d'information, aucun changement d'état.
    Sinon : émet un message assistant avec le résumé (préfixé du marqueur 🗜),
    pose `compacted_seq` au seq courant, re-base `context_tokens` au coût du résumé.
    Ne change JAMAIS l'état FSM — la compaction est orthogonale aux transitions.
    """
    lang = (prepared or {}).get("lang") or ctx.conv.lang
    summary = (prepared or {}).get("summary") or ""
    if not summary:
        ctx.emit(
            "message", {"role": "assistant", "text": _msg(lang, "compact_nothing")}
        )
        return ctx.conv.fsm_state

    # Marqueur `kind="compaction"` : le front rend ce message comme une carte
    # repliable formatée (Markdown), distincte d'une bulle de chat normale.
    ctx.emit("message", {"role": "assistant", "text": summary, "kind": "compaction"})
    # Borne de compaction = seq du message résumé qu'on vient d'émettre.
    ctx.conv.compacted_seq = ctx.conv.event_seq
    # Re-base context_tokens au coût du résumé (tokens du prep compact).
    # NOTE : consumers._run_transition N'accumule PAS pour user_compact — c'est h_compact
    # qui pose la valeur finale (le plan précise cette subtilité).
    ctx.conv.context_tokens = (prepared or {}).get("tokens") or 0
    return ctx.conv.fsm_state


# États « au repos » : compaction autorisée.
_COMPACT_REST_STATES = {"IDLE", "DONE", "FAILED", "AWAITING_PLAN"}

LLM_PREP = {
    ("IDLE", "user_input"): _prep_planning,
    ("DONE", "user_input"): _prep_planning,
    ("FAILED", "user_input"): _prep_planning,
    ("AWAITING_PLAN", "user_input"): _prep_planning,
    ("AWAITING_SUBAGENTS", "user_input"): _prep_planning,
    ("AWAITING_PLAN", "plan_submit"): _prep_dispatch,
    ("WRITING_REPORT", "write_report"): _prep_report,
    **{(s, "user_compact"): _prep_compact for s in _COMPACT_REST_STATES},
}


# --------------------------------------------------------------------------
# Phase B — application (mutations DB + émission UI, DANS la transaction fencée)
# --------------------------------------------------------------------------


def _decide_open_proposal(ctx: TransitionContext) -> None:
    """Verrouille le widget plan_proposal encore ouvert (masque ses boutons côté UI)."""
    plan = ctx.conv.plan or {}
    if plan.get("status") == "proposed":
        ctx.emit(
            "widget_update",
            {"widget_id": f"plan-t{plan.get('turn', 1)}", "patch": {"decided": True}},
        )


def _propose_plan(ctx: TransitionContext, prepared: dict) -> str:
    """Construit le plan ordonné, l'émet en widget `plan_proposal` et attend l'analyste.

    Aucun dispatch ici : on stocke le plan en `status="proposed"` et on passe à
    AWAITING_PLAN. L'analyste répondra par `plan_submit` (Approuver/Modifier).
    """
    _decide_open_proposal(ctx)  # ferme une éventuelle proposition précédente
    text = prepared["text"]
    memories = prepared.get("memories", [])
    relevant_ids = set((prepared.get("routing") or {}).get("relevant_memory_ids") or [])
    # Le widget ne montre QUE les mémoires que le routeur LLM a réellement utilisées
    # (cf. memory.py : on lui en envoie 100, il en sélectionne ≤ N via `relevant_memory_ids`).
    memories_used = [m for m in memories if m.get("id") in relevant_ids]
    attachments = prepared.get("attachments", [])
    force_report = bool(prepared.get("force_report"))
    # Traçabilité de la décision de routage (« pourquoi ce plan ? LLM ou repli ? »).
    # Ces métadonnées sont exposées par llm.route_request sous ces clés exactes.
    routing = prepared.get("routing") or {}
    turn = (ctx.conv.plan or {}).get("turn", 0) + 1
    steps = [
        {
            "plan_step_id": st["plan_step_id"],
            "agent_type": st["agent_type"],
            "instruction": st["instruction"],
            "corr_id": None,
            "status": "pending",
        }
        for st in prepared["steps"]
    ]
    # Post-traitement DÉTERMINISTE du chaînage langue AVANT le human-gate : le plan
    # PROPOSÉ à l'analyste contient déjà le traducteur au bon endroit (insertion /
    # réordonnancement), il n'a donc pas à corriger un routage 35B non déterministe.
    steps = _enforce_translation_chain(steps, ctx.conv.lang or "en")
    ctx.conv.plan = {
        "turn": turn,
        "status": "proposed",
        "cursor": -1,
        "text": text,
        "attachments": attachments,
        "steps": steps,
        "force_report": force_report,
        "max_tokens": prepared.get("max_tokens"),
        # Détections géolocalisées de l'alerte → transmises au dispatch (params) puis à l'agent.
        "locations": prepared.get("locations") or [],
        # Décision de routage persistée (audit P9) — répondable depuis la base.
        "router_raw": routing.get("router_raw"),
        "router_source": routing.get("router_source"),
        "router_tokens": routing.get("router_tokens"),
        "prompt_hash": routing.get("prompt_hash"),
    }
    # Widget de rappel mémoire — montre ce qui a été chargé pour cette planif
    # (mémoire LT + convs référencées en @contexte).
    ctx.emit(
        "widget",
        {
            "widget_id": f"memory-t{turn}",
            "widget_type": "memory_recall",
            "data": {
                "query": text,
                "memories": memories_used,
                "count": len(memories_used),
                "context_refs": prepared.get("context_refs", []),
            },
        },
    )
    # Widget de plan proposé — boutons Approuver / Modifier côté UI. Le message texte
    # redondant (« Plan proposé — N étape(s)… ») a été supprimé : le widget porte l'info.
    ctx.emit(
        "widget",
        {
            "widget_id": f"plan-t{turn}",
            "widget_type": "plan_proposal",
            "data": {
                "turn": turn,
                "decided": False,
                "steps": [
                    {
                        "plan_step_id": s["plan_step_id"],
                        "agent_type": s["agent_type"],
                        "instruction": s["instruction"],
                    }
                    for s in steps
                ],
            },
        },
    )
    ctx.emit("status", {"state": "AWAITING_PLAN"})
    return "AWAITING_PLAN"


# Logique pipeline-aware : seul l'orchestrateur connaît l'enchaînement des étapes.
# Quand un agent TRADUCTEUR alimente un consommateur qui n'accepte QUE l'anglais
# (NER), on IMPOSE une instruction de traduction déterministe — on REMPLACE celle du
# routeur, on ne la complète pas.
#
# Pourquoi remplacer plutôt qu'ajouter des hints : le routeur produit souvent
# « Identifier la langue du texte fourni ET le traduire en français ». Or
# « identifier la langue d'un texte » déclenche, côté translator-agent, une réponse en
# code ISO brut (« en ») au lieu d'une traduction (cf. son system prompt). Ajouter une
# cible anglaise par-dessus créait en plus une consigne contradictoire (fr vs en).
# Une instruction unique, sans « identifier la langue » et sans cible ambiguë, évite
# les deux pièges. Hors de ce cas, l'instruction du routeur reste inchangée.
_TRANSLATOR_AGENTS = {"translator-agent"}
_ENGLISH_ONLY_CONSUMERS = {"nlp-agent"}
_EN_TRANSLATION_OVERRIDE = {
    "fr": "Traduis le texte fourni en anglais. Donne uniquement la traduction, sans le texte source.",
    "en": "Translate the provided text into English. Give only the translation, without the source text.",
}


def _enforce_translation_chain(steps: list[dict], lang: str, insert: bool = True) -> list[dict]:
    """Post-traitement DÉTERMINISTE du plan : garantit qu'un traducteur précède
    IMMÉDIATEMENT tout consommateur anglais-only (NER).

    Le routeur 35B connaît la contrainte (`_ENGLISH_ONLY_CONSUMERS` /
    `_TRANSLATOR_AGENTS`) mais ne s'en sert que pour réécrire l'instruction du
    traducteur — jamais pour garantir sa présence/son ordre (mesuré : 0/6 correct,
    4/6 traducteur manquant, 2/6 ordre inversé). On répare par construction, hors
    modèle, AVANT le human-gate :

      - traducteur manquant   → insertion juste avant le consommateur (instruction
        de traduction via `_EN_TRANSLATION_OVERRIDE` selon la langue UI) ;
      - traducteur mal placé   → réordonnancement juste avant le consommateur ;
      - déjà juste avant       → no-op (fonction idempotente).

    Marque chaque consommateur réparé `consumes_upstream=True` : c'est la dépendance
    d'étape exploitée par `_advance_or_finish` (cf. P6) pour NE PAS exécuter l'aval
    sur l'entrée racine quand le producteur a échoué. Réindexe enfin les
    `plan_step_id` en `step-{n}` séquentiels pour rester cohérent avec le reste de
    la FSM (cursor positionnel, schéma `_build_steps`). Mutation in-place + retour.
    """
    override = _EN_TRANSLATION_OVERRIDE.get(lang or "en", _EN_TRANSLATION_OVERRIDE["en"])
    translator_agent = next(iter(_TRANSLATOR_AGENTS))
    i = 0
    while i < len(steps):
        if steps[i].get("agent_type") not in _ENGLISH_ONLY_CONSUMERS:
            i += 1
            continue
        prev = steps[i - 1] if i > 0 else None
        if prev is not None and prev.get("agent_type") in _TRANSLATOR_AGENTS:
            steps[i]["consumes_upstream"] = True  # déjà en place : on flague seulement
            i += 1
            continue
        # Cherche un traducteur « libre » ailleurs dans le plan (ordre inversé /
        # dispersé) — en évitant de voler celui qui sert déjà un autre consommateur.
        tr_idx = next(
            (
                j
                for j, st in enumerate(steps)
                if st.get("agent_type") in _TRANSLATOR_AGENTS
                and not (
                    j + 1 < len(steps)
                    and steps[j + 1].get("agent_type") in _ENGLISH_ONLY_CONSUMERS
                )
            ),
            None,
        )
        if tr_idx is not None:
            translator = steps.pop(tr_idx)
            if tr_idx < i:
                i -= 1  # le consommateur a glissé d'un cran à gauche après le pop
            steps.insert(i, translator)
        elif insert:
            steps.insert(
                i,
                {
                    "plan_step_id": "",  # réindexé en fin de fonction
                    "agent_type": translator_agent,
                    "instruction": override,
                    "tools": {},
                    "corr_id": None,
                    "status": "pending",
                },
            )
        else:
            # `insert=False` (plan MODIFIÉ par l'analyste) : aucun traducteur libre et on
            # ne ré-insère PAS — l'analyste l'a sciemment retiré dans l'éditeur, on respecte
            # sa décision (sinon « supprimer une étape » ne supprime rien et les DEUX agents
            # s'exécutent). Le consommateur tournera sur l'entrée racine, pas en aval.
            i += 1
            continue
        steps[i + 1]["consumes_upstream"] = True  # le consommateur est désormais en i+1
        i += 2
    for n, st in enumerate(steps, 1):
        st["plan_step_id"] = f"step-{n}"
    return steps


def _dispatch_step(ctx: TransitionContext, plan: dict, i: int) -> None:
    """Dispatche l'étape `i` du plan : INSERT Task (outbox) + file de dispatch + widget de suivi."""
    step = plan["steps"][i]
    agent = step["agent_type"]
    corr_id = str(uuid4())
    hard_timeout = AGENT_MAP.get(agent, {}).get("hard_timeout_s", 120)
    # Chaînage PAR RÉFÉRENCE : on transmet à cette étape les artefacts des étapes amont
    # déjà `done` (clé = leur corr_id). Le worker aval recharge le payload structuré
    # COMPLET depuis l'Object Store — fini le résumé tronqué qui perdait sources,
    # localisations et détections à chaque saut.
    input_refs = [
        ArtifactRef(
            artifact_id=s["artifact_id"],
            agent_type=s["agent_type"],
            preview=s.get("preview", ""),
        )
        for s in plan["steps"][:i]
        if s.get("status") == "done" and s.get("artifact_id")
    ]
    # Repli pour les agents A2A externes (qui ne lisent pas l'Object Store) : le bridge
    # lit params["upstream"], on y dépose le preview texte de chaque ref.
    upstream = [
        {"agent": r.agent_type, "text": r.preview} for r in input_refs if r.preview
    ]
    # Traducteur alimentant un NER anglais-only : on IMPOSE une instruction de
    # traduction déterministe (cf. _EN_TRANSLATION_OVERRIDE), à la place de celle du
    # routeur qui demandait « identifier la langue » → réponse en code ISO, pas une trad.
    instruction = step["instruction"]
    downstream_agents = {s["agent_type"] for s in plan["steps"][i + 1 :]}
    if agent in _TRANSLATOR_AGENTS and (downstream_agents & _ENGLISH_ONLY_CONSUMERS):
        instruction = _EN_TRANSLATION_OVERRIDE.get(
            ctx.conv.lang or "en", _EN_TRANSLATION_OVERRIDE["en"]
        )
    params = {
        "text": plan.get("text", ""),
        "tools": step.get("tools", {}),
        "attachments": plan.get("attachments", []),
        "upstream": upstream,
        # Détections géolocalisées de l'alerte (positions fiables, capteur) — rendues en
        # bloc 📍 parsable par a2a_bridge pour que l'agent C2 crée les observations sans clic.
        "locations": plan.get("locations") or [],
    }
    now = _now()
    # INSERT avant publish (outbox light) : published_at reste NULL jusqu'à publication.
    # On persiste aussi les input_refs dans input_payload pour qu'un retry (rebâti depuis
    # task.input_payload) restitue le chaînage par référence à l'identique.
    ctx.session.add(
        Task(
            corr_id=corr_id,
            conv_id=ctx.conv.conv_id,
            plan_step_id=step["plan_step_id"],
            agent_type=agent,
            status="pending",
            instruction=instruction,
            input_payload={
                **params,
                "input_refs": [r.model_dump(mode="json") for r in input_refs],
            },
            dispatched_at=now,
            # En file (status="pending"), le lease ne court PAS encore : il démarrera au
            # premier heartbeat (le lease consumer pose status="running" + lease_until).
            # lease_until IS NULL = « en file, pas encore démarrée » → pas de faux
            # task_timeout(lease_expired) pour une tâche derrière une autre tâche longue.
            lease_until=None,
            published_at=None,
        )
    )
    ctx.pending_dispatches.append(
        TaskDispatch(
            corr_id=corr_id,
            conv_id=ctx.conv.conv_id,
            agent_type=agent,
            plan_step_id=step["plan_step_id"],
            instruction=instruction,
            lang=ctx.conv.lang or "en",
            params=params,
            input_refs=input_refs,
            hard_timeout_s=hard_timeout,
        )
    )
    step["corr_id"] = corr_id
    step["status"] = "dispatched"
    plan["cursor"] = i
    ctx.emit(
        "widget",
        {
            "widget_id": corr_id,
            "widget_type": "task_progress",
            "data": {
                "corr_id": corr_id,
                "agent_type": agent,
                "status": "pending",
                "pct": 0.0,
                "instruction": step["instruction"],
            },
        },
    )


async def _advance_or_finish(ctx: TransitionContext, plan: dict) -> str:
    """Étape terminée : dispatcher la suivante (séquentiel), lancer le rapport, ou échouer."""
    steps = plan.get("steps", [])
    next_i = next(
        (
            i
            for i, st in enumerate(steps)
            if st.get("status") == "pending" and not st.get("corr_id")
        ),
        None,
    )
    if next_i is not None:
        step = steps[next_i]
        # Garde « échec amont » (P6) : un consommateur d'amont (ex. NER anglais-only)
        # ne doit JAMAIS s'exécuter sur l'entrée RACINE si son producteur immédiat
        # (le traducteur, posé par `_enforce_translation_chain`) n'a pas abouti — il
        # traiterait la mauvaise matière (texte source FR/UK au lieu de la traduction)
        # → garantie de langue violée silencieusement, une rivière classée comme
        # personne. On le SAUTE (statut `skipped` + marque `degraded`) au lieu de le
        # dispatcher, et on enchaîne (le skip peut se propager en cascade).
        if step.get("consumes_upstream") and next_i > 0:
            producer = steps[next_i - 1]
            if producer.get("status") != "done":
                step["status"] = "skipped"
                step["degraded"] = "upstream_failed"
                ctx.emit(
                    "widget",
                    {
                        "widget_id": f"{step['plan_step_id']}-skipped",
                        "widget_type": "task_progress",
                        "data": {
                            "corr_id": step["plan_step_id"],
                            "agent_type": step["agent_type"],
                            "status": "skipped",
                            "pct": 1.0,
                            "instruction": step.get("instruction", ""),
                        },
                    },
                )
                ctx.emit(
                    "message",
                    {
                        "role": "assistant",
                        "text": _msg(
                            ctx.conv.lang,
                            "task_skipped_upstream",
                            agent=step["agent_type"].upper(),
                            upstream=producer.get("agent_type", "?").upper(),
                        ),
                    },
                )
                ctx.conv.plan = plan
                return await _advance_or_finish(ctx, plan)
        _dispatch_step(ctx, plan, next_i)
        ctx.conv.plan = plan  # réassignation : JSONB marqué dirty
        ctx.emit("status", {"state": "AWAITING_SUBAGENTS"})
        return "AWAITING_SUBAGENTS"
    ctx.conv.plan = plan
    if any(st.get("status") == "done" for st in steps):
        # Final TOUJOURS rédigé par l'orchestrateur. Deux régimes (cf. `_prep_report`) :
        #  - `force_report`=True  → rapport complet (TipTap streamé) + message « rapport prêt »
        #  - `force_report`=False → synthèse courte directement en réponse au chat
        # Les deux passent par WRITING_REPORT pour réutiliser le même chemin (LLM hors
        # transaction, idempotence par msg_id, recovery via le sweeper).
        turn = plan.get("turn", 1)
        ctx.pending_internal.append(
            WriteReport(
                msg_id=f"write_report:{ctx.conv.conv_id}:{turn}",
                conv_id=ctx.conv.conv_id,
                turn=turn,
            )
        )
        ctx.emit("status", {"state": "WRITING_REPORT"})
        return "WRITING_REPORT"
    ctx.emit(
        "message",
        {"role": "assistant", "text": _msg(ctx.conv.lang, "investigation_failed")},
    )
    ctx.emit("status", {"state": "FAILED"})
    return "FAILED"


async def h_start_investigation(ctx: TransitionContext, prepared: dict) -> str:
    """IDLE/DONE/FAILED/AWAITING_PLAN + user_input : echo, routage, PROPOSITION ou réponse directe.

    Trois branches après le routeur LLM (phase A) :
      1. Registry vide → erreur explicite et FAILED.
      2. `chitchat` (pas d'agent à mobiliser) → réponse directe, DONE (conv réutilisable).
      3. `investigate` → on **propose** un plan (AWAITING_PLAN), on ne dispatche pas encore.
    """
    attach_meta = (getattr(ctx.event, "payload", {}) or {}).get("attachments") or []
    ctx.emit(
        "message", {"role": "user", "text": ctx.event.text, "attachments": attach_meta}
    )
    ctx.emit("status", {"state": "PLANNING"})

    if not AGENT_MAP:
        ctx.emit(
            "message", {"role": "assistant", "text": _msg(ctx.conv.lang, "no_agents")}
        )
        ctx.emit("status", {"state": "FAILED"})
        return "FAILED"

    if not prepared.get("steps"):
        _decide_open_proposal(ctx)
        memories = prepared.get("memories", [])
        relevant_ids = set(
            (prepared.get("routing") or {}).get("relevant_memory_ids") or []
        )
        memories_used = [m for m in memories if m.get("id") in relevant_ids]
        ref_titles = prepared.get("context_refs", [])
        # On émet le widget mémoire dès qu'il y a quelque chose à montrer : la mémoire LT
        # sélectionnée par le routeur OU des convs référencées. C'est ici que la réponse
        # vient du @contexte (Drez) : `memories_used` est vide mais `ref_titles` indique
        # d'où on a tiré la réponse.
        if memories_used or ref_titles:
            ctx.emit(
                "widget",
                {
                    "widget_id": f"memory-chitchat-{ctx.conv.event_seq}",
                    "widget_type": "memory_recall",
                    "data": {
                        "query": ctx.event.text,
                        "memories": memories_used,
                        "count": len(memories_used),
                        "context_refs": ref_titles,
                    },
                },
            )
        routing = prepared.get("routing") or {}
        # Ouverture de widgets pilotée par l'orchestrateur (« ouvre / affiche le
        # widget X ») : chaque clé → un événement spawn_widget que le frontend
        # transforme en addWidget(specId). Le `reply` du routeur sert de confirmation.
        for widget_key in routing.get("widgets") or []:
            ctx.emit("spawn_widget", {"widget": widget_key})
        # Mode personnalisation : si le LLM a extrait des faits à mémoriser, on les
        # propose à l'analyste via un widget de confirmation. L'écriture en BD n'a
        # lieu qu'après clic côté frontend (POST /api/memory), pas ici.
        proposed = routing.get("proposed_memories") or []
        if routing.get("intent") == "personalize" and proposed:
            ctx.emit(
                "widget",
                {
                    "widget_id": f"memory-proposal-{ctx.conv.event_seq}",
                    "widget_type": "memory_proposal",
                    "data": {"proposals": proposed, "decided": False},
                },
            )
        # Repli du `reply` : pour `personalize` sans question explicite, on dit qu'on
        # attend une décision sur le widget ; sinon question de clarification générique.
        reply = (routing.get("reply") or "").strip()
        if not reply:
            reply = _msg(
                ctx.conv.lang,
                "memory_proposed" if proposed else "ask_subject",
            )
        ctx.emit("message", {"role": "assistant", "text": reply})
        ctx.emit("status", {"state": "DONE"})
        return "DONE"

    return _propose_plan(ctx, prepared)


async def h_dispatch_plan(ctx: TransitionContext, prepared: dict) -> str:
    """AWAITING_PLAN + plan_submit : l'analyste a approuvé/modifié → on lance le déroulé séquentiel.

    Le plan final ordonné arrive dans `event.payload["steps"]`. On valide les agents
    contre la registry, on verrouille le widget de proposition, puis on dispatche
    **uniquement la 1re étape** — la suite partira au fil des résultats.
    """
    proposed = ctx.conv.plan or {}
    turn = proposed.get("turn", 1)
    tools_by_agent = prepared.get("tools_by_agent", {})
    steps: list[dict] = []
    for i, st in enumerate(prepared.get("steps") or [], 1):
        agent = st.get("agent_type")
        if agent not in AGENT_MAP:
            continue
        steps.append(
            {
                "plan_step_id": f"step-{i}",
                "agent_type": agent,
                "instruction": (st.get("instruction") or "").strip()
                or llm.default_instruction(
                    proposed.get("text", ""), AGENT_MAP.get(agent, {}), ctx.conv.lang
                ),
                "tools": tools_by_agent.get(agent, {}),
                "corr_id": None,
                "status": "pending",
            }
        )

    # Repose les flags `consumes_upstream` (perdus à la reconstruction depuis le payload
    # submit) dont dépend la garde « échec amont » de `_advance_or_finish`, et réordonne un
    # traducteur encore présent mais mal placé. `insert=False` : on NE ré-insère PAS un
    # traducteur que l'analyste a retiré dans l'éditeur — sinon « supprimer une étape » ne
    # supprime rien et les deux agents s'exécutent. L'enforcement COMPLET (avec insertion)
    # reste appliqué au plan PROPOSÉ (cf. _propose_plan), donc « Approuver » sans éditer
    # garde bien le traducteur. Idempotent.
    steps = _enforce_translation_chain(steps, ctx.conv.lang or "en", insert=False)

    _decide_open_proposal(ctx)
    if not steps:
        ctx.emit(
            "message", {"role": "assistant", "text": _msg(ctx.conv.lang, "empty_plan")}
        )
        ctx.emit("status", {"state": "FAILED"})
        return "FAILED"

    # Le widget plan_proposal a été émis avec les steps PROPOSÉS. Si l'analyste a modifié
    # le plan (suppression / réordonnancement d'étapes), on patche le widget avec les steps
    # RÉELLEMENT dispatchés — sinon une étape retirée reste affichée et reçoit un statut
    # d'exécution par index (corrByIdx), donnant l'illusion qu'un agent supprimé s'exécute.
    ctx.emit(
        "widget_update",
        {
            "widget_id": f"plan-t{turn}",
            "patch": {
                "steps": [
                    {
                        "plan_step_id": st["plan_step_id"],
                        "agent_type": st["agent_type"],
                        "instruction": st["instruction"],
                    }
                    for st in steps
                ]
            },
        },
    )

    plan = {
        "turn": turn,
        "status": "running",
        "cursor": -1,
        "text": proposed.get("text", ""),
        "attachments": proposed.get("attachments", []),
        "steps": steps,
        "force_report": bool(proposed.get("force_report")),
        # Détections géolocalisées de l'alerte : reportées depuis le plan PROPOSÉ (sinon
        # perdues à la reconstruction depuis le payload submit → l'agent C2 ne reçoit pas
        # le bloc 📍 et redemande un placement carte manuel).
        "locations": proposed.get("locations") or [],
    }
    ctx.conv.plan = plan
    # Pas de message « Investigation lancée… » : le widget de suivi de tâche suffit.
    _dispatch_step(ctx, plan, 0)
    ctx.conv.plan = plan
    ctx.emit("status", {"state": "AWAITING_SUBAGENTS"})
    return "AWAITING_SUBAGENTS"


async def h_cancel(ctx: TransitionContext, _prepared: Any) -> str:
    """user_cancel : annule les tâches en vol (le cas échéant) et passe à FAILED.

    Double-Esc côté UI → le gateway publie un UserInput intent=cancel → on tombstone
    chaque tâche non-terminale (les résultats tardifs seront ignorés via `tasks.status`,
    invariant n°9) et on clôt en FAILED. Valable aussi pendant AWAITING_PLAN (aucune
    tâche en vol : on abandonne simplement la proposition).
    """
    _decide_open_proposal(ctx)
    ctx.emit("message", {"role": "assistant", "text": _msg(ctx.conv.lang, "cancelled")})
    corr_ids = [
        s["corr_id"] for s in (ctx.conv.plan or {}).get("steps", []) if s.get("corr_id")
    ]
    if corr_ids:
        rows = (
            (await ctx.session.execute(select(Task).where(Task.corr_id.in_(corr_ids))))
            .scalars()
            .all()
        )
        for task in rows:
            if task.status in ("pending", "running"):
                task.status = "cancelled"
                task.completed_at = _now()
                # P8 : propage l'annulation au worker / bridge A2A pour rendre le pool
                # 35B partagé sans attendre la fin de la tâche (best-effort, cf. helper).
                await _publish_agent_cancel(task.agent_type, task.corr_id)
                ctx.emit(
                    "widget_update",
                    {"widget_id": task.corr_id, "patch": {"status": "cancelled"}},
                )
    ctx.emit("status", {"state": "FAILED"})
    return "FAILED"


_MEMORY_KINDS = {"about_user", "learned_fact"}


async def h_memory_decision(ctx: TransitionContext, _prepared: Any) -> str:
    """memory_decision : crée les LtmMemory pour les `confirm`, patche le widget pour persister.

    Pas de changement d'état FSM : la décision est orthogonale au déroulé de la conv.
    Le `widget_update` porte un dict `decisions[index] = "saved"|"rejected"|"error"` qui
    réhydratera le widget côté frontend à chaque replay/reconnexion.
    """
    payload = getattr(ctx.event, "payload", {}) or {}
    widget_id = str(payload.get("widget_id") or "")
    raw = payload.get("decisions") or []
    if not widget_id or not isinstance(raw, list) or not raw:
        return ctx.conv.fsm_state
    decisions_state: dict[str, str] = {}
    for d in raw:
        if not isinstance(d, dict):
            continue
        try:
            idx = int(d.get("index"))
        except (TypeError, ValueError):
            continue
        action = str(d.get("action") or "").strip()
        if action == "reject":
            decisions_state[str(idx)] = "rejected"
            continue
        if action != "confirm":
            continue
        kind = str(d.get("kind") or "").strip()
        content = str(d.get("content") or "").strip()
        if kind not in _MEMORY_KINDS or not content:
            decisions_state[str(idx)] = "error"
            continue
        # Fait proposé pendant une investigation de salon → mémoire DE LA CELLULE
        # (scope channel_id), sinon mémoire user/globale.
        cid = ctx.conv.conv_id
        channel_id = cid[len("chan-"):] if cid.startswith("chan-") else None
        m = LtmMemory(
            kind=kind,
            content=content[:500],
            user_id=ctx.conv.user_id,
            conv_id=ctx.conv.conv_id,
            channel_id=channel_id,
            embedding=[0.0] * 384,
        )
        ctx.session.add(m)
        decisions_state[str(idx)] = "saved"
    ctx.emit(
        "widget_update",
        {"widget_id": widget_id, "patch": {"decisions": decisions_state}},
    )
    return ctx.conv.fsm_state


async def h_reorient(ctx: TransitionContext, prepared: dict) -> str:
    """AWAITING_SUBAGENTS + user_input (human-in-the-loop) : ré-orienter OU répondre en aparté.

    Le routeur LLM (phase A) a déjà classé la requête :
      - `chitchat` → on N'annule PAS la tâche en vol, on répond à côté et on reste
        en AWAITING_SUBAGENTS.
      - `investigate` → on tombstone la tâche active (invariant n°9) puis on **propose
        un nouveau plan** (AWAITING_PLAN) — la réorientation passe aussi par
        Approuver/Modifier.
    """
    ctx.emit("message", {"role": "user", "text": ctx.event.text})

    if not prepared.get("steps"):
        routing = prepared.get("routing") or {}
        reply = (routing.get("reply") or "").strip() or _msg(
            ctx.conv.lang, "reorient_clarify"
        )
        ctx.emit("message", {"role": "assistant", "text": reply})
        return "AWAITING_SUBAGENTS"

    ctx.emit(
        "message",
        {"role": "assistant", "text": _msg(ctx.conv.lang, "reorient_cancelled")},
    )
    corr_ids = [
        s["corr_id"] for s in (ctx.conv.plan or {}).get("steps", []) if s.get("corr_id")
    ]
    if corr_ids:
        rows = (
            (await ctx.session.execute(select(Task).where(Task.corr_id.in_(corr_ids))))
            .scalars()
            .all()
        )
        for task in rows:
            if task.status in ("pending", "running"):
                task.status = "cancelled"
                task.completed_at = _now()
                # P8 : propage l'annulation au worker / bridge A2A pour rendre le pool
                # 35B partagé sans attendre la fin de la tâche (best-effort, cf. helper).
                await _publish_agent_cancel(task.agent_type, task.corr_id)
                ctx.emit(
                    "widget_update",
                    {"widget_id": task.corr_id, "patch": {"status": "cancelled"}},
                )
    return _propose_plan(ctx, prepared)


async def h_collect_result(ctx: TransitionContext, prepared: Any) -> str:
    """AWAITING_SUBAGENTS + subagent_result : persiste le résultat puis avance dans le plan.

    Cas `input_required` (round-trip A2A) : l'agent réclame un complément à
    l'analyste. On ne clôt PAS l'étape — on la met en attente (`awaiting_input`,
    statut non-terminal ignoré des balayeurs), on mémorise le `context_id` A2A sur
    l'étape pour reprendre la MÊME tâche distante, on affiche la question comme
    message assistant, et on passe en AWAITING_USER_INPUT. La réponse de l'analyste
    (h_answer_input) re-dispatchera l'agent avec ce context_id.
    """
    res: Result = ctx.event
    task = await ctx.session.get(Task, res.corr_id)
    log.debug(
        "h_collect_result: corr=%s status=%s needs_form=%s output_keys=%s",
        res.corr_id[:8],
        res.status,
        (res.output or {}).get("needs_form"),
        list((res.output or {}).keys())[:8],
    )
    if task is None or task.status in TERMINAL or task.status == "awaiting_input":
        # Doublon, résultat d'une tâche déjà annulée, ou déjà en attente d'input → discard.
        return ctx.conv.fsm_state

    if res.status == "input_required":
        question = (res.output or {}).get(
            "question"
        ) or "L'agent demande une précision."
        context_id = (res.output or {}).get("context_id") or ""
        task.status = "awaiting_input"
        task.progress_note = "en attente d'un complément de l'analyste"
        ctx.emit(
            "widget_update",
            {"widget_id": res.corr_id, "patch": {"status": "awaiting_input"}},
        )
        ctx.emit("message", {"role": "assistant", "text": question})
        # Demande de POSITION (ex. agent C2) : spawn la carte sur le tableau en mode
        # placement + une directive (mode=place_point) liée à la tâche en attente. La
        # directive ne porte AUCUN `locations` (sinon marqueur fantôme dans GeoMap).
        geo = (res.output or {}).get("geo_request")
        if isinstance(geo, dict):
            ctx.emit("spawn_widget", {"widget": "map"})
            ctx.emit(
                "widget",
                {
                    "widget_id": f"{res.corr_id}-place",
                    "widget_type": "map",
                    "data": {
                        "mode": "place_point",
                        "prompt": question,
                        "field_label": geo.get("field_label") or "position",
                        "request_id": res.corr_id,
                        "multi": bool(geo.get("multi")),
                    },
                },
            )
        plan = deepcopy(ctx.conv.plan or {})
        for st in plan.get("steps", []):
            if st.get("corr_id") == res.corr_id:
                st["status"] = "awaiting_input"
                st["context_id"] = context_id
        ctx.conv.plan = plan
        ctx.emit("status", {"state": "AWAITING_USER_INPUT"})
        return "AWAITING_USER_INPUT"

    # Retry automatique pour les échecs TRANSITOIRES du bridge A2A (réseau, 5xx, timeout).
    # Même borne attempt_count que pour TaskTimeout dans h_handle_timeout.
    # Les échecs DÉFINITIFS (4xx, mauvaise config, mauvaise matière) ne matchent aucun
    # marqueur de _TRANSIENT_BRIDGE_MARKERS → tombent dans la section normale ci-dessous.
    if (
        res.status == "failed"
        and _is_transient_bridge_failure(res.error)
        and task.attempt_count < settings.max_task_attempts
    ):
        log.info(
            "h_collect_result: échec transitoire '%s' pour %s (tentative %d/%d) → retry",
            (res.error or "")[:80],
            task.corr_id[:8],
            task.attempt_count + 1,
            settings.max_task_attempts,
        )
        _enqueue_task_retry(ctx, task)
        return ctx.conv.fsm_state  # rester en AWAITING_SUBAGENTS

    task.status = res.status
    task.result_payload = res.output
    task.error = res.error
    task.worker_id = res.worker_id
    task.progress_pct = 1.0
    task.completed_at = _now()
    ctx.emit(
        "widget_update",
        {"widget_id": res.corr_id, "patch": {"status": res.status, "pct": 1.0}},
    )
    preview = ""
    if res.status == "done":
        # Extrait texte court pour le chaînage / repli A2A. PAS de validation ici :
        # le strict (extra="forbid") vit à la frontière du WORKER interne (SDK), sur
        # les disciplines qu'on possède. Un agent A2A externe a son propre contrat
        # (Agent Card) — lui appliquer notre schéma interne homonyme serait une erreur
        # de catégorie (c'est ce qui faisait timeout mi_search). On stocke sa sortie telle quelle.
        preview = str(
            (res.output or {}).get("summary") or (res.output or {}).get("text") or ""
        )[:4000]
        # Miroir Object Store : seulement si le producteur ne l'a pas déjà stocké
        # (cas des agents A2A externes via le bridge, qui ne s'auto-stockent pas).
        if not res.artifact_id:
            ctx.pending_artifacts.append(
                ArtifactEnvelope(
                    artifact_id=res.corr_id,
                    agent_type=task.agent_type,
                    schema_name=task.agent_type,
                    payload=dict(res.output or {}),
                    preview=preview,
                )
            )
        ctx.emit(
            "widget",
            {
                "widget_id": f"{res.corr_id}-result",
                "widget_type": (
                    "relegsim_form"
                    if (res.output or {}).get("needs_form")
                    else _RESULT_WIDGET.get(task.agent_type, "markdown")
                ),
                "data": {
                    **res.output,
                    "agent_type": task.agent_type,
                    "corr_id": res.corr_id,
                },
            },
        )
    plan = deepcopy(ctx.conv.plan or {})
    for st in plan.get("steps", []):
        if st.get("corr_id") == res.corr_id:
            st["status"] = res.status
            # On mémorise la RÉFÉRENCE de l'artefact (= corr_id) + un preview texte,
            # pour le chaînage par référence vers les étapes aval (cf. _dispatch_step).
            if res.status == "done":
                st["artifact_id"] = res.corr_id
                st["preview"] = preview

    # Formulaire relegsim ou appui-feu : on met le plan en pause et on attend la soumission du form.
    # Le submit (relegsim_form_submit / fire_support_submit → UserInput) sera routé vers
    # h_answer_input qui re-dispatche directement à l'agent sans passer par le LLM de planning.
    if res.status == "done" and (
        (res.output or {}).get("needs_form") or (res.output or {}).get("needs_fire_form")
    ):
        task.status = "awaiting_input"
        task.progress_note = (
            "en attente des cibles d'appui feu"
            if (res.output or {}).get("needs_fire_form")
            else "en attente des paramètres ReLeGSim"
        )
        for st in plan.get("steps", []):
            if st.get("corr_id") == res.corr_id:
                st["status"] = "awaiting_input"
        ctx.conv.plan = plan
        # Appui feu : on spawn le widget dédié sur le TABLEAU DE BORD (canvas), pas
        # dans le fil — il occupe une grande tuile (carte + liste de cibles). La
        # directive `mode=fire_request` porte le prompt + le request_id (= corr de la
        # tâche en attente) ; le widget lit ça dans les events et renvoie le lot via
        # `fire_support_submit` → h_answer_input reprend CETTE tâche C2 en attente.
        if (res.output or {}).get("needs_fire_form"):
            prompt_af = (
                (res.output or {}).get("text")
                or (res.output or {}).get("summary")
                or "Demande d'appui feu : placez vos cibles sur le tableau de bord, puis envoyez."
            )
            # Pas de message assistant dans le fil : le widget qui apparaît sur le
            # tableau de bord EST le signal (texte « Ouvrez le tableau… » retiré).
            ctx.emit("spawn_widget", {"widget": "fire_support"})
            ctx.emit(
                "widget",
                {
                    "widget_id": f"{res.corr_id}-fire",
                    "widget_type": "fire_support",
                    "data": {
                        "mode": "fire_request",
                        "prompt": prompt_af,
                        "request_id": res.corr_id,
                    },
                },
            )
        ctx.emit(
            "widget_update",
            {"widget_id": res.corr_id, "patch": {"status": "awaiting_input"}},
        )
        ctx.emit("status", {"state": "AWAITING_USER_INPUT"})
        return "AWAITING_USER_INPUT"

    return await _advance_or_finish(ctx, plan)


# Marqueurs de réorientation (P7) : tournures par lesquelles l'analyste change de cap
# plutôt que de répondre à la question pendante d'un agent A2A. Heuristique bon marché
# (aucun appel LLM → on protège le pool 35B partagé) et volontairement CONSERVATRICE :
# au moindre doute, on garde le comportement actuel (re-dispatch), pas de régression.
_REORIENT_MARKERS = (
    "laisse tomber", "laisse beton", "laisse béton", "oublie ", "oublie ça",
    "oublie ca", "annule", "plutot", "plutôt", "au lieu", "a la place", "à la place",
    "change de sujet", "non,", "finalement", "scratch that", "never mind", "nevermind",
    "forget it", "forget that", "instead", "actually", "cancel that", "drop that",
)


def _is_reorientation(text: str) -> bool:
    """Le message est-il une RÉORIENTATION (nouvelle direction) plutôt qu'une réponse ?

    On ne déclenche que sur des tournures explicites de changement de cap ; tout le reste
    (y compris ambigu) est traité comme une réponse → re-dispatch inchangé.
    """
    low = (text or "").strip().lower()
    if not low:
        return False
    return any(m in low for m in _REORIENT_MARKERS)


async def h_defer_during_report(ctx: TransitionContext, _prepared: Any) -> str:
    """WRITING_REPORT + user_input : message analyste pendant la rédaction (fenêtre 1-2 min).

    Sans cette transition, le message était ack'é puis jeté sans écho (P7). On l'échoe,
    on le journalise et on le mémorise pour un traitement post-rédaction (`deferred_inputs`
    du plan), avec un accusé « pris en compte, je termine le rapport ». On reste en
    WRITING_REPORT : plus de perte silencieuse.
    """
    text = (getattr(ctx.event, "text", "") or "").strip()
    ctx.emit("message", {"role": "user", "text": text})
    log.info(
        "defer pendant WRITING_REPORT (conv=%s) : message analyste mémorisé pour le tour suivant",
        ctx.conv.conv_id,
    )
    if text:
        plan = deepcopy(ctx.conv.plan or {})
        deferred = plan.get("deferred_inputs") or []
        deferred.append(text)
        plan["deferred_inputs"] = deferred
        ctx.conv.plan = plan  # réassignation : JSONB marqué dirty
    ctx.emit(
        "message",
        {"role": "assistant", "text": _msg(ctx.conv.lang, "deferred_during_report")},
    )
    return "WRITING_REPORT"


async def h_answer_input(ctx: TransitionContext, prepared: Any) -> str:
    """AWAITING_USER_INPUT + user_input : la réponse de l'analyste reprend la tâche A2A en attente.

    On re-dispatche le MÊME agent avec un NOUVEAU corr_id (un même corr_id serait
    dédupliqué par la fenêtre NATS si la réponse arrive vite) en transportant le
    `context_id` A2A mémorisé → l'agent distant reprend sa tâche là où il l'a
    laissée. Le texte de la réponse devient l'instruction. On revient en
    AWAITING_SUBAGENTS : la suite du plan repart au fil des résultats.
    """
    ctx.emit("message", {"role": "user", "text": ctx.event.text})
    plan = deepcopy(ctx.conv.plan or {})
    steps = plan.get("steps", [])
    idx = next(
        (i for i, st in enumerate(steps) if st.get("status") == "awaiting_input"), None
    )
    if idx is None:
        # Incohérence (aucune étape en attente) → on referme proprement sans dispatch.
        ctx.emit("status", {"state": "AWAITING_SUBAGENTS"})
        return "AWAITING_SUBAGENTS"

    step = steps[idx]
    agent = step["agent_type"]
    context_id = step.get("context_id") or ""

    # P7 : ne pas re-dispatcher AVEUGLÉMENT toute saisie comme réponse à la question
    # pendante. Pour un round-trip A2A (context_id présent — le formulaire ReLeGSim n'en
    # a pas, on ne touche donc pas à ce flux), on classe d'abord : réponse à la question,
    # ou nouvelle direction ? En cas de doute → on retombe sur le re-dispatch ci-dessous.
    if context_id and _is_reorientation(ctx.event.text):
        # Nouvelle direction : on tombstone la tâche A2A en attente (au lieu de lui
        # envoyer un texte hors-sujet) + propagation de l'annulation (P8), puis on
        # repasse en AWAITING_SUBAGENTS d'où la prochaine saisie est routée vers
        # h_reorient (re-planification). Le tombstone DB reste l'autorité.
        pending = (
            await ctx.session.get(Task, step.get("corr_id"))
            if step.get("corr_id")
            else None
        )
        if pending is not None and pending.status not in TERMINAL:
            pending.status = "cancelled"
            pending.completed_at = _now()
            await _publish_agent_cancel(pending.agent_type, pending.corr_id)
            ctx.emit(
                "widget_update",
                {"widget_id": pending.corr_id, "patch": {"status": "cancelled"}},
            )
        step["status"] = "cancelled"
        step.pop("context_id", None)
        ctx.conv.plan = plan
        ctx.emit(
            "message",
            {"role": "assistant", "text": _msg(ctx.conv.lang, "reorient_cancelled")},
        )
        ctx.emit("status", {"state": "AWAITING_SUBAGENTS"})
        return "AWAITING_SUBAGENTS"

    corr_id = str(uuid4())
    hard_timeout = AGENT_MAP.get(agent, {}).get("hard_timeout_s", 120)
    # La réponse devient le texte/instruction ; context_id → reprise A2A.
    params = {
        "text": ctx.event.text,
        "tools": step.get("tools", {}),
        "attachments": plan.get("attachments", []),
        "context_id": context_id,
    }
    now = _now()
    ctx.session.add(
        Task(
            corr_id=corr_id,
            conv_id=ctx.conv.conv_id,
            plan_step_id=step["plan_step_id"],
            agent_type=agent,
            status="pending",
            instruction=ctx.event.text,
            input_payload=params,
            dispatched_at=now,
            # En file (status="pending"), le lease ne court PAS encore : il démarrera au
            # premier heartbeat (le lease consumer pose status="running" + lease_until).
            lease_until=None,
            published_at=None,
        )
    )
    ctx.pending_dispatches.append(
        TaskDispatch(
            corr_id=corr_id,
            conv_id=ctx.conv.conv_id,
            agent_type=agent,
            plan_step_id=step["plan_step_id"],
            instruction=ctx.event.text,
            lang=ctx.conv.lang or "en",
            params=params,
            hard_timeout_s=hard_timeout,
        )
    )
    step["corr_id"] = corr_id
    step["status"] = "dispatched"
    step.pop("context_id", None)  # consommé : porté par le nouveau dispatch
    plan["cursor"] = idx
    ctx.conv.plan = plan
    ctx.emit(
        "widget",
        {
            "widget_id": corr_id,
            "widget_type": "task_progress",
            "data": {
                "corr_id": corr_id,
                "agent_type": agent,
                "status": "pending",
                "pct": 0.0,
                "instruction": step["instruction"],
            },
        },
    )
    ctx.emit("status", {"state": "AWAITING_SUBAGENTS"})
    return "AWAITING_SUBAGENTS"


# ---------------------------------------------------------------------------
# Retry transitoire — partagé par h_handle_timeout et h_collect_result
# ---------------------------------------------------------------------------

# Reasons de TaskTimeout qui sont retentables (transitoires).
# `hard_timeout` : l'agent était trop lent / surchargé → transitoire au même titre que
# `lease_expired`. Borne : même settings.max_task_attempts.
_TRANSIENT_TIMEOUT_REASONS: frozenset[str] = frozenset({"lease_expired", "hard_timeout"})

# Marqueurs détectant un échec transitoire dans Result.error (bridge A2A).
# Conservatif : on ne retente QUE ce qu'on identifie avec certitude comme transitoire.
# Les 4xx (400, 401, 403, 404, 422…), "mauvaise matière", "agent A2A sans card_url"
# ne matchent aucun marqueur → pas de retry.
_TRANSIENT_BRIDGE_MARKERS: tuple[str, ...] = (
    "hard_timeout",            # TimeoutError catchée dans le bridge (asyncio.timeout)
    "502",                     # HTTP 502 Bad Gateway
    "503",                     # HTTP 503 Service Unavailable
    "504",                     # HTTP 504 Gateway Timeout
    "connectionerror",         # aiohttp.ClientConnectorError / ClientOSError
    "clientconnectorerror",    # aiohttp précis
    "clientoserror",           # aiohttp OS-level socket error
    "serverdisconnectederror", # aiohttp ServerDisconnectedError
    "connection refused",
    "connection reset",
    "cannot connect",
    "network unreachable",
    "timeout",                 # générique str-repr TimeoutError / asyncio.TimeoutError
)


def _is_transient_bridge_failure(error: str | None) -> bool:
    """Retourne True si Result.error correspond à un échec TRANSITOIRE du bridge A2A.

    Conservatif : toute erreur non reconnue est traitée comme définitive (pas de retry).
    """
    if not error:
        return False
    lo = error.lower()
    return any(m in lo for m in _TRANSIENT_BRIDGE_MARKERS)


def _enqueue_task_retry(ctx: TransitionContext, task: Task) -> None:
    """Incrémente attempt_count, remet la tâche en pending et enfile un nouveau dispatch.

    Pré-condition (à vérifier par l'appelant) :
        task.attempt_count < settings.max_task_attempts

    Partagé par h_handle_timeout (TaskTimeout transitoire) et h_collect_result
    (Result.status='failed' transitoire issu du bridge A2A).
    """
    task.attempt_count += 1  # compteur d'échecs RÉELS (≠ num_delivered NATS)
    task.status = "pending"
    task.worker_id = None
    task.progress_pct = 0.0
    task.progress_note = ""
    task.dispatched_at = _now()
    # Le retry est re-mis en file : son lease redémarrera à son premier heartbeat.
    # lease_until = NULL → pas de re-timeout fantôme tant qu'aucun worker ne l'a prise.
    task.lease_until = None
    task.published_at = None
    hard_timeout = AGENT_MAP.get(task.agent_type, {}).get("hard_timeout_s", 120)
    # Retry = on rejoue la MÊME étape (même corr_id, mêmes params) — le cursor ne bouge pas.
    # On restitue les input_refs persistées : le chaînage par référence survit au retry.
    saved = dict(task.input_payload or {})
    refs = [ArtifactRef(**r) for r in saved.pop("input_refs", [])]
    ctx.pending_dispatches.append(
        TaskDispatch(
            corr_id=task.corr_id,
            conv_id=ctx.conv.conv_id,
            agent_type=task.agent_type,
            plan_step_id=task.plan_step_id,
            instruction=task.instruction,
            lang=ctx.conv.lang or "en",
            params=saved,
            input_refs=refs,
            hard_timeout_s=hard_timeout,
            # Même corr_id (cursor/fence en dépend), Nats-Msg-Id distinct par tentative :
            # sinon la fenêtre de dédup JetStream avale les retries et max_task_attempts
            # devient un no-op (cf. h_answer_input qui régénère un corr_id pour la même raison).
            msg_id=f"{task.corr_id}:{task.attempt_count}",
        )
    )
    ctx.emit(
        "widget_update",
        {
            "widget_id": task.corr_id,
            "patch": {"status": "retrying", "attempt": task.attempt_count},
        },
    )
    ctx.emit(
        "message",
        {
            "role": "assistant",
            "text": _msg(
                ctx.conv.lang,
                "task_retry",
                agent=task.agent_type.upper(),
                n=task.attempt_count,
                max=settings.max_task_attempts,
            ),
        },
    )


async def h_handle_timeout(ctx: TransitionContext, prepared: Any) -> str:
    """AWAITING_SUBAGENTS + task_timeout : retry de l'étape, sinon échec et avance dans le plan."""
    tt: TaskTimeout = ctx.event
    task = await ctx.session.get(Task, tt.corr_id)
    if task is None or task.status in TERMINAL:
        return ctx.conv.fsm_state  # la tâche s'est terminée entre-temps → drop
    # Les deux reasons de TaskTimeout sont transitoires : lease_expired (heartbeat mort)
    # et hard_timeout (agent trop lent / surchargé). Même borne max_task_attempts.
    can_retry = (
        tt.reason in _TRANSIENT_TIMEOUT_REASONS
        and task.attempt_count < settings.max_task_attempts
    )
    if can_retry:
        _enqueue_task_retry(ctx, task)
        return "AWAITING_SUBAGENTS"
    # Plus de retry : la tâche échoue définitivement.
    task.status = "timeout"
    task.completed_at = _now()
    task.error = f"abandon ({tt.reason}) après {task.attempt_count} tentative(s)"
    ctx.emit(
        "widget_update", {"widget_id": task.corr_id, "patch": {"status": "timeout"}}
    )
    ctx.emit(
        "message",
        {
            "role": "assistant",
            "text": _msg(
                ctx.conv.lang,
                "task_abandoned",
                agent=task.agent_type.upper(),
                reason=tt.reason,
            ),
        },
    )
    plan = deepcopy(ctx.conv.plan or {})
    for st in plan.get("steps", []):
        if st.get("corr_id") == tt.corr_id:
            st["status"] = "timeout"
    return await _advance_or_finish(ctx, plan)


async def h_write_report(ctx: TransitionContext, prepared: dict) -> str:
    """WRITING_REPORT + write_report : clôt le tour par le mot final de l'orchestrateur.

    Deux régimes (cf. `_prep_report`) :
      - `force_report=True`  → widget rapport (TipTap) + message « rapport prêt »
        portant le `report_widget_id` (le front rend un bouton « Télécharger PDF »).
      - `force_report=False` → simple message d'assistant avec la synthèse rédigée.
    Dans tous les cas, l'analyste a TOUJOURS une réponse finale dans le chat.
    """
    turn = (ctx.conv.plan or {}).get("turn", 1)
    fr = (ctx.conv.lang or "en") == "fr"
    if prepared.get("force_report"):
        report_widget_id = f"{ctx.conv.conv_id}:report-t{turn}"
        ctx.emit(
            "widget",
            {
                "widget_id": report_widget_id,
                "widget_type": "report",
                "data": {"text": prepared["report_text"], "turn": turn},
            },
        )
        # Message « rapport prêt » : le front détecte `kind == "report_ready"` et
        # rend un bouton de téléchargement directement dans le chat.
        ctx.emit(
            "message",
            {
                "role": "assistant",
                "kind": "report_ready",
                "text": (
                    "Vous trouverez votre rapport dans le tableau ci-joint."
                    if fr
                    else "You'll find your report in the attached panel."
                ),
                "report_widget_id": report_widget_id,
                "filename": f"rapport-hemicycle-t{turn}.pdf",
            },
        )
    else:
        # Régime « synthèse de chat » : message d'assistant classique. On porte le
        # `stream_id` du bubble streamé (chat-summary-t{turn}) pour que le front purge
        # le buffer live correspondant à l'arrivée du message durable — sinon le bubble
        # éphémère survit et la réponse réapparaît au tour suivant (doublon en bas de chat).
        ctx.emit(
            "message",
            {
                "role": "assistant",
                "text": prepared.get("summary_text") or "",
                "stream_id": f"{ctx.conv.conv_id}:chat-summary-t{turn}",
            },
        )
    # Graphit flow — snapshot du plan pour affichage dans le widget GraphIt.
    plan = ctx.conv.plan or {}
    if plan.get("steps"):
        ctx.emit(
            "widget",
            {
                "widget_id": f"graphit-flow-{ctx.conv.conv_id}",
                "widget_type": "graphit_flow",
                "data": build_graphit_json(plan),
            },
        )
    ctx.emit("status", {"state": "DONE"})
    return "DONE"


async def h_noop(ctx: TransitionContext, prepared: Any) -> str:
    """Événement valide mais hors-séquence dans cet état (tardif/doublon) → drop sans effet."""
    return ctx.conv.fsm_state


TRANSITIONS = {
    ("IDLE", "user_input"): h_start_investigation,
    ("DONE", "user_input"): h_start_investigation,
    ("FAILED", "user_input"): h_start_investigation,
    ("AWAITING_PLAN", "user_input"): h_start_investigation,  # re-proposition
    ("AWAITING_PLAN", "plan_submit"): h_dispatch_plan,
    ("AWAITING_PLAN", "user_cancel"): h_cancel,
    ("AWAITING_SUBAGENTS", "user_input"): h_reorient,
    ("AWAITING_SUBAGENTS", "user_cancel"): h_cancel,
    ("AWAITING_SUBAGENTS", "subagent_result"): h_collect_result,
    ("AWAITING_SUBAGENTS", "task_timeout"): h_handle_timeout,
    # input-required (round-trip A2A) : l'analyste répond → reprise de la tâche.
    ("AWAITING_USER_INPUT", "user_input"): h_answer_input,
    ("AWAITING_USER_INPUT", "user_cancel"): h_cancel,
    ("WRITING_REPORT", "write_report"): h_write_report,
    ("WRITING_REPORT", "user_cancel"): h_cancel,
    # P7 : un message pendant la rédaction n'est plus avalé silencieusement.
    ("WRITING_REPORT", "user_input"): h_defer_during_report,
    # Compaction : autorisée au repos, no-op pendant une investigation active.
    **{(s, "user_compact"): h_compact for s in _COMPACT_REST_STATES},
    ("AWAITING_SUBAGENTS", "user_compact"): h_noop,
    ("WRITING_REPORT", "user_compact"): h_noop,
    ("AWAITING_USER_INPUT", "user_compact"): h_noop,
    # Tardifs/doublons pendant l'attente d'un complément (ex. résultat re-livré).
    ("AWAITING_USER_INPUT", "subagent_result"): h_noop,
    ("AWAITING_USER_INPUT", "task_timeout"): h_noop,
    ("AWAITING_USER_INPUT", "plan_submit"): h_noop,
    # Événements valides mais hors-séquence (tardifs / doublons) → drop explicite.
    ("AWAITING_PLAN", "subagent_result"): h_noop,
    ("AWAITING_PLAN", "task_timeout"): h_noop,
    ("WRITING_REPORT", "subagent_result"): h_noop,
    ("WRITING_REPORT", "task_timeout"): h_noop,
    ("WRITING_REPORT", "plan_submit"): h_noop,
    ("AWAITING_SUBAGENTS", "plan_submit"): h_noop,
    ("DONE", "subagent_result"): h_noop,
    ("DONE", "task_timeout"): h_noop,
    ("DONE", "user_cancel"): h_noop,
    ("DONE", "plan_submit"): h_noop,
    ("IDLE", "subagent_result"): h_noop,
    ("IDLE", "task_timeout"): h_noop,
    ("IDLE", "user_cancel"): h_noop,
    ("IDLE", "plan_submit"): h_noop,
    ("FAILED", "subagent_result"): h_noop,
    ("FAILED", "task_timeout"): h_noop,
    ("FAILED", "user_cancel"): h_noop,
    ("FAILED", "plan_submit"): h_noop,
    # Décision sur un widget memory_proposal — orthogonale à l'état FSM, valide partout.
    **{
        (s, "memory_decision"): h_memory_decision
        for s in (
            "IDLE",
            "DONE",
            "FAILED",
            "AWAITING_PLAN",
            "AWAITING_SUBAGENTS",
            "AWAITING_USER_INPUT",
            "WRITING_REPORT",
        )
    },
}
