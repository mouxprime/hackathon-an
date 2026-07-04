"""Modèles Agent Card conformes à la spec A2A v1.0.

Un agent A2A se présente via un document JSON publié à `/.well-known/agent-card.json`.
Le document liste : identité (`name`, `description`, `version`, `url`), capacités
(`streaming`, `pushNotifications`), modalités d'I/O (`defaultInputModes`,
`defaultOutputModes`), et compétences (`skills`).

`manifest_to_agent_card` projette un `AgentManifest` Hémicycle vers cette structure
— c'est le pont qui rend nos sub-agents internes consommables comme agents A2A
externes sans réécriture.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..manifests import AgentManifest


class AgentCapabilities(BaseModel):
    """Capacités optionnelles annoncées par un agent A2A (spec v1.0)."""

    streaming: bool = True             # supporte message/stream (SSE)
    pushNotifications: bool = False    # supporte les webhooks (non implémenté en itér. 1)
    stateTransitionHistory: bool = True


class AgentSkill(BaseModel):
    """Une compétence exposée par l'agent — ≈ un point d'entrée fonctionnel.

    En Hémicycle, on projette : id = `agent_id`, le when_to_use du manifeste devient
    la description, et chaque outil natif/MCP devient un tag pour faciliter le
    routing chez un consommateur externe.
    """

    id: str
    name: str
    description: str
    tags: list[str] = []
    examples: list[str] = []
    inputModes: list[str] = ["text/plain"]
    outputModes: list[str] = ["application/json"]


class AgentCard(BaseModel):
    """Carte d'identité A2A v1.0 publiée à `/.well-known/agent-card.json`.

    `extra="allow"` est CRITIQUE : la spec A2A autorise des extensions namespacées
    (`x-hemicycle-discipline`, `x-hemicycle-when-to-use`, `x-hemicycle-mcp-servers`,
    `x-hemicycle-cost-hint`...). Sans ça, Pydantic les DROPPE au `model_validate`
    du discover → l'enrôlement perd la discipline/when_to_use de routage et
    retombe sur name/description. C'est ce qui rendait le routeur aveugle aux
    métadonnées enrichies des agents A2A.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    url: str                                       # endpoint racine A2A
    version: str = "1.0.0"
    protocolVersion: Literal["0.2.5", "1.0"] = "1.0"
    defaultInputModes: list[str] = ["text/plain"]
    defaultOutputModes: list[str] = ["application/json"]
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    skills: list[AgentSkill] = []
    # Métadonnées utiles côté UI / monitoring — extensions Hémicycle (non standard).
    provider: dict = Field(default_factory=lambda: {"organization": "Hémicycle", "url": "hemicycle.local"})


def manifest_to_agent_card(manifest: AgentManifest, base_url: str) -> AgentCard:
    """Projette un `AgentManifest` Hémicycle en `AgentCard` A2A v1.0.

    `base_url` est l'URL absolue où le serveur A2A du sub-agent répond
    (ex. `http://subagent-mi-search:8600`).
    """
    skill = AgentSkill(
        id=manifest.id,
        name=f"{manifest.discipline} — {manifest.description}",
        description=manifest.when_to_use,
        tags=list(manifest.tools) + [s.name for s in (manifest.mcp_servers or [])],
        examples=[f"Investiguer une cible {manifest.discipline} sur un secteur donné."],
    )
    return AgentCard(
        name=f"hemicycle-{manifest.id}",
        description=manifest.description,
        url=base_url.rstrip("/"),
        skills=[skill],
    )
