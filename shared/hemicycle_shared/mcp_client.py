"""Client MCP — HTTP Streamable transport (MCP 2025-03-26).

Remplace le stub de l'itération 1. Effectue de vrais appels JSON-RPC vers
un serveur MCP (FastMCP) via le transport HTTP Streamable :
  1. POST /mcp  initialize  → obtient le mcp-session-id
  2. POST /mcp  notifications/initialized
  3. POST /mcp  tools/list  → schémas complets
  4. POST /mcp  tools/call  → résultat réel

Les réponses JSON ou SSE sont toutes les deux gérées.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .manifests import MCPServerRef

_PROTO_VERSION = "2025-03-26"
_CLIENT_INFO = {"name": "hemicycle-gateway", "version": "1.0"}
_TIMEOUT_INIT = 10.0
_TIMEOUT_CALL = 30.0


def _base_headers(session_id: str | None = None) -> dict[str, str]:
    """En-têtes communs à toutes les requêtes MCP."""
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        h["mcp-session-id"] = session_id
    return h


async def _parse_response(resp: httpx.Response) -> Any:
    """Extrait le champ 'result' d'une réponse JSON ou SSE (agrège le premier message data)."""
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        text = resp.text
        for line in text.splitlines():
            if line.startswith("data: "):
                try:
                    msg = json.loads(line[6:])
                    if "result" in msg:
                        return msg["result"]
                    if "error" in msg:
                        raise RuntimeError(f"MCP error: {msg['error']}")
                except json.JSONDecodeError:
                    continue
        return {}
    msg = resp.json()
    if "error" in msg:
        raise RuntimeError(f"MCP error: {msg['error']}")
    return msg.get("result", {})


class MCPClient:
    """Façade vers un serveur MCP via HTTP Streamable (FastMCP / SDK officiel)."""

    def __init__(self, ref: MCPServerRef) -> None:
        """Mémorise la référence ; la session sera ouverte à la première requête."""
        self.ref = ref

    async def _open_session(self, http: httpx.AsyncClient) -> str | None:
        """Handshake MCP : initialize + notifications/initialized. Retourne le session-id."""
        body = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": _PROTO_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        }
        resp = await http.post(self.ref.url, json=body, headers=_base_headers(), timeout=_TIMEOUT_INIT)
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id")

        # Notification initialized (fire-and-forget, le serveur ne répond pas)
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        await http.post(self.ref.url, json=notif, headers=_base_headers(session_id), timeout=5.0)
        return session_id

    async def _call(
        self, http: httpx.AsyncClient, session_id: str | None,
        method: str, params: dict[str, Any],
    ) -> Any:
        """Appel JSON-RPC générique, gère JSON et SSE."""
        body = {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}
        resp = await http.post(
            self.ref.url, json=body, headers=_base_headers(session_id), timeout=_TIMEOUT_CALL,
        )
        resp.raise_for_status()
        return await _parse_response(resp)

    async def list_tools(self) -> list[dict[str, Any]]:
        """Découverte des outils MCP (tools/list) — renvoie schémas JSON Schema complets."""
        async with httpx.AsyncClient() as http:
            session_id = await self._open_session(http)
            result = await self._call(http, session_id, "tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return tools  # [{name, description, inputSchema}, …]

    async def call_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Invoque un outil MCP via tools/call avec les arguments fournis."""
        async with httpx.AsyncClient() as http:
            session_id = await self._open_session(http)
            result = await self._call(http, session_id, "tools/call", {"name": name, "arguments": params})
        return result if isinstance(result, dict) else {"content": result}

    async def close(self) -> None:
        """No-op : chaque appel ouvre/ferme sa propre connexion HTTP."""
