"""Gateway Hémicycle — traducteur WebSocket ↔ bus + API REST de surveillance read-only.

Le gateway est un pur traducteur : il transforme les WebSockets en messages sur le
bus et inversement. L'orchestrateur ignore que les WebSockets existent.

Il expose aussi une API REST **read-only** qui alimente les vues Opérations du
dashboard (conversations, tâches, pods, DLQ, bus, LLM) — fenêtre directe sur la
machinerie de résilience.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid as _uuid
from collections import deque
from contextlib import asynccontextmanager
from uuid import uuid4

from datetime import datetime, timezone

import httpx
from elasticsearch import AsyncElasticsearch, NotFoundError as ESNotFoundError
from fastapi import (
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from pydantic import BaseModel
from sqlalchemy import delete, desc, func, select, text, update as sa_update

from hemicycle_shared import nats_io, s3_docs, subjects
from hemicycle_shared.channels import ensure_channels, post_channel_message
from hemicycle_shared.config import settings
from hemicycle_shared.a2a import A2AClient, probe_agent
from hemicycle_shared.db import (
    AgentType,
    Attachment,
    Channel,
    ChannelMessage,
    Conversation,
    Event,
    ExecutionTrace,
    LtmMemory,
    Task,
    Tool,
    Workflow,
    get_sessionmaker,
    init_db,
)
from hemicycle_shared.alert_event import parse_alert_event
from hemicycle_shared.llm_client import complete as llm_complete
from hemicycle_shared.messages import UserInput

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [gateway] %(levelname)s %(message)s"
)
log = logging.getLogger("gateway")
# httpx logge chaque requête en INFO — bruyant avec le poll de /api/health/services
# (1 Hz côté frontend → 2 GET/s vers ES + llm-stub). On le passe à WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)


AGENTS_INDEX = "hemicycle-agents"

# Limite par défaut du serveur NATS (max_payload non surchargé dans nats.conf).
# Un publish qui dépasse cette borne lève une exception côté client ; on refuse
# proprement avant d'essayer pour renvoyer un feedback applicatif au lieu de
# crasher la session WS.
_NATS_MAX_PAYLOAD = 1_048_576  # 1 Mo
# Limite applicative sur le texte brut d'un message utilisateur avant publication
# NATS. L'enveloppe JSON ajoute de l'overhead ; 900 Ko laisse une marge confortable.
_NATS_TEXT_LIMIT = 900_000  # octets (UTF-8 encodé)


class _State:
    """État du process gateway : connexions bus + tails en mémoire pour le dashboard."""

    def __init__(self) -> None:
        self.nc = None
        self.js = None
        self.jsm = None  # JetStream Manager — introspection des streams
        self.pods_kv = None
        self.tools_kv = None  # KV `tools_config` (réplique de la table tools)
        self.locks_kv = None  # KV `locks` (lock par conv) — purgé sur delete conv
        self.sm = None
        self.es: AsyncElasticsearch | None = None  # client ES — indexation des agents
        self.subs: list = []  # garde les abonnements de fond en vie
        self.bus_tail: deque = deque(maxlen=120)  # derniers UIEvent (vue Bus)
        self.dlq_tail: deque = deque(
            maxlen=60
        )  # derniers messages dead-letter (vue DLQ)


state = _State()


async def _retry(coro_factory, what: str, attempts: int = 40):
    """Réessaie une opération d'init le temps que la dépendance soit prête."""
    for _ in range(attempts):
        try:
            return await coro_factory()
        except Exception as e:  # noqa: BLE001 - attente volontaire des erreurs d'init
            log.info("attente de %s (%s)...", what, e)
            await asyncio.sleep(2)
    raise RuntimeError(f"{what} indisponible")


async def _on_bus_event(msg) -> None:
    """Callback du tail Bus : conserve les derniers événements UI tous convs confondus."""
    try:
        state.bus_tail.append(json.loads(msg.data))
    except Exception:
        log.exception("bus tail: message illisible")


