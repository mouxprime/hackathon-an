"""Compatibilité A2A v1.0 (Agent2Agent, protocole ouvert Linux Foundation).

Ce package fournit les briques pour qu'un sub-agent Hémicycle se présente comme un
agent A2A natif : une **Agent Card** publiée à `/.well-known/agent-card.json`,
un endpoint JSON-RPC 2.0 `message/send`, et un flux SSE pour le streaming.

Symétriquement, le client `A2AClient` permet à l'orchestrateur de dispatcher du
travail à des agents externes A2A, livrés par des tiers (cf. chantier 4).
"""

from .card import AgentCapabilities, AgentCard, AgentSkill, manifest_to_agent_card
from .client import A2AClient
from .probe import Check, ProbeReport, probe_agent, probe_agent_sync, validate_agent_card
from .server import mount_a2a

__all__ = [
    "AgentCapabilities",
    "AgentCard",
    "AgentSkill",
    "manifest_to_agent_card",
    "A2AClient",
    "mount_a2a",
    "Check",
    "ProbeReport",
    "probe_agent",
    "probe_agent_sync",
    "validate_agent_card",
]
