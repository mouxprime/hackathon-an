"""Point d'entrée d'un pod sub-agent.

Le type d'agent (agent_votes / agent_deputes / agent_lois) est choisi par la variable
d'environnement `HEMI_AGENT_ID` — une seule image, trois services docker-compose scalables.

Chaque pod monte aussi un serveur A2A v1.0 sur le port `HEMI_A2A_PORT` (8600
par défaut) : Agent Card publiée à `/.well-known/agent-card.json`, JSON-RPC à `/`,
SSE à `/tasks/{id}/stream`. C'est l'interface exposée à l'extérieur du tailnet.
"""

import asyncio
import logging
import os

from hemicycle_shared.config import settings
from hemicycle_shared.manifests import AgentManifest

from disciplines import DISCIPLINES
from sdk import Worker

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [subagent] %(levelname)s %(message)s")
log = logging.getLogger("subagent")


# Manifests static pour les sub-agents Hémicycle (alignés sur ceux du seed Postgres).
# La duplication est volontaire : ces métadonnées sont publiées via A2A — le worker
# ne doit pas dépendre de Postgres pour les exposer (invariant n°7).
_MANIFESTS: dict[str, AgentManifest] = {
    "agent_votes": AgentManifest(
        id="agent_votes", discipline="VOTES",
        description="Spécialiste des scrutins publics de l'Assemblée nationale : résultat "
                    "d'un vote, détail par groupe politique, position d'un député.",
        when_to_use="Quand la question porte sur un VOTE ou un SCRUTIN : « comment a voté "
                    "tel député / tel groupe », « résultat du scrutin sur tel texte », "
                    "« qui a voté pour/contre », motion de censure, adoption ou rejet d'un "
                    "texte en séance.",
        input_schema={"text": "str"},
        output_schema={"scrutin": "dict", "position_depute": "dict", "groupes": "list",
                       "sources": "list", "summary": "str"},
        tools=["sql_scrutins_search", "sql_scrutin_detail", "sql_vote_depute", "civix_api"],
        cost_hint="medium", latency_p95_s=10.0, hard_timeout_s=120),
    "agent_deputes": AgentManifest(
        id="agent_deputes", discipline="DEPUTES",
        description="Spécialiste des députés et groupes politiques : fiche d'identité "
                    "parlementaire, circonscription, votes récents, participation.",
        when_to_use="Quand la question porte sur un DÉPUTÉ ou un GROUPE politique : « qui "
                    "est le député de telle circonscription », fiche/biographie "
                    "parlementaire, activité et participation d'un élu, composition d'un "
                    "groupe.",
        input_schema={"text": "str"},
        output_schema={"depute": "dict", "votes_recents": "list", "stats": "dict",
                       "sources": "list", "summary": "str"},
        tools=["sql_depute_lookup", "sql_depute_votes_recents", "sql_depute_stats"],
        cost_hint="medium", latency_p95_s=10.0, hard_timeout_s=120),
    "agent_lois": AgentManifest(
        id="agent_lois", discipline="LOIS",
        description="Spécialiste des dossiers législatifs : parcours d'un texte (navette), "
                    "étapes passées et à venir, statut exact d'un projet ou d'une "
                    "proposition de loi.",
        when_to_use="Quand la question porte sur une LOI ou un TEXTE en discussion : « où "
                    "en est tel projet/proposition de loi », parcours législatif, navette, "
                    "commission mixte paritaire, promulgation, contenu d'un dossier "
                    "législatif.",
        input_schema={"text": "str"},
        output_schema={"dossier": "dict", "etapes": "list", "sources": "list",
                       "summary": "str"},
        tools=["sql_dossier_search", "sql_dossier_timeline", "mcp_moulineuse"],
        cost_hint="medium", latency_p95_s=12.0, hard_timeout_s=150),
}


async def main() -> None:
    """Sélectionne le handler de discipline et lance le worker (NATS + A2A)."""
    agent_id = settings.agent_id
    if agent_id not in DISCIPLINES:
        raise SystemExit(f"HEMI_AGENT_ID inconnu: {agent_id!r} (attendu: {list(DISCIPLINES)})")
    settings.service_name = f"subagent-{agent_id}"
    log.info("démarrage du sub-agent %s", agent_id)
    worker = Worker(agent_id, DISCIPLINES[agent_id], manifest=_MANIFESTS.get(agent_id))
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