async def _on_dlq(msg) -> None:
    """Callback du tail DLQ : conserve les derniers messages morts pour inspection."""
    state.dlq_tail.append(
        {
            "subject": msg.subject,
            "reason": msg.headers.get("X-DLQ-Reason", "?") if msg.headers else "?",
            "orig_subject": msg.headers.get("X-Orig-Subject", "?")
            if msg.headers
            else "?",
            "payload": msg.data.decode(errors="replace")[:500],
        }
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise les connexions (DB, NATS) et lance les tails de fond au démarrage."""
    settings.service_name = "gateway"
    await _retry(lambda: init_db(settings.postgres_dsn), "Postgres")
    state.sm = get_sessionmaker(settings.postgres_dsn)
    # Création idempotente du bucket MinIO de transit — best-effort, ne bloque jamais le démarrage.
    try:
        s3_docs.S3DocClient().ensure_bucket()
    except Exception:  # noqa: BLE001
        log.warning("s3_docs: impossible de créer le bucket de transit (ignoré)")
    # Seed idempotent des channels (hemicycle + état-major G1..G10) : garantit leur
    # existence à chaque démarrage, même sans lancer le job seed. Upsert seul.
    await ensure_channels(state.sm)
    state.nc = await nats_io.connect(f"gateway-{settings.instance_id}")
    state.js = state.nc.jetstream()
    state.jsm = state.nc.jsm()  # introspection JetStream (chantier 5)
    await nats_io.ensure_topology(state.js)
    state.pods_kv = await state.js.key_value(subjects.KV_PODS)
    state.tools_kv = await state.js.key_value(subjects.KV_TOOLS_CONFIG)
    state.locks_kv = await state.js.key_value(subjects.KV_LOCKS)
    # Sync initial du KV `tools_config` depuis Postgres : le gateway est le seul
    # writer du bucket, donc il peut reconstruire l'état à chaque (re)démarrage.
    await _sync_all_tools_kv()
    # Tails de fond (consumers éphémères, no-ack — pur affichage).
    state.subs.append(
        await state.js.subscribe(
            subjects.ORCH_EVENTS_ALL,
            cb=_on_bus_event,
            config=ConsumerConfig(
                deliver_policy=DeliverPolicy.NEW, ack_policy=AckPolicy.NONE
            ),
        )
    )
    state.subs.append(
        await state.js.subscribe(
            "dlq.agent.>",
            cb=_on_dlq,
            config=ConsumerConfig(
                deliver_policy=DeliverPolicy.ALL, ack_policy=AckPolicy.NONE
            ),
        )
    )
    state.es = AsyncElasticsearch(settings.elasticsearch_url, request_timeout=10)
    log.info("gateway prêt")
    yield
    await state.nc.drain()
    await state.es.close()


app = FastAPI(title="Hémicycle Gateway", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# --------------------------------------------------------------------------
# WebSocket : traduction bidirectionnelle WS ↔ bus
# --------------------------------------------------------------------------


@app.websocket("/ws/{conv_id}")
async def ws_endpoint(ws: WebSocket, conv_id: str) -> None:
    """Pont WebSocket d'une conversation : pousse les événements du bus, publie les entrées user."""
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(msg):
        """Événement UI durable (JetStream) → file de sortie WS."""
        await queue.put(("event", msg.data))

    async def on_progress(msg):
        """Progression live (core NATS, éphémère) → file de sortie WS."""
        await queue.put(("progress", msg.data))

    # Événements UI : consumer JetStream éphémère, deliver ALL → le client rejoue
    # tout l'historique de la conv au (re)connect ; le frontend dédoublonne par `seq`.
    ev_sub = await state.js.subscribe(
        subjects.orch_events(conv_id),
        cb=on_event,
        config=ConsumerConfig(
            deliver_policy=DeliverPolicy.ALL, ack_policy=AckPolicy.NONE
        ),
    )
    pr_sub = await state.nc.subscribe(subjects.orch_progress(conv_id), cb=on_progress)
    log.info("WS ouvert pour conv=%s", conv_id)
    try:
        await asyncio.gather(_ws_send(ws, queue), _ws_recv(ws, conv_id))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        log.exception("WS conv=%s : erreur", conv_id)
    finally:
        await ev_sub.unsubscribe()
        await pr_sub.unsubscribe()
        log.info("WS fermé pour conv=%s", conv_id)


@app.websocket("/ws/channel/{channel_id}")
async def ws_channel_endpoint(ws: WebSocket, channel_id: str) -> None:
    """Pont WebSocket d'un channel : pousse les nouveaux messages, accepte les posts.

    Calque léger de `/ws/{conv_id}` mais SANS JetStream : abonnement core NATS
    éphémère (le live seulement). L'historique est chargé par le client via REST
    (`GET /api/channels/{id}/messages`) — pas de replay au (re)connect.
    """
    await ws.accept()
    # Charge le channel une fois : valide l'existence et mémorise `readonly`.
    async with state.sm() as s:
        ch = await s.get(Channel, channel_id)
    if ch is None:
        await ws.close(code=4404)
        return
    readonly = ch.readonly
    queue: asyncio.Queue = asyncio.Queue()

    async def on_message(msg):
        """Nouveau message du bus (déjà sérialisé en JSON) → file de sortie WS."""
        await queue.put(msg.data)

    sub = await state.nc.subscribe(subjects.channel_events(channel_id), cb=on_message)
    log.info("WS channel ouvert pour channel=%s", channel_id)
    try:
        await asyncio.gather(
            _ws_channel_send(ws, queue),
            _ws_channel_recv(ws, channel_id, readonly),
        )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        log.exception("WS channel=%s : erreur", channel_id)
    finally:
        await sub.unsubscribe()
        log.info("WS channel fermé pour channel=%s", channel_id)


async def _ws_channel_send(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Draine la file du bus vers le WebSocket (un message de channel par item).

    Deux types d'événements transitent sur `channel.events.{id}` :
      - un message sérialisé (post analyste / alerte) → `{kind:'message', data}` ;
      - une enveloppe de suppression `{_event:'delete', id}` → `{kind:'delete', id}`,
        pour que tous les clients connectés retirent le message en direct.
    """
    while True:
        data = await queue.get()
        obj = json.loads(data)
        if isinstance(obj, dict) and obj.get("_event") == "delete":
            await ws.send_json({"kind": "delete", "id": obj.get("id")})
        else:
            await ws.send_json({"kind": "message", "data": obj})


async def _ws_channel_recv(ws: WebSocket, channel_id: str, readonly: bool) -> None:
    """Reçoit les posts du client et les persiste + diffuse (sauf channel readonly)."""
    while True:
        msg = await ws.receive_json()
        if msg.get("type") != "post":
            continue
        text_ = (msg.get("text") or "").strip()
        if not text_ or readonly:
            # Garde-fou serveur : un channel en lecture seule (hemicycle) ignore les posts.
            continue
        await post_channel_message(
            state.sm,
            state.nc,
            channel_id=channel_id,
            text=text_,
            author_id=msg.get("author_id", "analyst-1"),
            author_role=msg.get("author_role", "Analyste"),
        )


async def _ws_send(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Draine la file du bus vers le WebSocket."""
    while True:
        kind, data = await queue.get()
        await ws.send_json({"kind": kind, "data": json.loads(data)})


async def _ws_recv(ws: WebSocket, conv_id: str) -> None:
    """Reçoit les messages du client et publie les entrées utilisateur sur le bus.

    La boucle distingue deux catégories d'erreurs :
    - Exception sur ws.receive_json() → déconnexion réseau réelle → break propre.
    - Exception sur le TRAITEMENT d'un message → event {"type":"error"} envoyé au
      client, boucle continuée (le WS n'est PAS fermé pour un seul message fautif).
    """
    while True:
        # ------------------------------------------------------------------ #
        # Réception : toute exception ici est une vraie déconnexion.         #
        # ------------------------------------------------------------------ #
        try:
            msg = await ws.receive_json()
        except (WebSocketDisconnect, RuntimeError):
            break

        # ------------------------------------------------------------------ #
        # Traitement : un message fautif ne doit pas fermer la session.       #
        # ------------------------------------------------------------------ #
        try:
            mtype = msg.get("type")
            user_id = msg.get("user_id", "analyst-1")
            # Langue courante de l'UI (drapeau du header) — bornée à "en"/"fr", défaut "en".
            lang = msg.get("lang") if msg.get("lang") in ("en", "fr") else "fr"
            if mtype == "user_input" and msg.get("text"):
                # Guard taille : refus avant publish NATS pour éviter une exception bus.
                raw_text: str = msg["text"]
                if len(raw_text.encode()) > _NATS_TEXT_LIMIT:
                    await ws.send_json({
                        "type": "error",
                        "detail": (
                            f"Message trop volumineux ({len(raw_text.encode())} o"
                            f" > {_NATS_TEXT_LIMIT} o). "
                            "Utilisez /api/attachments pour les contenus longs, "
                            "puis référencez la pièce jointe."
                        ),
                    })
                    continue
                # Le message peut porter des pièces jointes, un workflow pré-sélectionné,
                # et les bascules thinking (raisonnement live) / rapport (synthèse rédigée).
                payload = {}
                if msg.get("attachments"):
                    payload["attachments"] = msg["attachments"]
                if msg.get("workflow_steps"):
                    payload["workflow_steps"] = msg["workflow_steps"]
                if msg.get("thinking"):
                    payload["thinking"] = True
                if msg.get("force_report"):
                    payload["force_report"] = True
                if msg.get("context_refs"):
                    payload["context_refs"] = msg["context_refs"]
                # Budget tokens configuré par l'analyste dans SettingsView — propagé
                # tel quel vers l'orchestrateur (optionnel, None si absent).
                if msg.get("max_tokens") is not None:
                    try:
                        payload["max_tokens"] = int(msg["max_tokens"])
                    except (ValueError, TypeError):
                        await ws.send_json({
                            "type": "error",
                            "detail": "max_tokens invalide — valeur numérique entière attendue.",
                        })
                        continue
                ui = UserInput(
                    msg_id=str(uuid4()),
                    conv_id=conv_id,
                    user_id=user_id,
                    text=raw_text,
                    lang=lang,
                    payload=payload,
                )
                await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
                log.info(
                    "user_input publié conv=%s (pièces=%d, refs=%d, lang=%s)",
                    conv_id,
                    len(payload.get("attachments", [])),
                    len(payload.get("context_refs", [])),
                    lang,
                )
            elif mtype == "plan_submit":
                # Décision de l'analyste sur le plan proposé (Approuver tel quel / Modifié).
                ui = UserInput(
                    msg_id=str(uuid4()),
                    conv_id=conv_id,
                    user_id=user_id,
                    text="",
                    intent="plan_submit",
                    lang=lang,
                    payload={"steps": msg.get("steps", [])},
                )
                await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
                log.info(
                    "plan_submit publié conv=%s (%d étapes)",
                    conv_id,
                    len(msg.get("steps", [])),
                )
            elif mtype == "cancel":
                # Double-Esc côté UI : on publie un UserInput intent=cancel, l'orch
                # tombstone les tâches en vol et passe la conv à FAILED (cf. h_cancel).
                ui = UserInput(
                    msg_id=str(uuid4()),
                    conv_id=conv_id,
                    user_id=user_id,
                    text="",
                    intent="cancel",
                    lang=lang,
                )
                await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
                log.info("user_cancel publié conv=%s", conv_id)
            elif mtype == "memory_decision":
                # Décision de l'analyste sur un widget memory_proposal (Valider / Rejeter
                # par fait). L'orchestrateur (h_memory_decision) crée les LtmMemory pour
                # les `confirm` et patche le widget pour persister l'état au refresh.
                ui = UserInput(
                    msg_id=str(uuid4()),
                    conv_id=conv_id,
                    user_id=user_id,
                    text="",
                    intent="memory_decision",
                    lang=lang,
                    payload={
                        "widget_id": msg.get("widget_id", ""),
                        "decisions": msg.get("decisions", []),
                    },
                )
                await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
                log.info(
                    "memory_decision publié conv=%s widget=%s (%d décisions)",
                    conv_id,
                    msg.get("widget_id", ""),
                    len(msg.get("decisions", [])),
                )
            elif mtype == "relegsim_form_submit":
                # Soumission du formulaire ReLeGSim : platforms [{lat, lon, platform_type}]
                # + num_runs → converti en texte naturel → republié comme user_input normal
                # pour que l'orchestrateur relance l'agent avec les paramètres complets.
                platforms: list[dict] = msg.get("platforms", [])
                num_runs: int = int(msg.get("num_runs", 10))
                parts = [
                    f"{p.get('platform_type', 'Tank')} at {float(p['lat']):.4f},{float(p['lon']):.4f}"
                    for p in platforms
                    if "lat" in p and "lon" in p
                ]
                text = f"Simulate {num_runs} runs with: {', '.join(parts)}" if parts else ""
                if text:
                    ui = UserInput(
                        msg_id=str(uuid4()),
                        conv_id=conv_id,
                        user_id=user_id,
                        text=text,
                        lang=lang,
                    )
                    await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
                    log.info(
                        "relegsim_form_submit → user_input conv=%s (%d plateformes, %d runs)",
                        conv_id, len(platforms), num_runs,
                    )
            elif mtype == "map_point_submit":
                # Point posé sur la carte (mode placement) → texte naturel labellisé →
                # republié comme user_input normal → h_answer_input reprend la tâche C2 en
                # attente avec ce point. Labels lat=/lon= explicites pour éviter l'inversion.
                lat = msg.get("lat")
                lon = msg.get("lon")
                label = msg.get("field_label") or "position"
                if lat is not None and lon is not None:
                    text = f"📍 {label}: lat={float(lat):.6f}, lon={float(lon):.6f}"
                    ui = UserInput(
                        msg_id=str(uuid4()),
                        conv_id=conv_id,
                        user_id=user_id,
                        text=text,
                        lang=lang,
                    )
                    await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
                    log.info(
                        "map_point_submit → user_input conv=%s (%.5f,%.5f)",
                        conv_id, float(lat), float(lon),
                    )
            elif mtype == "fire_support_submit":
                # Soumission du formulaire appui-feu : requests [{lat, lon, objective_type, effect}]
                # → converti en texte naturel numéroté → republié comme user_input normal pour que
                # l'orchestrateur relance l'agent C2 avec les cibles complètes.
                reqs = msg.get("requests") or []
                lines = []
                for i, r in enumerate(reqs, start=1):
                    try:
                        lat_v = float(r["lat"])
                        lon_v = float(r["lon"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    lines.append(
                        f"{i}) lat={lat_v:.6f}, lon={lon_v:.6f},"
                        f" type={str(r.get('objective_type') or '')},"
                        f" effet={str(r.get('effect') or '')}"
                    )
                if lines:
                    text = f"DEMANDES D'APPUI FEU ({len(lines)}):\n" + "\n".join(lines)
                    ui = UserInput(
                        msg_id=str(uuid4()),
                        conv_id=conv_id,
                        user_id=user_id,
                        text=text,
                        lang=lang,
                    )
                    await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
                    log.info(
                        "fire_support_submit → user_input conv=%s (%d demandes)",
                        conv_id, len(lines),
                    )
            elif mtype == "compact":
                # Compaction de contexte : la FSM (h_compact) résume l'historique, réécrit
                # compacted_seq et re-base context_tokens. Passe par le bus pour respecter
                # le lock + fence (invariant n°3) — le gateway n'écrit jamais en DB.
                ui = UserInput(
                    msg_id=str(uuid4()),
                    conv_id=conv_id,
                    user_id=user_id,
                    text="",
                    intent="compact",
                    lang=lang,
                )
                await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
                log.info("user_compact publié conv=%s", conv_id)
        except Exception as exc:
            log.warning(
                "Erreur traitement message WS conv=%s type=%s: %s",
                conv_id,
                msg.get("type") if isinstance(msg, dict) else "?",
                exc,
                exc_info=True,
            )
            try:
                await ws.send_json({"type": "error", "detail": str(exc)})
            except Exception:
                pass


# --------------------------------------------------------------------------
# API REST read-only — alimente les vues Opérations du dashboard
# --------------------------------------------------------------------------


def _conv_dict(c: Conversation) -> dict:
    """Sérialise une conversation pour l'API, incluant le compteur de tokens de contexte."""
    return {
        "conv_id": c.conv_id,
        "user_id": c.user_id,
        "title": c.title,
        "fsm_state": c.fsm_state,
        "version": c.version,
        "event_seq": c.event_seq,
        "lock_holder": c.lock_holder,
        "lock_expires_at": c.lock_expires_at,
        "plan": c.plan,
        "updated_at": c.updated_at,
        "context_tokens": c.context_tokens or 0,
    }


def _task_dict(t: Task) -> dict:
    """Sérialise une tâche pour l'API."""
    return {
        "corr_id": t.corr_id,
        "conv_id": t.conv_id,
        "agent_type": t.agent_type,
        "plan_step_id": t.plan_step_id,
        "status": t.status,
        "worker_id": t.worker_id,
        "attempt_count": t.attempt_count,
        "progress_pct": t.progress_pct,
        "progress_note": t.progress_note,
        "instruction": t.instruction,
        "dispatched_at": t.dispatched_at,
        "published_at": t.published_at,
        "lease_until": t.lease_until,
        "completed_at": t.completed_at,
        "result_payload": t.result_payload,
        "error": t.error,
    }


@app.get("/health")
async def health() -> dict:
    """Sonde de santé pour docker-compose."""
    return {"status": "ok"}


# URL interne du service imagery-agent. Le proxy ci-dessous expose
# ses routes carte (tile/wmts-info/footprints/preview) au navigateur SANS jamais
# divulguer la clé API imagerie (elle reste côté agent).
_IMAGERY_AGENT_URL = os.environ.get(
    "IMAGERY_AGENT_URL",
    "http://hemicycle-imagery-agent.agents.svc.cluster.local:8600",
).rstrip("/")


@app.get("/api/imagery/{path:path}")
async def proxy_imagery(path: str, request: Request) -> Response:
    """Reverse-proxy GET vers les routes custom de l'imagery-agent.

    Le gabarit de tuile de l'agent est `/api/imagery/tile/<pid>/{z}/{x}/{y}` ;
    le navigateur tape donc cette URL (même origine Hémicycle), et on la relaie au
    Service de l'agent. Transparent : on recopie statut, content-type et
    Cache-Control pour que le cache navigateur des tuiles fonctionne. Lecture seule.
    """
    url = f"{_IMAGERY_AGENT_URL}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.get(url)
    except httpx.HTTPError as exc:
        return Response(content=f"imagery proxy error: {exc}".encode(), status_code=502)
    fwd = {}
    for h in ("Cache-Control", "X-Tile-Source"):
        if h in upstream.headers:
            fwd[h] = upstream.headers[h]
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=fwd,
    )


@app.get("/api/conversations")
async def list_conversations() -> list[dict]:
    """Liste les conversations (récentes d'abord) — vue Conversation State + sélecteur de chat."""
    async with state.sm() as s:
        rows = (
            (
                await s.execute(
                    select(Conversation)
                    # Exclure les conversations de salon (conv_id "chan-<id>") :
                    # elles vivent dans les salons, pas dans l'historique de chat privé.
                    .where(~Conversation.conv_id.like("chan-%"))
                    .order_by(desc(Conversation.updated_at))
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
    return [_conv_dict(c) for c in rows]


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str) -> dict:
    """Détail d'une conversation (état FSM, version, event_seq, lock, plan)."""
    async with state.sm() as s:
        c = await s.get(Conversation, conv_id)
    return _conv_dict(c) if c else {"error": "not_found"}


class _ConvPatch(BaseModel):
    """Payload de renommage d'une conversation (hors-FSM, pas de fence)."""

    title: str


@app.patch("/api/conversations/{conv_id}")
async def rename_conversation(conv_id: str, payload: _ConvPatch) -> dict:
    """Renomme une conversation — écriture ciblée via Core SQL (pas l'ORM) pour ne pas
    heurter le fence `version_id_col` pendant qu'une investigation tourne.
    Le titre est une métadonnée hors-FSM : pas de lock, pas de version increment.
    """
    title = payload.title.strip()
    if not title:
        raise HTTPException(422, "titre vide")
    async with state.sm() as s:
        result = await s.execute(
            sa_update(Conversation)
            .where(Conversation.conv_id == conv_id)
            .values(title=title)
            .returning(Conversation.conv_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, "conversation inconnue")
        await s.commit()
    return {"conv_id": conv_id, "title": title}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str) -> dict:
    """Supprime DURE une conversation et toute sa descendance.

    Refuse si une tâche est encore RUNNING (l'orchestrateur tient le lock).
    Sinon, supprime en transaction : execution_trace + events + tasks + conv.
    Purge aussi le lock NATS KV (best-effort — TTL le supprimerait sinon).
    """
    async with state.sm() as s:
        conv = await s.get(Conversation, conv_id)
        if conv is None:
            raise HTTPException(404, "conversation inconnue")
        # Garde-fou : ne pas supprimer une conv avec une tâche encore en cours.
        running = (
            await s.execute(
                select(func.count())
                .select_from(Task)
                .where(Task.conv_id == conv_id, Task.status == "running")
            )
        ).scalar() or 0
        if running > 0:
            raise HTTPException(409, f"{running} tâche(s) en cours — annule d'abord")
        # Cascade explicite (les FK ne sont pas toutes déclarées).
        await s.execute(delete(ExecutionTrace).where(ExecutionTrace.conv_id == conv_id))
        await s.execute(delete(Event).where(Event.conv_id == conv_id))
        await s.execute(delete(Task).where(Task.conv_id == conv_id))
        await s.execute(delete(Conversation).where(Conversation.conv_id == conv_id))
        await s.commit()
    # Best-effort : purge le lock NATS KV s'il existait encore.
    try:
        await state.locks_kv.delete(conv_id)
    except Exception:  # noqa: BLE001
        pass
    return {"deleted": conv_id}


@app.get("/api/tasks")
async def list_tasks(conv_id: str | None = None) -> list[dict]:
    """Liste les tâches (filtrable par conv) — vue Task Inspector."""
    async with state.sm() as s:
        stmt = select(Task).order_by(desc(Task.dispatched_at)).limit(80)
        if conv_id:
            stmt = (
                select(Task)
                .where(Task.conv_id == conv_id)
                .order_by(desc(Task.dispatched_at))
            )
        rows = (await s.execute(stmt)).scalars().all()
    return [_task_dict(t) for t in rows]


@app.get("/api/events/{conv_id}")
async def list_events(conv_id: str) -> list[dict]:
    """Journal des événements UI d'une conversation (ordonné par seq) — sert au replay."""
    async with state.sm() as s:
        rows = (
            (
                await s.execute(
                    select(Event).where(Event.conv_id == conv_id).order_by(Event.seq)
                )
            )
            .scalars()
            .all()
        )
    return [
        {"seq": e.seq, "type": e.type, "payload": e.payload, "ts": e.ts} for e in rows
    ]


@app.get("/api/execution-trace/{conv_id}")
async def list_execution_trace(conv_id: str) -> list[dict]:
    """Journal de trace d'exécution d'une conversation — sert au replay de la pyramide UI."""
    async with state.sm() as s:
        rows = (
            (
                await s.execute(
                    select(ExecutionTrace)
                    .where(ExecutionTrace.conv_id == conv_id)
                    .order_by(ExecutionTrace.id)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "corr_id": r.corr_id,
            "agent_id": r.agent_id,
            "kind": r.kind,
            "data": r.data,
            "ts": r.ts,
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Channels — salons de discussion de groupe (distincts des conversations FSM)
# --------------------------------------------------------------------------


def _channel_dict(c: Channel) -> dict:
    """Sérialise un Channel pour la liste latérale."""
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "role": c.role,
        "description": c.description,
        "readonly": c.readonly,
        "position": c.position,
    }


@app.get("/api/channels")
async def list_channels() -> list[dict]:
    """Liste les channels ordonnés (hemicycle en tête, puis G1..G10)."""
    async with state.sm() as s:
        rows = (
            (await s.execute(select(Channel).order_by(Channel.position, Channel.id)))
            .scalars()
            .all()
        )
    return [_channel_dict(c) for c in rows]


@app.get("/api/channels/{channel_id}/messages")
async def list_channel_messages(
    channel_id: str, before: int | None = None, limit: int = 50
) -> list[dict]:
    """Historique d'un channel (Postgres), ordonné par `id` croissant.

    Pagination « remonter » : `before` = id strictement supérieur déjà chargé.
    On lit les `limit` plus récents avant ce curseur, puis on renvoie en ordre
    chronologique pour un rendu direct.
    """
    limit = max(1, min(limit, 100))
    async with state.sm() as s:
        stmt = select(ChannelMessage).where(ChannelMessage.channel_id == channel_id)
        if before is not None:
            stmt = stmt.where(ChannelMessage.id < before)
        stmt = stmt.order_by(desc(ChannelMessage.id)).limit(limit)
        rows = (await s.execute(stmt)).scalars().all()
    rows = list(reversed(rows))
    return [
        {
            "id": m.id,
            "channel_id": m.channel_id,
            "author_id": m.author_id,
            "author_role": m.author_role,
            "kind": m.kind,
            "text": m.text,
            "payload": m.payload or {},
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]


class _ChannelPost(BaseModel):
    """Payload de post d'un message dans un channel.

    `kind`/`payload` optionnels servent les actions de message : une RÉPONSE porte
    `payload.reply_to`, un TRANSFERT (post dans un autre channel) porte
    `payload.transfer_from`. Les analystes restent `kind="message"`.
    """

    text: str
    author_id: str = "analyst-1"
    author_role: str = "Analyste"
    kind: str = "message"
    payload: dict = {}


async def _ensure_postable(channel_id: str) -> Channel:
    """Charge le channel et refuse 404 (inconnu) / 403 (lecture seule)."""
    async with state.sm() as s:
        ch = await s.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(404, "channel inconnu")
    if ch.readonly:
        raise HTTPException(403, "channel en lecture seule")
    return ch


@app.post("/api/channels/{channel_id}/messages")
async def post_channel_message_rest(channel_id: str, payload: _ChannelPost) -> dict:
    """Poste un message d'analyste. Garde-fou serveur : 403 si channel en lecture seule.

    Un transfert sans note ajoutée a un `text` vide mais un `payload.transfer_from`
    porteur — on accepte donc un texte vide dès lors que le payload est non vide.
    """
    text_ = payload.text.strip()
    if not text_ and not payload.payload:
        raise HTTPException(422, "message vide")
    await _ensure_postable(channel_id)
    msg = await post_channel_message(
        state.sm,
        state.nc,
        channel_id=channel_id,
        text=text_,
        author_id=payload.author_id,
        author_role=payload.author_role,
        kind=payload.kind or "message",
        payload=payload.payload or None,
    )
    return msg or {"error": "duplicate"}


@app.delete("/api/channels/{channel_id}/messages/{msg_id}")
async def delete_channel_message(channel_id: str, msg_id: int) -> dict:
    """Supprime un message d'un channel et diffuse la suppression en direct.

    Suppression réelle de la ligne (les channels sont un fil append-only sans
    fence ; pas de soft-delete). On publie ensuite une enveloppe `_event=delete`
    sur le bus du channel pour que tous les clients connectés retirent le message.
    """
    async with state.sm() as s:
        res = await s.execute(
            delete(ChannelMessage).where(
                ChannelMessage.id == msg_id,
                ChannelMessage.channel_id == channel_id,
            )
        )
        await s.commit()
    if res.rowcount == 0:
        raise HTTPException(404, "message inconnu")
    await state.nc.publish(
        subjects.channel_events(channel_id),
        json.dumps({"_event": "delete", "id": msg_id}).encode(),
    )
    return {"deleted": msg_id}


class _ChannelPatch(BaseModel):
    """Mise à jour éditable d'un channel — pour l'instant : son prompt système."""

    description: str


@app.patch("/api/channels/{channel_id}")
async def patch_channel(channel_id: str, body: _ChannelPatch) -> dict:
    """Met à jour le prompt système (`description`) d'un channel d'état-major.

    Ce texte oriente la planification d'Hémicycle quand on l'appelle depuis ce salon
    (cf. orchestrateur `_prep_planning`). On refuse le salon système `hemicycle`
    (lecture seule) : son rôle n'est pas une cellule à mandat éditable.
    """
    async with state.sm() as s:
        ch = await s.get(Channel, channel_id)
        if ch is None:
            raise HTTPException(404, "channel inconnu")
        if ch.readonly:
            raise HTTPException(403, "channel non éditable")
        ch.description = body.description.strip()
        await s.commit()
        await s.refresh(ch)
        return _channel_dict(ch)


# --------------------------------------------------------------------------
# /api/alerts — point d'entrée des AUTRES agents (inter-agents, in-cluster)
# --------------------------------------------------------------------------


class _AlertPost(BaseModel):
    """Alerte poussée par un agent externe vers l'orchestrateur Hémicycle.

    Contrairement à `_ChannelPost` (post d'analyste), une alerte :
      - vise par défaut le channel système « hemicycle » (le salon de l'orchestrateur),
        mais peut cibler n'importe quel channel via `channel_id` (ex. g2) ou plusieurs
        via `channel_ids` (fan-out) ;
      - est marquée `kind="alert"` (rendu distinct côté UI) ;
      - est idempotente : `dedup_key` (ou un hash auto sur source+channel+texte)
        garantit qu'un POST rejoué — ou deux agents signalant le même évènement —
        ne crée qu'un seul message par channel (index unique partiel
        uq_channel_messages_dedup).
    """

    text: str | None = None         # corps de l'alerte (optionnel si `event` fourni)
    source: str = "agent"           # id de l'agent émetteur → author_id
    severity: str = "info"          # info | warning | critical — métadonnée d'affichage
    title: str | None = None        # titre court optionnel
    channel_id: str = "hemicycle"     # channel cible unique (rétro-compatibilité)
    channel_ids: list[str] | None = None  # liste de channels cibles (prioritaire sur channel_id)
    dedup_key: str | None = None    # clé d'idempotence explicite (sinon dérivée par channel)
    payload: dict = {}              # métadonnées libres rattachées à l'alerte
    event: dict | None = None       # payload alert-event STAM brut (optionnel)
    summarize: bool = True          # déclencher une investigation Hémicycle (plan) sur les channels staff


async def _trigger_hemicycle_investigation(
    cid: str,
    parsed_or_text: dict | str,
    source: str,
    lang: str = "fr",
) -> None:
    """Déclenche une investigation Hémicycle (PLAN) dans la conversation du salon.

    Au lieu de publier un simple résumé, l'alerte est injectée comme un tour
    utilisateur dans la conversation liée au salon (`conv_id = "chan-<id>"`),
    exactement comme un appel « @hemicycle » d'un analyste : l'orchestrateur
    planifie alors une investigation (plan → accepter/modifier → run) rendue dans
    le fil du salon, plutôt qu'un résumé figé.

    Tourne en tâche de fond (asyncio.create_task) — ne propage jamais d'exception.
    N'émet rien si le contexte d'alerte est vide.
    """
    try:
        locs: list[dict] = []
        if isinstance(parsed_or_text, dict):
            # Construit le contexte depuis le résumé factuel + les positions détectées
            base = parsed_or_text.get("text", "")
            locs = parsed_or_text.get("locations") or []
            locs_str = "; ".join(
                f"{l['name']} ({l.get('note', '')})" for l in locs if l.get("name")
            ) if locs else ""
            context = f"{base}\nPositions : {locs_str}" if locs_str else base
        else:
            context = str(parsed_or_text)
        context = context.strip()
        if not context:
            return

        # On cadre l'alerte comme une directive d'investigation pour que le routeur
        # LLM la classe en `investigate` (→ plan) et non en simple accusé de réception.
        text = (
            f"⚠️ Alerte reçue (source : {source}) :\n{context}\n\n"
            "Analyse cette alerte et propose une investigation appropriée."
        )
        conv_id = f"chan-{cid}"
        ui = UserInput(
            msg_id=str(uuid4()),
            conv_id=conv_id,
            user_id=f"alert:{source}",
            text=text,
            lang=lang,
            # Positions structurées de l'alerte transportées séparément de la prose : elles
            # voyagent jusqu'au dispatch (params["locations"]) puis sont rendues en lignes
            # 📍 parsables pour l'agent C2 → création d'observations sans clic analyste.
            payload={"locations": locs} if locs else {},
        )
        await state.js.publish(subjects.user_input(conv_id), ui.to_bytes())
        log.info("investigation alerte déclenchée conv=%s (source=%s)", conv_id, source)
    except Exception:
        log.exception("Erreur déclenchement investigation alerte pour channel %s", cid)


@app.post("/api/alerts")
async def post_alert(payload: _AlertPost) -> dict:
    """Reçoit une alerte d'un agent et la fan-out dans les channels cibles.

    Endpoint inter-agents : les autres agents POSTent ici quand ils détectent un
    évènement. Fan-out sur `channel_ids` (ou `channel_id` seul pour la rétro-compat).
    Volontairement SANS le garde `readonly` de `_ensure_postable` : les alertes sont
    précisément le seul écrivain légitime du salon système « hemicycle » (lecture seule
    pour les analystes).

    Si `event` (payload STAM alert-event) est fourni, il est normalisé via
    `parse_alert_event` pour en extraire le texte factuel, la sévérité, le titre et
    les positions géo (locations). Sur les channels staff, l'alerte déclenche en plus
    une investigation Hémicycle asynchrone — l'orchestrateur propose un PLAN dans la
    conversation du salon, comme un appel « @hemicycle » (sauf si `summarize=False`).
    """
    # --- 1. Détermination des channels cibles (dédupliqués, ordre préservé) ----------
    raw_channels = payload.channel_ids or [payload.channel_id]
    seen_cids: set[str] = set()
    target_channels: list[str] = []
    for cid in raw_channels:
        if cid and cid not in seen_cids:
            seen_cids.add(cid)
            target_channels.append(cid)

    # --- 2. Normalisation de l'event STAM (si fourni) --------------------------------
    parsed: dict | None = None
    if payload.event:
        parsed = parse_alert_event(payload.event)
        effective_text = (payload.text or "").strip() or parsed["text"]
        severity = parsed["severity"]
        title    = parsed["title"]
        geo: dict | None = {
            "locations": parsed["locations"],
            "polygons":  parsed["polygons"],
        }
    else:
        effective_text = (payload.text or "").strip()
        if not effective_text:
            raise HTTPException(422, "alerte vide")
        severity = payload.severity
        title    = payload.title
        geo      = None

    # --- 3. Validation des channels cibles ------------------------------------------
    # Chargement et vérification d'existence ; capture du kind (system vs staff)
    # pour le filtre résumé LLM. Volontairement hors-transaction (lecture seule).
    channel_kinds: dict[str, str] = {}
    async with state.sm() as s:
        for cid in target_channels:
            ch = await s.get(Channel, cid)
            if ch is None:
                raise HTTPException(404, f"channel inconnu : {cid}")
            channel_kinds[cid] = ch.kind

    # --- 4. Construction du payload enrichi ------------------------------------------
    alert_kind = "site_monitoring" if (geo and geo.get("locations")) else "generic"
    alert_payload: dict = {
        "severity":   severity,
        **(payload.payload or {}),
        "source":     payload.source,
        "alert_kind": alert_kind,
    }
    if title:
        alert_payload["title"] = title
    if geo and geo.get("locations"):
        alert_payload["geo"] = geo

    # --- 5. Fan-out : post dans chaque channel cible ---------------------------------
    results: dict[str, dict] = {}
    for cid in target_channels:
        # Idempotence : clé explicite ou hash stable incluant le channel
        # (le hash intègre `cid` → clé distincte par channel, pas de collision croisée)
        dedup = payload.dedup_key or hashlib.sha1(
            f"{payload.source}|{cid}|{effective_text}".encode()
        ).hexdigest()
        msg = await post_channel_message(
            state.sm,
            state.nc,
            channel_id=cid,
            text=effective_text,
            author_id=payload.source,
            author_role="Alerte",
            kind="alert",
            payload=alert_payload,
            dedup_key=dedup,
        )
        # `None` = doublon dédupliqué → déjà publié
        results[cid] = msg or {"status": "duplicate", "dedup_key": dedup}

    # --- 6. Investigation Hémicycle asynchrone sur les channels staff -------------------
    # Planifiée en tâche de fond : on retourne immédiatement, l'orchestrateur propose
    # ensuite un PLAN dans la conversation du salon (comme un appel « @hemicycle »),
    # au lieu d'un simple résumé figé.
    if payload.summarize:
        alert_ctx: dict | str = parsed if parsed is not None else effective_text
        for cid in target_channels:
            if channel_kinds.get(cid) != "system":
                asyncio.create_task(
                    _trigger_hemicycle_investigation(
                        cid, alert_ctx, payload.source, lang="fr"
                    )
                )

    return {"channels": results}


@app.get("/api/agent-types")
async def list_agent_types() -> list[dict]:
    """Catalogue de l'Agent Registry — vue Fleet."""
    async with state.sm() as s:
        rows = (await s.execute(select(AgentType))).scalars().all()
    return [
        {
            "id": a.id,
            "discipline": a.discipline,
            "description": a.description,
            "when_to_use": a.when_to_use,
            "tools": a.tools,
            "cost_hint": a.cost_hint,
            "latency_p95_s": a.latency_p95_s,
            "hard_timeout_s": a.hard_timeout_s,
        }
        for a in rows
    ]


# --------------------------------------------------------------------------
# /api/workflows — workflows d'investigation pré-définis (composés sur le canvas)
# --------------------------------------------------------------------------


def _workflow_dict(w: Workflow) -> dict:
    """Sérialise un workflow pour l'API."""
    return {
        "id": w.id,
        "name": w.name,
        "description": w.description,
        "user_id": w.user_id,
        "graph": w.graph,
        "steps": w.steps,
        "created_at": w.created_at,
        "updated_at": w.updated_at,
    }


class WorkflowCreate(BaseModel):
    """Payload de création d'un workflow. `graph` = nodes/edges React Flow ; `steps` = déroulé ordonné."""

    name: str
    description: str = ""
    graph: dict = {}
    steps: list = []  # [{agent_type, instruction}, ...] — ordre d'exécution


class WorkflowPatch(BaseModel):
    """Payload de mise à jour partielle d'un workflow."""

    name: str | None = None
    description: str | None = None
    graph: dict | None = None
    steps: list | None = None


@app.get("/api/workflows")
async def list_workflows() -> list[dict]:
    """Liste les workflows enregistrés (récents d'abord)."""
    async with state.sm() as s:
        rows = (
            (await s.execute(select(Workflow).order_by(desc(Workflow.updated_at))))
            .scalars()
            .all()
        )
    return [_workflow_dict(w) for w in rows]


@app.post("/api/workflows")
async def create_workflow(payload: WorkflowCreate) -> dict:
    """Enregistre un nouveau workflow composé sur le canvas."""
    async with state.sm() as s:
        w = Workflow(
            id=str(uuid4()),
            name=payload.name,
            description=payload.description,
            graph=payload.graph,
            steps=payload.steps,
        )
        s.add(w)
        await s.commit()
        await s.refresh(w)
        return _workflow_dict(w)


@app.get("/api/workflows/{workflow_id}")
async def get_workflow(workflow_id: str) -> dict:
    """Détail d'un workflow (pour le rouvrir dans l'éditeur)."""
    async with state.sm() as s:
        w = await s.get(Workflow, workflow_id)
        if w is None:
            raise HTTPException(404, "workflow inconnu")
        return _workflow_dict(w)


@app.patch("/api/workflows/{workflow_id}")
async def patch_workflow(workflow_id: str, payload: WorkflowPatch) -> dict:
    """Met à jour partiellement un workflow."""
    async with state.sm() as s:
        w = await s.get(Workflow, workflow_id)
        if w is None:
            raise HTTPException(404, "workflow inconnu")
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(w, field, value)
        await s.commit()
        await s.refresh(w)
        return _workflow_dict(w)


@app.delete("/api/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str) -> dict:
    """Supprime un workflow."""
    async with state.sm() as s:
        w = await s.get(Workflow, workflow_id)
        if w is None:
            raise HTTPException(404, "workflow inconnu")
        await s.delete(w)
        await s.commit()
    return {"deleted": workflow_id}


# --------------------------------------------------------------------------
# /api/workflows/draft — assistant « Demander à Hémicycle » (LLM ou repli mots-clés)
# --------------------------------------------------------------------------


class WorkflowDraftRequest(BaseModel):
    """Entrée du builder conversationnel : historique du mini-chat + brouillon courant."""

    messages: list[dict] = []  # [{role:"user"|"assistant", text:str}, ...]
    current_steps: list[dict] = []  # [{agent_type, instruction}, ...]
    lang: str = "fr"  # langue de l'UI ("en" | "fr")


def _draft_default_instruction(discipline: str, text: str, lang: str = "en") -> str:
    """Instruction de repli ciblée par discipline (quand le LLM n'en fournit pas), localisée."""
    if lang == "fr":
        label = (discipline or "SPÉCIALISÉ").upper()
        return (
            f"En tant qu'agent {label}, analyse la demande et produis un livrable {label} "
            f"ciblé et actionnable. Demande : « {text} »"
        )
    label = (discipline or "SPECIALIZED").upper()
    return (
        f"As a {label} agent, analyze the request and produce a focused, actionable "
        f"{label} deliverable. Request: “{text}”"
    )


def _draft_keyword_agents(text: str, catalog: list[AgentType]) -> list[str]:
    """Repli déterministe : agents dont le manifest croise les mots-clés de la requête."""
    toks = {t.lower() for t in re.findall(r"[\w\-]+", text or "") if len(t) > 2}
    chosen = []
    for a in catalog:
        haystack = " ".join(
            str(x or "") for x in (a.discipline, a.when_to_use, a.description)
        ).lower()
        if any(tok in haystack for tok in toks):
            chosen.append(a.id)
    return chosen


def _workflow_draft_prompt(
    messages: list[dict],
    current_steps: list[dict],
    catalog: list[AgentType],
    lang: str = "en",
) -> str:
    """Compose le prompt JSON-only de composition de workflow (même contrat que le routeur), localisé."""
    if lang == "fr":
        catalog_desc = (
            "\n".join(
                f"- {a.id} (discipline={a.discipline}): {a.when_to_use or a.description or '(pas de description)'}"
                for a in catalog
            )
            or "(aucun agent enregistré)"
        )
        draft = (
            "\n".join(
                f"{i}. {st.get('agent_type')} — {st.get('instruction') or '(sans instruction)'}"
                for i, st in enumerate(current_steps, 1)
            )
            or "(vide)"
        )
        convo = (
            "\n".join(
                f"{'User' if m.get('role') == 'user' else 'Assistant'}: {(m.get('text') or '').strip()}"
                for m in messages
                if (m.get("text") or "").strip()
            )
            or "(aucun message)"
        )
        return (
            "Tu es l'assistant de composition de WORKFLOWS d'un orchestrateur de renseignement "
            "À partir de la conversation et du brouillon courant, propose un workflow = une "
            "CHAÎNE ORDONNÉE d'agents, et pour CHAQUE agent une `instruction` courte, ACTIONNABLE et "
            "spécifique à sa discipline (extrais entités/lieux/période/objectif ; ne recopie pas la "
            "requête brute). Tu peux amender le brouillon courant (ajouter/retirer/réordonner). "
            "Rédige `reply` et chaque `instruction` EN FRANÇAIS.\n\n"
            f"AGENTS DISPONIBLES :\n{catalog_desc}\n\n"
            f"BROUILLON COURANT (étapes déjà sur le board) :\n{draft}\n\n"
            f"CONVERSATION :\n{convo}\n\n"
            "Réponds STRICTEMENT en JSON valide, sans markdown ni commentaire :\n"
            '{"agents":[{"id":"<id_agent>","instruction":"<mission ciblée>"}, ...],'
            '"reply":"<courte réponse expliquant le workflow proposé>"}'
        )
    catalog_desc = (
        "\n".join(
            f"- {a.id} (discipline={a.discipline}): {a.when_to_use or a.description or '(no description)'}"
            for a in catalog
        )
        or "(no agent registered)"
    )
    draft = (
        "\n".join(
            f"{i}. {st.get('agent_type')} — {st.get('instruction') or '(no instruction)'}"
            for i, st in enumerate(current_steps, 1)
        )
        or "(empty)"
    )
    convo = (
        "\n".join(
            f"{'User' if m.get('role') == 'user' else 'Assistant'}: {(m.get('text') or '').strip()}"
            for m in messages
            if (m.get("text") or "").strip()
        )
        or "(no message)"
    )
    return (
        "You are the WORKFLOW composition assistant of an intelligence orchestrator. "
        "From the conversation and the current draft, propose a workflow = an ORDERED CHAIN of "
        "agents, and for EACH agent a short, ACTIONABLE `instruction` specific to its discipline "
        "(extract entities/places/period/objective; do not copy the raw request). You may amend "
        "the current draft (add/remove/reorder). Write `reply` and every `instruction` IN ENGLISH.\n\n"
        f"AVAILABLE AGENTS:\n{catalog_desc}\n\n"
        f"CURRENT DRAFT (steps already on the board):\n{draft}\n\n"
        f"CONVERSATION:\n{convo}\n\n"
        "Reply STRICTLY in valid JSON, no markdown or comment:\n"
        '{"agents":[{"id":"<agent_id>","instruction":"<focused mission>"}, ...],'
        '"reply":"<short reply explaining the proposed workflow>"}'
    )


@app.post("/api/workflows/draft")
async def draft_workflow(req: WorkflowDraftRequest) -> dict:
    """Génère/amende un workflow à partir d'une description en langage naturel.

    Appelle le pool LLM (même contrat JSON que le routeur de l'orchestrateur). Repli
    déterministe (match mots-clés) si le LLM est HS ou répond du JSON invalide — utile
    en local (llm-stub simulé).
    """
    async with state.sm() as s:
        catalog = (await s.execute(select(AgentType))).scalars().all()
    known = {a.id: a for a in catalog}
    last_user = next(
        (m.get("text", "") for m in reversed(req.messages) if m.get("role") == "user"),
        "",
    )

    lang = req.lang if req.lang in ("en", "fr") else "fr"
    raw = ""
    try:
        raw = await llm_complete(
            "planning",
            "high",
            _workflow_draft_prompt(req.messages, req.current_steps, catalog, lang),
            lang=lang,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("draft workflow : LLM injoignable (%s) — repli mots-clés", e)

    steps: list[dict] = []
    reply = ""
    data = None
    if raw:
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            data = json.loads(m.group(0) if m else raw)
        except Exception:
            data = None
    if isinstance(data, dict) and isinstance(data.get("agents"), list):
        for a in data["agents"]:
            aid = a.get("id") or a.get("agent_type") if isinstance(a, dict) else a
            if aid in known:
                instr = (a.get("instruction") if isinstance(a, dict) else "") or ""
                steps.append(
                    {
                        "agent_type": aid,
                        "instruction": instr.strip()
                        or _draft_default_instruction(
                            known[aid].discipline, last_user, lang
                        ),
                    }
                )
        reply = str(data.get("reply") or "")

    if not steps:
        # Repli : match mots-clés sur le dernier message.
        for aid in _draft_keyword_agents(last_user, catalog):
            steps.append(
                {
                    "agent_type": aid,
                    "instruction": _draft_default_instruction(
                        known[aid].discipline, last_user, lang
                    ),
                }
            )
        if steps:
            chain = " → ".join(s["agent_type"].upper() for s in steps)
            reply = reply or (
                f"Workflow proposé à partir des mots-clés : {chain}."
                if lang == "fr"
                else f"Proposed workflow from keywords: {chain}."
            )
        else:
            reply = reply or (
                "Décris le flow souhaité (sujet, zone, disciplines) — "
                "ex. « surveiller les mouvements logistiques nocturnes secteur Nord »."
                if lang == "fr"
                else "Describe the flow you want (subject, area, disciplines) — "
                "e.g. “monitor nighttime logistics movements in the Northern sector”."
            )

    return {"steps": steps, "reply": reply}


# --------------------------------------------------------------------------
# /api/memory — mémoire interne long-terme (ltm_memory) : voir / ajouter / supprimer
# --------------------------------------------------------------------------

_MEMORY_KINDS = {"about_user", "learned_fact"}


class MemoryCreate(BaseModel):
    """Payload d'ajout d'un fait en mémoire interne (`learned_fact` = monde, `about_user` = analyste).

    `channel_id` (optionnel) rattache le fait à la mémoire d'un salon d'état-major
    (cf. pop-up réglages d'un channel) ; NULL = mémoire globale/utilisateur.
    """

    content: str
    kind: str = "learned_fact"
    user_id: str | None = "analyst-1"
    channel_id: str | None = None


def _memory_dict(m: LtmMemory) -> dict:
    """Sérialise un fait mémoire pour l'API (sans l'embedding, inutile à l'UI)."""
    return {
        "id": m.id,
        "kind": m.kind,
        "content": m.content,
        "conv_id": m.conv_id,
        "user_id": m.user_id,
        "channel_id": m.channel_id,
        "created_at": m.created_at,
    }


@app.get("/api/memory")
async def list_memory(channel_id: str | None = None) -> list[dict]:
    """Liste les faits de la mémoire interne (récents d'abord).

    `channel_id` fourni → mémoire DE CE SALON uniquement (pop-up réglages d'un
    channel). Absent → toute la mémoire (vue Monitoring).
    """
    async with state.sm() as s:
        stmt = select(LtmMemory).order_by(desc(LtmMemory.created_at)).limit(200)
        if channel_id is not None:
            stmt = stmt.where(LtmMemory.channel_id == channel_id)
        rows = (await s.execute(stmt)).scalars().all()
    return [_memory_dict(m) for m in rows]


@app.post("/api/memory")
async def create_memory(payload: MemoryCreate) -> dict:
    """Ajoute un fait en mémoire (kind ∈ {about_user, learned_fact}). Embedding nul en itér. 1."""
    if not payload.content.strip():
        raise HTTPException(422, "contenu vide")
    if payload.kind not in _MEMORY_KINDS:
        raise HTTPException(
            422, f"kind invalide : {payload.kind} (attendu : {sorted(_MEMORY_KINDS)})"
        )
    async with state.sm() as s:
        m = LtmMemory(
            kind=payload.kind,
            content=payload.content.strip(),
            user_id=payload.user_id,
            channel_id=payload.channel_id,
            embedding=[0.0] * 384,
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        return _memory_dict(m)


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: int) -> dict:
    """Supprime un fait de la mémoire interne."""
    async with state.sm() as s:
        m = await s.get(LtmMemory, memory_id)
        if m is None:
            raise HTTPException(404, "fait mémoire inconnu")
        await s.delete(m)
        await s.commit()
    return {"deleted": memory_id}


# --------------------------------------------------------------------------
# /api/attachments — pièces jointes (texte extrait, périmètre v1 : fichiers texte)
# --------------------------------------------------------------------------

_ATTACH_MAX_BYTES = 25_000_000  # 25 Mo bruts acceptés (images, PDF, petites vidéos…)
_ATTACH_MAX_TEXT = 256_000  # 256 Ko de texte conservés (transitent ensuite par le bus)
# Indices de type MIME considérés comme « texte » (extraction directe).
_TEXT_CT_HINTS = (
    "text/",
    "json",
    "xml",
    "csv",
    "yaml",
    "javascript",
    "x-www-form",
    "markdown",
)


def _human_size(n: int) -> str:
    """Taille lisible (o / Ko / Mo)."""
    if n < 1024:
        return f"{n} o"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} Ko"
    return f"{n / 1024 / 1024:.1f} Mo"


def _extract_pdf_text(raw: bytes) -> str | None:
    """Extrait le texte d'un PDF via pypdf — concatène toutes les pages.

    Renvoie None si pypdf n'arrive pas à parser (PDF chiffré, image-only sans OCR,
    binaire corrompu) → l'appelant retombe sur la note descriptive.
    """
    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(raw))
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                pages.append("")
        text = "\n\n".join(p.strip() for p in pages if p.strip())
        return text.replace("\x00", "") or None
    except Exception:  # noqa: BLE001
        log.warning("pypdf: extraction PDF échouée", exc_info=True)
        return None


def _extract_attachment_text(raw: bytes, content_type: str, filename: str) -> str:
    """Extrait un texte exploitable de n'importe quel fichier — jamais d'échec.

    Texte (utf-8 propre) → contenu direct. PDF → pages concaténées via pypdf.
    Autre binaire (image / vidéo / …) → note descriptive. On purge les octets nuls
    (`\\x00`) que Postgres TEXT refuse.
    """
    ct = (content_type or "").lower()
    looks_text = any(h in ct for h in _TEXT_CT_HINTS)
    looks_pdf = "pdf" in ct or filename.lower().endswith(".pdf")
    if looks_pdf:
        pdf_text = _extract_pdf_text(raw)
        if pdf_text:
            return pdf_text[:_ATTACH_MAX_TEXT]
        # Pas de texte exploitable → note descriptive (PDF image-only, chiffré, etc.)
        return (
            f"[PDF attachment « {filename} » — {_human_size(len(raw))}. "
            f"No extractable text (likely image-only or encrypted).]"
        )
    try:
        decoded = raw.decode("utf-8")
        if "\x00" not in decoded:  # vrai texte utf-8 propre
            return decoded[:_ATTACH_MAX_TEXT]
    except UnicodeDecodeError:
        pass
    if looks_text:  # texte déclaré, encodage exotique
        return raw.decode("utf-8", errors="replace").replace("\x00", "")[
            :_ATTACH_MAX_TEXT
        ]
    return (
        f"[Binary attachment « {filename} » — type {content_type or 'unknown'}, "
        f"{_human_size(len(raw))}. Content not extracted.]"
    )


@app.post("/api/attachments")
async def upload_attachment(
    file: UploadFile = File(...), conv_id: str | None = None
) -> dict:
    """Stocke une pièce jointe (tout type). Texte extrait si possible, sinon note descriptive.

    Le contenu sera rechargé par l'orchestrateur au planning et injecté dans le
    dispatch des agents (les workers ne lisent jamais Postgres — invariant n°7).
    """
    raw = await file.read()
    if len(raw) > _ATTACH_MAX_BYTES:
        raise HTTPException(
            413, f"fichier trop volumineux (max {_human_size(_ATTACH_MAX_BYTES)})"
        )
    filename = file.filename or "fichier"
    content_type = file.content_type or "application/octet-stream"
    text_content = _extract_attachment_text(raw, content_type, filename)

    # Poussée des octets bruts vers le bucket MinIO de transit (best-effort).
    # En cas d'échec, s3_uri reste None et l'orchestrateur se rabat sur le texte extrait.
    s3_uri: str | None = None
    object_key: str | None = None
    try:
        result = s3_docs.push_doc(
            filename,
            raw,
            content_type=content_type,
            conv_id=conv_id,
        )
        s3_uri = result["uri"]
        object_key = result["object_key"]
        log.info("pièce jointe poussée vers S3 : %s (%d o)", s3_uri, result["size_bytes"])
    except s3_docs.S3DocError as exc:
        log.warning("s3_docs: push échoué pour '%s' — %s (ignoré)", filename, exc)
    except Exception:  # noqa: BLE001
        log.warning("s3_docs: erreur inattendue lors du push de '%s' (ignoré)", filename, exc_info=True)

    aid = str(uuid4())
    async with state.sm() as s:
        s.add(
            Attachment(
                id=aid,
                conv_id=conv_id,
                filename=filename,
                content_type=content_type,
                size_bytes=len(raw),
                text_content=text_content,
                s3_uri=s3_uri,
                object_key=object_key,
            )
        )
        await s.commit()
    return {
        "id": aid,
        "filename": filename,
        "content_type": file.content_type,
        "size_bytes": len(raw),
        "chars": len(text_content),
        "s3_uri": s3_uri,
    }


@app.get("/api/attachments/{attachment_id}/content")
async def attachment_content(attachment_id: str) -> Response:
    """Renvoie les OCTETS ORIGINAUX d'une pièce jointe pour le lecteur de fichier.

    Source : le bucket de transit (`object_key`) via le s3-controller ; à défaut
    (push bucket jadis échoué → colonnes NULL), on retombe sur le texte extrait.
    Disposition `inline` pour que le navigateur affiche image/PDF directement.
    """
    async with state.sm() as s:
        row = (
            await s.execute(select(Attachment).where(Attachment.id == attachment_id))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "pièce jointe inconnue")

    ctype = row.content_type or "application/octet-stream"
    disp = f'inline; filename="{row.filename}"'

    if row.object_key:
        cli = s3_docs.S3DocClient()
        url = f"{cli.base_url}/downloadFrom/{cli.bucket}?objectKey={row.object_key}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(url)
                r.raise_for_status()
                return Response(content=r.content, media_type=ctype,
                                headers={"Content-Disposition": disp})
        except Exception:  # noqa: BLE001
            log.warning("attachment_content: échec download bucket pour %s (repli texte)",
                        attachment_id, exc_info=True)

    # Repli : texte extrait (octets originaux indisponibles dans le bucket).
    return Response(content=(row.text_content or "").encode("utf-8"),
                    media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": disp})


# --------------------------------------------------------------------------
# /agents — registre enrichi (catalogue + tools + activité + pods)
# --------------------------------------------------------------------------


def _tool_dict(t: Tool) -> dict:
    """Sérialise un outil pour l'API."""
    return {
        "id": t.id,
        "agent_id": t.agent_id,
        "name": t.name,
        "description": t.description,
        "input_schema": t.input_schema,
        "source": t.source,
        "enabled": t.enabled,
        "paused": t.paused,
        "created_at": t.created_at,
    }


async def _publish_tools_kv(agent_id: str) -> None:
    """Réplique l'état des tools d'un agent dans le KV `tools_config` (consommé par l'orchestrateur)."""
    async with state.sm() as s:
        rows = (
            (await s.execute(select(Tool).where(Tool.agent_id == agent_id)))
            .scalars()
            .all()
        )
    cfg = {
        t.name: {"enabled": t.enabled, "paused": t.paused, "source": t.source}
        for t in rows
    }
    try:
        await state.tools_kv.put(agent_id, json.dumps(cfg).encode())
    except Exception:
        log.exception("KV tools_config : échec d'écriture pour %s", agent_id)


async def _sync_all_tools_kv() -> None:
    """Reconstruit le KV `tools_config` depuis Postgres — appelé au démarrage du gateway."""
    async with state.sm() as s:
        agent_ids = (await s.execute(select(AgentType.id))).scalars().all()
    for agent_id in agent_ids:
        await _publish_tools_kv(agent_id)
    log.info("KV tools_config synchronisé pour %d agent(s)", len(agent_ids))


@app.get("/api/agents")
async def list_agents() -> list[dict]:
    """Registre enrichi : par agent → manifest + tools + activité + pods live."""
    async with state.sm() as s:
        agents = (await s.execute(select(AgentType))).scalars().all()
        tools = (await s.execute(select(Tool))).scalars().all()
        # Dernière activité + count tasks 24h, par agent.
        last_act = dict(
            (
                await s.execute(
                    select(Task.agent_type, func.max(Task.completed_at)).group_by(
                        Task.agent_type
                    )
                )
            ).all()
        )
        count_24h = dict(
            (
                await s.execute(
                    select(Task.agent_type, func.count())
                    .where(
                        Task.dispatched_at >= func.now() - text("interval '24 hours'")
                    )
                    .group_by(Task.agent_type)
                )
            ).all()
        )
        # Statut de la tâche la plus récente par agent (DISTINCT ON) → santé Healthy/Error.
        latest_status = dict(
            (
                await s.execute(
                    select(Task.agent_type, Task.status)
                    .distinct(Task.agent_type)
                    .order_by(Task.agent_type, desc(Task.dispatched_at))
                )
            ).all()
        )
    by_agent: dict[str, list[Tool]] = {}
    for t in tools:
        by_agent.setdefault(t.agent_id, []).append(t)
    # Pods live (KV `pods`) — lu depuis le cache TTL partagé (_get_pods_entries).
    pods_by_agent: dict[str, int] = {}
    for value in (await _get_pods_entries()).values():
        try:
            p = json.loads(value)
            pods_by_agent[p.get("agent_id", "?")] = (
                pods_by_agent.get(p.get("agent_id", "?"), 0) + 1
            )
        except Exception:
            pass
    out = []
    for a in agents:
        agent_tools = by_agent.get(a.id, [])
        # Outils PROPRES de l'agent : si l'agent expose un MCP, ses tools issus de
        # ses skills A2A ne sont que des points d'entrée — la vraie surface vit
        # dans le MCP (compté via `mcp_count`). On ne les compte donc pas comme
        # outils. Sans MCP (ex. data-management), les skills SONT les opérations.
        has_mcp = bool(a.mcp_servers)
        own_tools = [t for t in agent_tools if not (has_mcp and t.source == "a2a")]
        pods_live = pods_by_agent.get(a.id, 0)
        # Statut dérivé du réel : `error` si dernière tâche en échec ; sinon `healthy`
        # si des pods répondent (nats) / si l'Agent Card est connue (a2a) ; sinon `idle`.
        if latest_status.get(a.id) in ("failed", "timeout"):
            status = "error"
        elif a.transport == "a2a":
            status = "healthy" if a.agent_card else "idle"
        else:
            status = "healthy" if pods_live > 0 else "idle"
        out.append(
            {
                "id": a.id,
                "discipline": a.discipline,
                "description": a.description,
                "when_to_use": a.when_to_use,
                "cost_hint": a.cost_hint,
                "latency_p95_s": a.latency_p95_s,
                "hard_timeout_s": a.hard_timeout_s,
                "transport": a.transport,
                "card_url": a.card_url,
                "tools": [_tool_dict(t) for t in own_tools],
                "tools_total": len(own_tools),
                "tools_active": sum(1 for t in own_tools if t.enabled and not t.paused),
                "mcp_count": len(a.mcp_servers or []),
                "status": status,
                "pods_live": pods_live,
                "last_activity": last_act.get(a.id),
                "tasks_24h": int(count_24h.get(a.id, 0)),
            }
        )
    return out


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    """Détail d'un agent : manifest + tools + serveurs MCP + snapshot Agent Card."""
    async with state.sm() as s:
        a = await s.get(AgentType, agent_id)
        if a is None:
            raise HTTPException(404, "agent inconnu")
        tools = (
            (await s.execute(select(Tool).where(Tool.agent_id == agent_id)))
            .scalars()
            .all()
        )
    # Cohérent avec /api/agents : on masque les tools "a2a" (points d'entrée de
    # skill) quand l'agent a un MCP — la surface réelle est exposée côté MCP.
    has_mcp = bool(a.mcp_servers)
    own_tools = [t for t in tools if not (has_mcp and t.source == "a2a")]
    return {
        "id": a.id,
        "discipline": a.discipline,
        "description": a.description,
        "when_to_use": a.when_to_use,
        "cost_hint": a.cost_hint,
        "latency_p95_s": a.latency_p95_s,
        "hard_timeout_s": a.hard_timeout_s,
        "transport": a.transport,
        "card_url": a.card_url,
        "agent_card": a.agent_card or None,
        "mcp_servers": a.mcp_servers or [],
        "tools": [_tool_dict(t) for t in own_tools],
    }


# --- CRUD agent_types (catalogue) ---


class AgentTypeCreate(BaseModel):
    """Payload de création d'un agent_type (entrée de l'Agent Registry)."""

    id: str
    discipline: str
    description: str = ""
    when_to_use: str = ""
    input_schema: dict = {}
    output_schema: dict = {}
    tools: list = []  # liste de noms d'outils (déclaratif)
    mcp_servers: list = []
    cost_hint: str = "medium"
    latency_p95_s: float = 30.0
    hard_timeout_s: int = 120
    transport: str = "nats"  # 'nats' (worker interne) | 'a2a' (HTTP)
    card_url: str | None = None  # requis si transport='a2a'
    agent_card: dict | None = None


class AgentTypePatch(BaseModel):
    """Payload de mise à jour partielle d'un agent_type — tous les champs optionnels."""

    discipline: str | None = None
    description: str | None = None
    when_to_use: str | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    tools: list | None = None
    mcp_servers: list | None = None
    cost_hint: str | None = None
    latency_p95_s: float | None = None
    hard_timeout_s: int | None = None
    transport: str | None = None
    card_url: str | None = None
    agent_card: dict | None = None


def _agent_to_es_doc(a: AgentType) -> dict:
    """Convertit un AgentType en document CollectionDTO pour l'index `hemicycle-agents`."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = a.id if a.id.endswith("-agent") else f"{a.id}-agent"
    return {
        "coreName": f"{a.discipline} Agent",
        "coreType": "Agent",
        "coreDataId": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"hemicycle-agent-{a.id}")),
        "coreDataSource": "Hémicycle",
        "coreCreateDate": ts,
        "coreLastUpdateDate": ts,
        "coreMainDate": ts,
        "coreDataURI": f"{settings.agents_base_url}/agents/{slug}",
        "coreProtection": "unprotected",
        "coreDescription": a.description,
        "coreCreator": "hemicycle",
    }


async def _index_agent_es(a: AgentType) -> None:
    """Indexe (ou met à jour) un agent dans ES — best-effort, n'échoue pas l'op Postgres."""
    if state.es is None:
        return
    try:
        doc = _agent_to_es_doc(a)
        # Upsert : préserve coreCreateDate si le doc existe déjà.
        await state.es.update(
            index=AGENTS_INDEX,
            id=a.id,
            body={
                "doc": {k: v for k, v in doc.items() if k != "coreCreateDate"},
                "upsert": doc,
            },
        )
    except Exception:  # noqa: BLE001
        log.warning("ES : échec de l'indexation de l'agent '%s' (non bloquant)", a.id)


async def _deindex_agent_es(agent_id: str) -> None:
    """Supprime un agent de l'index ES — best-effort."""
    if state.es is None:
        return
    try:
        await state.es.delete(index=AGENTS_INDEX, id=agent_id)
    except ESNotFoundError:
        pass  # déjà absent — silencieux
    except Exception:  # noqa: BLE001
        log.warning(
            "ES : échec de la suppression de l'agent '%s' (non bloquant)", agent_id
        )


def _agent_type_dict(a: AgentType) -> dict:
    """Sérialise un AgentType pour l'API."""
    return {
        "id": a.id,
        "discipline": a.discipline,
        "description": a.description,
        "when_to_use": a.when_to_use,
        "input_schema": a.input_schema,
        "output_schema": a.output_schema,
        "tools": a.tools,
        "mcp_servers": a.mcp_servers or [],
        "cost_hint": a.cost_hint,
        "latency_p95_s": a.latency_p95_s,
        "hard_timeout_s": a.hard_timeout_s,
        "transport": a.transport,
        "card_url": a.card_url,
        "agent_card": a.agent_card,
    }


@app.post("/api/agents")
async def create_agent_type(payload: AgentTypeCreate) -> dict:
    """Crée un nouvel agent_type dans l'Agent Registry."""
    async with state.sm() as s:
        if await s.get(AgentType, payload.id) is not None:
            raise HTTPException(409, f"agent_type '{payload.id}' existe déjà")
        a = AgentType(**payload.model_dump())
        s.add(a)
        await s.commit()
        await s.refresh(a)
    await _index_agent_es(a)
    return _agent_type_dict(a)


@app.patch("/api/agents/{agent_id}")
async def patch_agent_type(agent_id: str, payload: AgentTypePatch) -> dict:
    """Met à jour partiellement un agent_type — seuls les champs fournis changent."""
    async with state.sm() as s:
        a = await s.get(AgentType, agent_id)
        if a is None:
            raise HTTPException(404, "agent inconnu")
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(a, field, value)
        await s.commit()
        await s.refresh(a)
    await _index_agent_es(a)
    return _agent_type_dict(a)


@app.delete("/api/agents/{agent_id}")
async def delete_agent_type(agent_id: str) -> dict:
    """Supprime un agent_type et ses outils associés (cascade applicative)."""
    async with state.sm() as s:
        a = await s.get(AgentType, agent_id)
        if a is None:
            raise HTTPException(404, "agent inconnu")
        await s.execute(delete(Tool).where(Tool.agent_id == agent_id))
        await s.delete(a)
        await s.commit()
    await _publish_tools_kv(agent_id)
    await _deindex_agent_es(agent_id)
    return {"deleted": agent_id}


# --- CRUD outils ---


class ToolCreate(BaseModel):
    """Payload de création d'un outil."""

    name: str
    description: str = ""
    input_schema: dict = {}
    source: str = "native"


class ToolPatch(BaseModel):
    """Payload de mise à jour partielle d'un outil (toggle pause/enabled)."""

    enabled: bool | None = None
    paused: bool | None = None
    description: str | None = None


@app.post("/api/agents/{agent_id}/tools")
async def create_tool(agent_id: str, payload: ToolCreate) -> dict:
    """Crée un nouvel outil pour un agent — persiste en Postgres + propage au KV."""
    async with state.sm() as s:
        if await s.get(AgentType, agent_id) is None:
            raise HTTPException(404, "agent inconnu")
        t = Tool(
            agent_id=agent_id,
            name=payload.name,
            description=payload.description,
            input_schema=payload.input_schema,
            source=payload.source,
            enabled=True,
            paused=False,
        )
        s.add(t)
        await s.commit()
        await s.refresh(t)
    await _publish_tools_kv(agent_id)
    return _tool_dict(t)


@app.patch("/api/tools/{tool_id}")
async def patch_tool(tool_id: int, payload: ToolPatch) -> dict:
    """Met à jour un outil (toggle enabled/paused) — propage au KV `tools_config`."""
    async with state.sm() as s:
        t = await s.get(Tool, tool_id)
        if t is None:
            raise HTTPException(404, "outil inconnu")
        if payload.enabled is not None:
            t.enabled = payload.enabled
        if payload.paused is not None:
            t.paused = payload.paused
        if payload.description is not None:
            t.description = payload.description
        await s.commit()
        await s.refresh(t)
        agent_id = t.agent_id
        out = _tool_dict(t)
    await _publish_tools_kv(agent_id)
    return out


@app.delete("/api/tools/{tool_id}")
async def delete_tool(tool_id: int) -> dict:
    """Supprime un outil — propage au KV `tools_config`."""
    async with state.sm() as s:
        t = await s.get(Tool, tool_id)
        if t is None:
            raise HTTPException(404, "outil inconnu")
        agent_id = t.agent_id
        await s.delete(t)
        await s.commit()
    await _publish_tools_kv(agent_id)
    return {"deleted": tool_id}


def _sample_value(type_hint: object) -> object:
    """Valeur d'exemple réaliste pour un champ d'input_schema (str→"test", int→1…)."""
    h = str(type_hint).lower()
    if "bool" in h:
        return True
    if "int" in h:
        return 1
    if "float" in h or "number" in h:
        return 1.0
    if "list" in h or "array" in h:
        return ["test"]
    if "dict" in h or "object" in h:
        return {}
    return "test"


def _sample_args(input_schema: dict | None) -> dict:
    """Synthétise des args réalistes pour exercer un tool sans le faire échouer faute
    de paramètres requis. Gère deux formes : la forme simple `{champ: "type"}` (où
    `"type?"` = optionnel, ignoré) et la forme JSON Schema `{properties, required}`."""
    if not isinstance(input_schema, dict) or not input_schema:
        return {}
    props = input_schema.get("properties")
    if isinstance(props, dict):  # JSON Schema → on ne remplit que le `required`
        required = set(input_schema.get("required") or props.keys())
        return {
            f: _sample_value(
                (spec or {}).get("type", "string")
                if isinstance(spec, dict)
                else "string"
            )
            for f, spec in props.items()
            if f in required
        }
    # Forme simple {champ: "type"} : on saute les optionnels marqués d'un `?`.
    return {
        f: _sample_value(th)
        for f, th in input_schema.items()
        if not (isinstance(th, str) and th.strip().endswith("?"))
    }


@app.post("/api/tools/{tool_id}/test")
async def test_tool(tool_id: int) -> dict:
    """Teste un outil en l'exécutant réellement via l'agent A2A propriétaire : synthétise
    des args réalistes depuis `input_schema` puis demande à l'agent (HTTP sur `card_url`)
    d'exercer le tool. `ok` si la tâche A2A aboutit. Les agents internes (transport NATS)
    ne sont pas joignables directement depuis la gateway → renvoyés `skipped`."""
    import time

    async with state.sm() as s:
        t = await s.get(Tool, tool_id)
        if t is None:
            raise HTTPException(404, "outil inconnu")
        agent = await s.get(AgentType, t.agent_id)
        name, schema = t.name, t.input_schema

    args = _sample_args(schema)
    if agent is None or agent.transport != "a2a" or not agent.card_url:
        return {
            "tool_id": tool_id,
            "name": name,
            "ok": False,
            "skipped": True,
            "args": args,
            "latency_ms": 0.0,
            "detail": "agent interne (transport NATS) — non testable directement depuis la gateway",
        }

    hard_timeout = float(agent.hard_timeout_s or 120)
    base_url = agent.card_url.rsplit("/.well-known/agent-card.json", 1)[0]
    client = A2AClient(base_url, timeout_s=hard_timeout)
    prompt = (
        f"Exécute l'outil `{name}` avec ces paramètres : "
        f"{json.dumps(args, ensure_ascii=False)}. Réponds avec le résultat de l'outil."
    )
    t0 = time.monotonic()
    final: dict | None = None
    try:
        async with asyncio.timeout(hard_timeout):
            async for evt in client.send_and_stream(prompt):
                if evt["event"] == "result":
                    final = evt["data"]
    except TimeoutError:
        return {
            "tool_id": tool_id,
            "name": name,
            "ok": False,
            "args": args,
            "latency_ms": (time.monotonic() - t0) * 1000,
            "detail": f"timeout dépassé ({hard_timeout:.0f}s) — agent silencieux",
        }
    except Exception as ex:  # noqa: BLE001
        return {
            "tool_id": tool_id,
            "name": name,
            "ok": False,
            "args": args,
            "latency_ms": (time.monotonic() - t0) * 1000,
            "detail": f"erreur A2A : {ex}",
        }
    latency_ms = (time.monotonic() - t0) * 1000
    task_state = (final or {}).get("status", {}).get("state") if final else None
    ok = task_state in ("completed", "working")
    return {
        "tool_id": tool_id,
        "name": name,
        "ok": ok,
        "args": args,
        "latency_ms": latency_ms,
        "detail": f"tâche {task_state}"
        if ok
        else f"réponse inattendue : {task_state or 'aucune'}",
    }


@app.get("/api/agents/{agent_id}/mcp/{server_name}/tools")
async def list_mcp_tools(agent_id: str, server_name: str) -> dict:
    """Retourne les outils exposés par un serveur MCP (tools/list avec schémas JSON Schema).

    Appel réel vers le serveur MCP — utilisé par le frontend pour prépopuler
    les formulaires de test et afficher les descriptions des tools.
    """
    from hemicycle_shared.mcp_client import MCPClient
    from hemicycle_shared.manifests import MCPServerRef

    async with state.sm() as sess:
        a = await sess.get(AgentType, agent_id)
        if a is None:
            raise HTTPException(404, "agent inconnu")
        mcp_servers = a.mcp_servers or []

    ref_dict = next((s for s in mcp_servers if s.get("name") == server_name), None)
    if ref_dict is None:
        raise HTTPException(404, f"serveur MCP {server_name!r} inconnu sur cet agent")

    ref = MCPServerRef(**ref_dict)
    client = MCPClient(ref)
    try:
        tools = await client.list_tools()
        return {"server_name": server_name, "tools": tools}
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"impossible de joindre le serveur MCP : {ex}")


@app.post("/api/agents/{agent_id}/mcp/{server_name}/{tool_name}/test")
async def test_mcp_tool(
    agent_id: str,
    server_name: str,
    tool_name: str,
    body: dict = Body(default={}),
) -> dict:
    """Teste un outil MCP : découvre le schéma via tools/list, génère des args sample
    si l'utilisateur n'en fournit pas, puis appelle tools/call sur le vrai serveur MCP."""
    import time
    from hemicycle_shared.mcp_client import MCPClient
    from hemicycle_shared.manifests import MCPServerRef

    async with state.sm() as sess:
        a = await sess.get(AgentType, agent_id)
        if a is None:
            raise HTTPException(404, "agent inconnu")
        mcp_servers = a.mcp_servers or []

    ref_dict = next((s for s in mcp_servers if s.get("name") == server_name), None)
    if ref_dict is None:
        raise HTTPException(404, f"serveur MCP {server_name!r} inconnu sur cet agent")

    ref = MCPServerRef(**ref_dict)
    user_args: dict = body.get("args", {})

    t0 = time.monotonic()
    try:
        client = MCPClient(ref)
        # Récupère le schéma du tool pour générer des args si nécessaire.
        tools_list = await client.list_tools()
        tool_def = next((t for t in tools_list if t.get("name") == tool_name), None)
        schema = (tool_def or {}).get("inputSchema") or {}

        # Si l'utilisateur n'a pas fourni d'args et qu'il y a un schéma, on génère.
        args = user_args if user_args else _sample_args(schema)

        result = await client.call_tool(tool_name, args)
        latency_ms = (time.monotonic() - t0) * 1000
        return {
            "server_name": server_name,
            "tool_name": tool_name,
            "ok": True,
            "args": args,
            "latency_ms": latency_ms,
            "detail": "appel MCP réel (HTTP Streamable)",
            "result": result,
        }
    except Exception as ex:  # noqa: BLE001
        latency_ms = (time.monotonic() - t0) * 1000
        return {
            "server_name": server_name,
            "tool_name": tool_name,
            "ok": False,
            "args": user_args,
            "latency_ms": latency_ms,
            "detail": str(ex),
            "result": None,
        }


# --------------------------------------------------------------------------
# Enrôlement A2A — point d'entrée unique.
#
# Tous les agents (internes ou tiers) vivent dans la même table `agent_types`.
# Pour qu'un agent rejoigne la registry, l'opérateur appelle POST /api/agents/register
# avec l'URL de son Agent Card. Le gateway :
#   1. fetch la card
#   2. exécute `probe_agent` (validation statique + roundtrip + 1 ping/skill)
#   3. refuse si un check est `fail` (gate de qualité)
#   4. projette les champs de la card (+ extensions x-hemicycle-*) dans agent_types
#   5. upsert avec transport='a2a' et stocke le snapshot
# --------------------------------------------------------------------------


class AgentRegister(BaseModel):
    """Payload d'enrôlement — l'Agent Card est fetchée + probée depuis `card_url`."""

    name: str
    card_url: str


def _agent_card_to_fields(card: dict, fallback_url: str) -> dict:
    """Projette une Agent Card A2A v1.0 (+ extensions Hémicycle) en champs agent_types.

    Les métadonnées Hémicycle-spécifiques (when_to_use, mcp_servers, cost/latency
    hints, discipline) sont véhiculées par des extensions namespacées sur la
    card sous le préfixe `x-hemicycle-*`. La card reste 100% A2A-valide — un
    consommateur tiers les ignore proprement.
    """
    skills = card.get("skills") or []
    name = card.get("name") or fallback_url
    description = card.get("description") or ""
    out: dict = {
        "discipline": str(card.get("x-hemicycle-discipline") or name).upper()[:50],
        "description": description,
        "when_to_use": str(
            card.get("x-hemicycle-when-to-use")
            or description
            or "Agent A2A enrôlé dynamiquement."
        ),
        "tools": [s.get("id") or s.get("name") for s in skills if isinstance(s, dict)],
        "mcp_servers": card.get("x-hemicycle-mcp-servers") or [],
    }
    # Hints de routage LLM + timeout — facultatifs côté card, défauts sinon.
    cost = card.get("x-hemicycle-cost-hint")
    if isinstance(cost, str):
        out["cost_hint"] = cost
    p95 = card.get("x-hemicycle-latency-p95-s")
    if isinstance(p95, (int, float)):
        out["latency_p95_s"] = float(p95)
    hto = card.get("x-hemicycle-hard-timeout-s")
    if isinstance(hto, (int, float)):
        out["hard_timeout_s"] = int(hto)
    return out


@app.post("/api/agents/register")
async def register_agent(payload: AgentRegister) -> dict:
    """Enrôle un agent A2A dans la registry — fetch + probe + upsert.

    Refuse l'enrôlement si la card ne passe pas la validation A2A v1.0 (champs
    requis manquants, skills absentes…) ou si le roundtrip de probe échoue. Le
    rapport complet du probe est renvoyé dans la réponse pour diagnostic.
    """
    base_url = payload.card_url.replace("/.well-known/agent-card.json", "").rstrip("/")
    # Probe complet : discover + validation statique + roundtrip + 1 ping/skill.
    try:
        report = await probe_agent(base_url, per_skill=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"probe A2A impossible : {e}")
    if not report.ok:
        fails = [c for c in report.checks if c.status == "fail"]
        raise HTTPException(
            422,
            {
                "error": "agent non conforme A2A v1.0",
                "fails": [{"name": c.name, "detail": c.detail} for c in fails],
                "report": report.to_dict(),
            },
        )
    card_dict = report.agent_card or {}
    fields = _agent_card_to_fields(card_dict, payload.card_url)
    async with state.sm() as s:
        existing = await s.get(AgentType, payload.name)
        if existing is None:
            a = AgentType(
                id=payload.name,
                transport="a2a",
                card_url=payload.card_url,
                agent_card=card_dict,
                **fields,
            )
            s.add(a)
        else:
            existing.transport = "a2a"
            existing.card_url = payload.card_url
            existing.agent_card = card_dict
            for k, v in fields.items():
                setattr(existing, k, v)
            a = existing
        await s.commit()
        await s.refresh(a)
    await _index_agent_es(a)
    # Synchronise les outils déclarés par la card (skills → tools) dans la
    # table `tools`, pour que l'UI les affiche et que `tools_config` les voit.
    declared_tools = fields.get("tools") or []
    async with state.sm() as s:
        existing_tools = (
            (await s.execute(select(Tool).where(Tool.agent_id == payload.name)))
            .scalars()
            .all()
        )
        existing_by_name = {t.name: t for t in existing_tools}
        for tname in declared_tools:
            if not tname or tname in existing_by_name:
                continue
            s.add(
                Tool(
                    agent_id=payload.name,
                    name=tname,
                    description="",
                    input_schema={},
                    source="a2a",
                    enabled=True,
                    paused=False,
                )
            )
        await s.commit()
    await _publish_tools_kv(payload.name)
    return {**_agent_type_dict(a), "probe": report.to_dict()}


@app.post("/api/agents/{agent_id}/probe")
async def probe_registered_agent(agent_id: str) -> dict:
    """Re-exécute le probe A2A sur un agent déjà enrôlé — utile pour vérifier
    la santé après un redéploiement, ou rafraîchir le snapshot de card en base.
    """
    async with state.sm() as s:
        a = await s.get(AgentType, agent_id)
        if a is None or a.transport != "a2a" or not a.card_url:
            raise HTTPException(404, "agent A2A inconnu")
        card_url = a.card_url
    base_url = card_url.replace("/.well-known/agent-card.json", "").rstrip("/")
    try:
        report = await probe_agent(base_url, per_skill=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"probe A2A impossible : {e}")
    # Refresh du snapshot en base si la card est revenue, même si non-ok.
    if report.agent_card:
        async with state.sm() as s:
            a = await s.get(AgentType, agent_id)
            a.agent_card = report.agent_card
            await s.commit()
    return report.to_dict()


def _a2a_reply_text(data: dict | None) -> str:
    """Texte lisible d'un snapshot Task A2A : output.text → artifacts → status.message."""
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


@app.post("/api/agents/{agent_id}/invoke")
async def invoke_agent(agent_id: str, payload: dict = Body(...)) -> dict:
    """Roundtrip A2A direct (test / chat) — ne passe NI par l'orchestrateur NI par
    Postgres : appel HTTP direct à l'agent, aucune conversation persistée. Optionnel :
    `context_id` enchaîne les tours d'une même session (mémoire portée par l'agent).
    `reply` = texte prêt à afficher."""
    text = payload.get("text", "")
    context_id = payload.get("context_id") or None
    async with state.sm() as s:
        a = await s.get(AgentType, agent_id)
        if a is None or a.transport != "a2a" or not a.card_url:
            raise HTTPException(404, "agent A2A inconnu")
        base_url = a.card_url.rsplit("/.well-known/agent-card.json", 1)[0]
        hard_timeout = float(a.hard_timeout_s or 120)
    client = A2AClient(base_url, timeout_s=hard_timeout)
    events: list[dict] = []
    final: dict | None = None
    try:
        async with asyncio.timeout(hard_timeout):
            async for evt in client.send_and_stream(text, context_id=context_id):
                events.append(evt)
                if evt["event"] == "result":
                    final = evt["data"]
    except TimeoutError:
        raise HTTPException(504, f"timeout dépassé ({hard_timeout:.0f}s) — agent silencieux")
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"erreur A2A: {ex}")
    return {
        "events_count": len(events),
        "events": events[:30],
        "result": final,
        "reply": _a2a_reply_text(final),
    }


@app.get("/api/pods")
async def list_pods() -> list[dict]:
    """Liveness des pods sub-agents, lue depuis le bucket KV `pods` (TTL) — vue Fleet."""
    entries = await _get_pods_entries()
    pods = []
    for value in entries.values():
        try:
            pods.append(json.loads(value))
        except Exception:
            pass
    return pods


@app.get("/api/dlq")
async def list_dlq() -> list[dict]:
    """Messages dead-letter récents — vue DLQ."""
    return list(state.dlq_tail)


@app.get("/api/bus")
async def list_bus() -> list[dict]:
    """Tail des événements UI récents (tous convs) — vue Bus."""
    return list(state.bus_tail)


# --------------------------------------------------------------------------
# /api/jetstream/* — introspection JetStream (chantier 5)
# --------------------------------------------------------------------------


@app.get("/api/jetstream/streams")
async def list_streams() -> list[dict]:
    """Liste les streams JetStream avec leurs métriques principales (messages, bytes, seq)."""
    out = []
    for spec in subjects.STREAMS:
        try:
            info = await state.jsm.stream_info(spec["name"])
            out.append(
                {
                    "name": info.config.name,
                    "subjects": list(info.config.subjects),
                    "messages": info.state.messages,
                    "bytes": info.state.bytes,
                    "first_seq": info.state.first_seq,
                    "last_seq": info.state.last_seq,
                    "consumer_count": info.state.consumer_count,
                    "storage": getattr(
                        info.config.storage, "value", str(info.config.storage)
                    ),
                }
            )
        except Exception as e:  # noqa: BLE001
            out.append({"name": spec["name"], "error": str(e)})
    return out


@app.get("/api/jetstream/streams/{name}/consumers")
async def list_consumers(name: str) -> list[dict]:
    """Détaille les consumers (durables + éphémères) attachés à un stream donné."""
    try:
        consumers = await state.jsm.consumers_info(name)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"stream inconnu: {e}")
    out = []
    for c in consumers:
        out.append(
            {
                "name": c.name,
                "stream": c.stream_name,
                "num_pending": c.num_pending,
                "num_ack_pending": c.num_ack_pending,
                "num_redelivered": c.num_redelivered,
                "num_waiting": c.num_waiting,
                "delivered_consumer_seq": getattr(c.delivered, "consumer_seq", None),
                "delivered_stream_seq": getattr(c.delivered, "stream_seq", None),
                "ack_floor_stream_seq": getattr(c.ack_floor, "stream_seq", None),
                "filter_subject": c.config.filter_subject if c.config else None,
                "ack_wait": c.config.ack_wait if c.config else None,
                "max_deliver": c.config.max_deliver if c.config else None,
            }
        )
    return out


@app.get("/api/jetstream/streams/{name}/messages")
async def peek_messages(name: str, limit: int = 20) -> list[dict]:
    """Peek-only sur les `limit` derniers messages d'un stream (lecture, pas d'ack)."""
    try:
        info = await state.jsm.stream_info(name)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"stream inconnu: {e}")
    last_seq = info.state.last_seq
    if last_seq == 0:
        return []
    start_seq = max(1, last_seq - limit + 1)
    out = []
    for seq in range(start_seq, last_seq + 1):
        try:
            raw = await state.jsm.get_msg(name, seq=seq)
            try:
                payload = json.loads(raw.data.decode())
            except Exception:
                payload = raw.data.decode(errors="replace")[:300]
            out.append(
                {"seq": seq, "subject": raw.subject, "ts": raw.time, "payload": payload}
            )
        except Exception:
            continue  # message effacé / hors fenêtre
    return out


@app.get("/api/llm")
async def llm_stats() -> dict:
    """État du LLM. Le pool `llm-stub` a été retiré : Hémicycle appelle désormais le
    vLLM (qwen) en direct via le framework pydantic-ai (`hemicycle_shared.llm_client`).
    Plus de files à priorité/slots à exposer — réponse statique pour les appelants."""
    return {"mode": "direct", "framework": "pydantic-ai", "model": settings.llm_model}


_pods_cache: dict = {"ts": 0.0, "value": None}
_PODS_CACHE_TTL = 3.0  # secondes — au plus une lecture KV `pods` par TTL


async def _get_pods_entries() -> dict:
    """Retourne les entrées du bucket KV `pods` avec un cache TTL de 3 s.

    Remplace les appels directs à `nats_io.kv_entries(state.pods_kv)` dans
    `list_agents`, `list_pods` et `health_services` : chacun créait un consumer
    éphémère NATS distinct à chaque requête (source des 158 consumers accumulés).
    Désormais un seul `kv_entries()` au plus toutes les 3 s est partagé entre les
    trois call sites. En cas d'erreur, le cache périmé est retourné tel quel.
    """
    import time

    now = time.monotonic()
    if _pods_cache["value"] is not None and now - _pods_cache["ts"] < _PODS_CACHE_TTL:
        return _pods_cache["value"]
    try:
        entries = await nats_io.kv_entries(state.pods_kv)
    except Exception:  # noqa: BLE001
        return _pods_cache["value"] or {}
    _pods_cache["ts"] = now
    _pods_cache["value"] = entries
    return entries


_health_cache: dict = {"ts": 0.0, "value": None}


@app.get("/api/health/services")
async def health_services() -> list[dict]:
    """Statut agrégé des services de la stack — alimente le panneau du header.

    Vérifie postgres, NATS, Elasticsearch, LLM stub, et les 3 sub-agents via le
    bucket KV `pods` (heartbeats à TTL). Résultat caché 3 s pour éviter de
    spammer les services à chaque tick du header (1 Hz côté frontend).
    """
    import time

    now = time.monotonic()
    if _health_cache["value"] is not None and now - _health_cache["ts"] < 3.0:
        return _health_cache["value"]

    out: list[dict] = []

    # Postgres : SELECT 1 via le sessionmaker existant.
    try:
        async with state.sm() as s:
            await s.execute(text("SELECT 1"))
        out.append({"name": "postgres", "category": "infra", "status": "up"})
    except Exception as e:  # noqa: BLE001
        out.append(
            {
                "name": "postgres",
                "category": "infra",
                "status": "down",
                "detail": str(e)[:120],
            }
        )

    # NATS : la connexion porte un flag.
    if state.nc and state.nc.is_connected:
        out.append({"name": "nats", "category": "infra", "status": "up"})
    else:
        out.append(
            {
                "name": "nats",
                "category": "infra",
                "status": "down",
                "detail": "disconnected",
            }
        )

    # Elasticsearch + LLM stub : GET sur un endpoint qui existe (sinon ES répond
    # 200 sur "/" mais llm-stub répond 404 → spam INFO toutes les 5s sans valeur).
    # ES "/" peut mettre ~4 s à répondre sur le cluster partagé (le coordinating
    # node interroge l'état du cluster) → l'ancien timeout de 2 s le marquait à
    # tort "down". On laisse 6 s, et on sonde les deux EN PARALLÈLE (clients
    # indépendants) pour qu'une lenteur ES n'allonge pas la réponse du panneau ni
    # ne perturbe la sonde llm-stub.
    async def _probe_http(name: str, url: str, probe_path: str, category: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.get(url + probe_path)
            return {
                "name": name,
                "category": category,
                "status": "up" if r.status_code < 500 else "down",
            }
        except Exception as e:  # noqa: BLE001
            return {
                "name": name,
                "category": category,
                "status": "down",
                "detail": str(e)[:120],
            }

    out.extend(
        await asyncio.gather(
            _probe_http("elasticsearch", settings.elasticsearch_url, "/", "infra"),
        )
    )

    # Sous-agents : la santé dépend du TRANSPORT.
    #   - "nats" : worker(s) interne(s) → on compte les pods vivants dans KV `pods` ;
    #   - "a2a"  : agent HTTP externe → il N'écrit PAS dans KV `pods` (d'où le faux
    #              "down" historique). On vérifie sa liveness en interrogeant sa card.
    try:
        async with state.sm() as s:
            agents = (
                await s.execute(
                    select(AgentType.id, AgentType.transport, AgentType.card_url)
                )
            ).all()
    except Exception:
        agents = []
    # Pods vivants par agent (KV `pods`, alimenté par les workers NATS).
    pods_by_agent: dict[str, int] = {}
    try:
        for value in (await _get_pods_entries()).values():
            try:
                pod = json.loads(value)
                aid = pod.get("agent_id")
                if aid:
                    pods_by_agent[aid] = pods_by_agent.get(aid, 0) + 1
            except Exception:
                pass
    except Exception:
        pass
    # Probes A2A en parallèle (timeout court, mutualisé par le cache 3 s du endpoint).
    a2a_targets = [(aid, url) for aid, tr, url in agents if tr == "a2a" and url]
    a2a_up: dict[str, bool] = {}
    if a2a_targets:
        async with httpx.AsyncClient(timeout=2.0) as client:
            probes = await asyncio.gather(
                *[client.get(url) for _, url in a2a_targets], return_exceptions=True
            )
        for (aid, _), res in zip(a2a_targets, probes):
            a2a_up[aid] = (not isinstance(res, Exception)) and res.status_code < 500
    for aid, transport, _card_url in agents:
        if transport == "a2a":
            up = a2a_up.get(aid, False)
            out.append(
                {
                    "name": aid,
                    "category": "agents",
                    "transport": "a2a",
                    "pods": 0,
                    "status": "up" if up else "down",
                    "detail": "A2A joignable" if up else "A2A injoignable",
                }
            )
        else:
            count = pods_by_agent.get(aid, 0)
            out.append(
                {
                    "name": aid,
                    "category": "agents",
                    "transport": "nats",
                    "pods": count,
                    "status": "up" if count > 0 else "down",
                    "detail": f"{count} pod(s)",
                }
            )

    _health_cache["ts"] = now
    _health_cache["value"] = out
    return out


# ---------------------------------------------------------------------------
# Traduction live — proxy direct vers m2m100
# ---------------------------------------------------------------------------

_M2M_URL = "http://m2m100-translation.agents.svc.cluster.local:7002/process"
_LID_URL = (
    "http://text-language-identification.agents.svc.cluster.local:8080/detect-language"
)

# Le tokenizer HuggingFace M2M100 attend des codes ISO 639-1 à 2 lettres.
# La LID texte renvoie des trigrammes ISO 639-2B (ex. "fra", "eng") — on les
# convertit ici. Les codes inconnus sont passés tels quels (m2m100 rejette alors
# avec un 400 lisible plutôt qu'un crash opaque).
_ISO3_TO_ISO1: dict[str, str] = {
    "fra": "fr",
    "eng": "en",
    "deu": "de",
    "spa": "es",
    "ita": "it",
    "por": "pt",
    "nld": "nl",
    "pol": "pl",
    "rus": "ru",
    "ukr": "uk",
    "ara": "ar",
    "zho": "zh",
    "jpn": "ja",
    "kor": "ko",
    "tur": "tr",
    "ron": "ro",
    "ces": "cs",
    "swe": "sv",
}


def _to_iso1(code: str) -> str:
    return _ISO3_TO_ISO1.get(code.lower(), code)


class _TranslateRequest(BaseModel):
    text: str
    target: str = "fr"  # code ISO 639-1 (2 lettres)
    source: str = ""  # "" → auto-détection via LID


class _TranslateResponse(BaseModel):
    translated: str
    detected_source: str


@app.post("/api/translate", response_model=_TranslateResponse)
async def translate_text(req: _TranslateRequest) -> _TranslateResponse:
    """Proxy vers m2m100 : détecte la langue source si non fournie, traduit, renvoie le texte."""
    if not req.text.strip():
        return _TranslateResponse(translated="", detected_source="")

    source = _to_iso1(req.source.strip())

    # 1. Auto-détection de langue (LID texte) si source non fournie.
    if not source:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                lid_resp = await client.post(
                    _LID_URL, json={"plainText": req.text[:2000]}
                )
                lid_resp.raise_for_status()
                scores: dict = (lid_resp.json() or {}).get("languages") or {}
                if scores:
                    # LID renvoie des trigrammes → convertir avant d'envoyer à m2m100
                    source = _to_iso1(max(scores, key=scores.__getitem__))
        except Exception:
            log.warning("LID indisponible — traduction sans langue source explicite")

    # 2. Traduction m2m100 (codes ISO 639-1).
    async with httpx.AsyncClient(timeout=60.0) as client:
        trad_resp = await client.post(
            _M2M_URL,
            json={
                "plainText": req.text,
                "sourceLanguage": source,
                "targetLanguage": _to_iso1(req.target),
            },
        )
        trad_resp.raise_for_status()
        data = trad_resp.json()

    return _TranslateResponse(
        translated=data.get("translatedContent", ""),
        detected_source=source,
    )


# ---------------------------------------------------------------------------
# Voix — STT (transcription) + TTS.
# Le navigateur ne voit pas les ClusterIP : le gateway proxifie, comme pour
# la traduction. Le STT prend l'audio enregistré au micro, le TTS rend du WAV.
#
# TTS_BACKEND=melotts (défaut) → text-to-speech-cpu (POST /tts, WAV)
# TTS_BACKEND=voxtral          → voxtral-mini-tts (POST /v1/audio/speech, OpenAI-compat)
# ---------------------------------------------------------------------------

_STT_URL = os.environ.get(
    "STT_URL", "http://transcription.agents.svc.cluster.local:7000/process-file"
)
_TTS_URL = os.environ.get(
    "TTS_URL", "http://text-to-speech-cpu.agents.svc.cluster.local:8080/tts"
)
_TTS_URL_VOXTRAL = os.environ.get(
    "TTS_URL_VOXTRAL", "http://voxtral-mini-tts.agents.svc.cluster.local:8000/v1/audio/speech"
)
_TTS_BACKEND = os.environ.get("TTS_BACKEND", "melotts").lower()  # "melotts" | "voxtral"

# Voxtral : map langue ISO → voix disponible
_VOXTRAL_VOICE = {"FR": "fr", "EN": "en"}


@app.post("/api/voice/transcribe")
async def voice_transcribe(
    audio: UploadFile = File(...),
    language: str = Form(""),  # "" → auto-détection de langue par le service ASR
) -> dict:
    """Proxy STT : transmet l'audio micro au service transcription, renvoie le texte.

    Commande vocale courte = un seul locuteur : on coupe diarisation et détection
    de tours (plus rapide), on garde la VAD pour rogner les silences.
    """
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "audio vide")
    files = {"audio_file": (audio.filename or "voice.webm", raw, audio.content_type or "audio/webm")}
    data = {
        "language": language,
        "doVoiceActivityDetection": "true",
        "doSpeakerTurnDetection": "false",
        "doSpeakerDiarization": "false",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(_STT_URL, files=files, data=data)
            resp.raise_for_status()
            out = resp.json()
    except httpx.HTTPError as e:
        log.warning("STT indisponible : %s", e)
        raise HTTPException(502, "service de transcription indisponible")
    return {
        "text": (out.get("transcription") or "").strip(),
        "language": out.get("globalLanguage", ""),
    }


class _SpeakRequest(BaseModel):
    text: str
    language: str = "FR"
    speed: float = 1.0


@app.post("/api/voice/speak")
async def voice_speak(req: _SpeakRequest) -> Response:
    """Proxy TTS : transmet le texte au backend TTS actif, renvoie le WAV."""
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "texte vide")
    lang = req.language.strip().upper()
    if lang not in ("FR", "EN"):
        lang = "FR"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if _TTS_BACKEND == "voxtral":
                resp = await client.post(
                    _TTS_URL_VOXTRAL,
                    json={
                        "model": "voxtral-mini-tts-2603",
                        "input": text,
                        "voice": _VOXTRAL_VOICE.get(lang, "fr"),
                        "response_format": "wav",
                        "speed": req.speed,
                    },
                )
            else:
                resp = await client.post(
                    _TTS_URL, json={"text": text, "language": lang, "speed": req.speed}
                )
            resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("TTS indisponible (%s) : %s", _TTS_BACKEND, e)
        raise HTTPException(502, "service de synthèse vocale indisponible")
    return Response(content=resp.content, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
