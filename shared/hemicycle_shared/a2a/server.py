"""Serveur A2A v1.0 — monté à côté d'un sub-agent Hémicycle.

`mount_a2a(app, manifest, handler, base_url)` greffe sur un FastAPI :
- `GET /.well-known/agent-card.json` — l'Agent Card
- `POST /` — endpoint JSON-RPC 2.0 (méthodes `message/send`, `tasks/get`)
- `GET /tasks/{task_id}/stream` — flux SSE (trace + result), équivalent au
  `message/stream` JSON-RPC pour les clients qui préfèrent un GET.

Backend uniforme : le `handler` reçoit `(td, progress, trace) -> dict` (même
signature que les handlers NATS) — la même fonction discipline sert les deux
canaux. Le serveur A2A construit un faux `TaskDispatch` à partir du message
A2A entrant, puis appelle le handler avec des callbacks qui poussent dans une
queue par task. Le SSE consomme la queue.

L'implémentation reste **minimaliste et conforme** : ce qui est sur le fil
correspond à la spec, mais on ne couvre pas l'extension webhooks/push.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import uuid4

from fastapi import APIRouter, Body, FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..manifests import AgentManifest
from ..messages import TaskDispatch
from .card import AgentCard, manifest_to_agent_card

log = logging.getLogger("hemicycle.a2a.server")

# Type d'un handler de discipline (réutilisé tel quel depuis les workers NATS).
Handler = Callable[[TaskDispatch, Callable[[float, str], Awaitable[None]],
                    Callable[[str, dict], Awaitable[None]]], Awaitable[dict]]


def _now_iso() -> str:
    """Horodatage ISO-8601 UTC pour les champs A2A `timestamp`."""
    return datetime.now(timezone.utc).isoformat()


def _extract_text(message: dict) -> str:
    """Récupère le texte combiné des `parts` d'un Message A2A (text/plain uniquement)."""
    parts = message.get("parts", []) if isinstance(message, dict) else []
    return "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("kind") == "text")


class _TaskStore:
    """Registre en mémoire des tâches A2A actives — backing store du SSE et de `tasks/get`."""

    def __init__(self) -> None:
        self.queues: dict[str, asyncio.Queue] = {}
        self.tasks: dict[str, dict] = {}              # task_id → snapshot Task A2A

    def create(self, task_id: str, context_id: str) -> asyncio.Queue:
        """Initialise la queue d'événements et l'état initial d'une task."""
        q: asyncio.Queue = asyncio.Queue()
        self.queues[task_id] = q
        self.tasks[task_id] = {
            "id": task_id, "contextId": context_id,
            "status": {"state": "submitted", "timestamp": _now_iso()},
            "history": [], "artifacts": [], "kind": "task",
        }
        return q

    def update_state(self, task_id: str, state: str, note: str = "") -> dict:
        """Met à jour l'état d'une task et retourne le snapshot pour ack JSON-RPC."""
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = {"state": state, "timestamp": _now_iso(),
                                              "message": {"role": "agent", "parts": [
                                                  {"kind": "text", "text": note}]}}
        return self.tasks.get(task_id, {})


