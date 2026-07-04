"""Schéma du manifeste d'agent — le contrat de l'« Agent Registry ».

Le manifeste est la projection « capabilities » qu'un type de sub-agent expose.
L'orchestrateur sérialise ces manifestes dans le prompt du LLM-planificateur :
le champ `when_to_use` (langage naturel) est ce qui pilote la qualité du routing.
"""

from typing import Any, Literal

from pydantic import BaseModel


class MCPServerRef(BaseModel):
    """Référence à un serveur MCP utilisé par un sub-agent.

    Cf. `hemicycle_shared/mcp_client.py` : iter. 1 = stub ; le SDK worker instanciera
    un vrai client MCP en iter. 2 dès qu'un serveur est livré (cf. doc README).
    """

    name: str                            # nom logique ("telegram", "federated-search"…)
    url: str                             # endpoint du serveur MCP
    tools: list[str] = []                # outils exposés par ce serveur


class AgentManifest(BaseModel):
    """Description versionnée d'un type de sub-agent (code + capacités).

    Très proche d'une **Agent Card** A2A : capacités déclaratives, schémas, coût.
    Le passage A2A consistera à exposer chaque sub-agent en HTTP/JSON-RPC en plus
    de son consumer NATS (cf. roadmap).
    """

    id: str                              # ex. "mi_search", "imint", "geoint"
    discipline: str                      # ex. "SEARCH", "IMINT", "GEOINT"
    description: str                     # résumé court de ce que fait l'agent
    when_to_use: str                     # langage naturel — pilote le routing LLM
    input_schema: dict[str, Any]         # ce qu'il faut lui fournir
    output_schema: dict[str, Any]        # ce qu'il rend
    tools: list[str]                     # outils internes (noms libres)
    mcp_servers: list[MCPServerRef] = [] # serveurs MCP que ce type d'agent peut appeler
    cost_hint: Literal["low", "medium", "high"]
    latency_p95_s: float                 # latence observée — aide l'arbitrage LLM
    hard_timeout_s: int                  # borne dure : au-delà, la tâche est abandonnée
