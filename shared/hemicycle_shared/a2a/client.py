"""Client A2A v1.0 — utilisé par l'orchestrateur pour appeler des agents externes.

Le client expose deux usages :
- `discover(card_url)` → récupère l'Agent Card distante (rétro-projection en `AgentCard`) ;
- `send_and_stream(card, text)` → envoie un message via JSON-RPC `message/send`,
  puis itère sur le flux SSE des événements (trace + result) — async generator.

L'orchestrateur transforme ces events SSE en `Progress` virtuels qu'il pousse
dans le consumer de leases — la pyramide UI rend alors un agent A2A externe
exactement comme un sub-agent NATS interne (chantier 4).
"""

import json
import logging
from typing import AsyncIterator

import httpx

from .card import AgentCard

log = logging.getLogger("hemicycle.a2a.client")


class A2AClient:
    """Petit client async pour la spec A2A v1.0 (HTTP + JSON-RPC + SSE)."""

    def __init__(self, base_url: str, timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def discover(self) -> AgentCard:
        """Récupère l'Agent Card publiée à `/.well-known/agent-card.json`."""
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.get(f"{self.base_url}/.well-known/agent-card.json")
            resp.raise_for_status()
            return AgentCard.model_validate(resp.json())

    async def send_message(self, text: str, context_id: str | None = None,
                           lang: str | None = None) -> dict:
        """Envoie un message via JSON-RPC `message/send` — retourne le snapshot Task initial.

        `lang` (si fourni) voyage dans `message.metadata.lang` (extension conforme A2A) :
        un agent qui sait lire la métadonnée produit sa sortie dans cette langue.
        """
        message: dict = {"role": "user", "parts": [{"kind": "text", "text": text}]}
        if lang:
            message["metadata"] = {"lang": lang}
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {
                "message": message,
                **({"contextId": context_id} if context_id else {}),
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(f"{self.base_url}/", json=payload)
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                raise RuntimeError(body["error"])
            return body["result"]

    async def stream_task(self, task_id: str) -> AsyncIterator[dict]:
        """Itère sur les événements SSE d'une tâche en cours : `{event, data}` par item."""
        url = f"{self.base_url}/tasks/{task_id}/stream"
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                event_name = "message"
                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    if not line:
                        # Fin d'event SSE → on dispatch puis on remet à zéro.
                        if data_lines:
                            data_str = "\n".join(data_lines)
                            try:
                                payload = json.loads(data_str)
                            except Exception:
                                payload = data_str
                            yield {"event": event_name, "data": payload}
                            if event_name == "done":
                                return
                        event_name, data_lines = "message", []
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())

    async def send_and_stream(self, text: str, context_id: str | None = None,
                              lang: str | None = None) -> AsyncIterator[dict]:
        """Combo `message/send` + flux SSE — usage standard côté orchestrateur."""
        task = await self.send_message(text, context_id=context_id, lang=lang)
        async for evt in self.stream_task(task["id"]):
            yield evt