def mount_a2a(app: FastAPI, manifest: AgentManifest, handler: Handler, base_url: str) -> None:
    """Greffe les endpoints A2A v1.0 sur l'application FastAPI passée."""
    card = manifest_to_agent_card(manifest, base_url)
    store = _TaskStore()
    router = APIRouter()

    @app.get("/.well-known/agent-card.json", response_model=AgentCard,
             tags=["A2A"], summary="Carte d'identité A2A v1.0 du sub-agent")
    async def agent_card() -> AgentCard:
        """Renvoie l'Agent Card de ce sub-agent — discovery A2A v1.0."""
        return card

    def _sse(event: str, data: dict) -> dict:
        """Sérialise un payload dict en SSE conforme (data: <json>)."""
        return {"event": event, "data": json.dumps(data, default=str)}

    async def _run_task(task_id: str, context_id: str, td: TaskDispatch,
                         q: asyncio.Queue) -> None:
        """Exécute le handler discipline et pousse trace + result dans la queue SSE."""
        store.update_state(task_id, "working", "tâche en cours")
        await q.put(_sse("status", store.tasks[task_id]))

        async def progress(pct: float, note: str) -> None:
            """Callback compat handler NATS — émet un status partiel sur le SSE."""
            await q.put(_sse("progress", {"pct": pct, "note": note, "taskId": task_id}))

        async def trace(kind: str, data: dict) -> None:
            """Callback compat handler NATS — émet une trace riche (thinking/tool_call/...)."""
            await q.put(_sse("trace", {"kind": kind, "data": data, "taskId": task_id}))

        try:
            output = await handler(td, progress, trace)
            store.update_state(task_id, "completed", "tâche terminée")
            store.tasks[task_id]["artifacts"] = [{
                "artifactId": str(uuid4()), "name": f"{manifest.id}-result",
                "parts": [{"kind": "data", "data": output}],
            }]
            await q.put(_sse("result", store.tasks[task_id]))
        except Exception as e:  # noqa: BLE001
            log.exception("A2A task %s en échec", task_id)
            store.update_state(task_id, "failed", str(e))
            await q.put(_sse("result", store.tasks[task_id]))
        finally:
            await q.put(_sse("done", {"taskId": task_id}))

    @router.post("/", tags=["A2A"], summary="Endpoint JSON-RPC 2.0 (message/send + tasks/get)")
    async def jsonrpc(payload: dict = Body(...)) -> dict:
        """Point d'entrée JSON-RPC 2.0. Méthodes supportées : `message/send`, `tasks/get`."""
        rpc_id = payload.get("id")
        method = payload.get("method", "")
        params = payload.get("params") or {}

        if method == "message/send":
            message = params.get("message") or {}
            text = _extract_text(message)
            # Langue : extension `message.metadata.lang` (sinon défaut anglais).
            meta = message.get("metadata") if isinstance(message, dict) else None
            lang = (meta or {}).get("lang") if isinstance(meta, dict) else None
            lang = lang if lang in ("en", "fr") else "en"
            task_id = str(uuid4())
            context_id = params.get("contextId") or str(uuid4())
            q = store.create(task_id, context_id)
            td = TaskDispatch(corr_id=task_id, conv_id=context_id, agent_type=manifest.id,
                              plan_step_id="a2a-direct", instruction=text, lang=lang,
                              params={"text": text, "tools": {}},
                              hard_timeout_s=manifest.hard_timeout_s)
            asyncio.create_task(_run_task(task_id, context_id, td, q))
            return {"jsonrpc": "2.0", "id": rpc_id, "result": store.tasks[task_id]}

        if method == "tasks/get":
            task_id = params.get("id")
            if not task_id or task_id not in store.tasks:
                return {"jsonrpc": "2.0", "id": rpc_id,
                        "error": {"code": -32001, "message": "task inconnue"}}
            return {"jsonrpc": "2.0", "id": rpc_id, "result": store.tasks[task_id]}

        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32601, "message": f"méthode inconnue: {method}"}}

    @router.get("/tasks/{task_id}/stream", tags=["A2A"],
                summary="Stream SSE des événements d'une tâche (trace + result)")
    async def stream_task(task_id: str) -> EventSourceResponse:
        """Flux SSE des trace events + résultat final d'une tâche en cours."""
        q = store.queues.get(task_id)
        if q is None:
            raise HTTPException(404, "task inconnue")

        async def event_gen():
            while True:
                evt = await q.get()
                yield evt
                if evt["event"] == "done":
                    return

        return EventSourceResponse(event_gen())

    app.include_router(router)
    log.info("A2A v1.0 monté pour agent=%s sur %s", manifest.id, base_url)
