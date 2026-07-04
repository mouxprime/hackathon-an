"""Point d'entrée de l'orchestrateur.

Démarre : connexion NATS + topologie, init DB, chargement du catalogue d'agents,
puis lance en parallèle le consumer principal, le consumer de leases et les balayeurs.
L'orchestrateur est sans état mémoire : tout son état durable vit dans Postgres.
"""

import asyncio
import logging

from sqlalchemy import select

import a2a_bridge
import consumers
import fsm
import sweepers
from hemicycle_shared import nats_io, subjects
from hemicycle_shared.config import settings
from hemicycle_shared.db import AgentType, get_sessionmaker, init_db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [orchestrator] %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")
# httpx logge chaque appel HTTP en INFO — un dispatch A2A en produit 3, plus
# un par appel LLM. On le bascule à WARNING : on garde les vrais événements
# de la FSM lisibles.
logging.getLogger("httpx").setLevel(logging.WARNING)


async def _retry(coro_factory, what: str, attempts: int = 40):
    """Réessaie une opération d'init (DB/NATS) le temps que la dépendance soit prête."""
    for _ in range(attempts):
        try:
            return await coro_factory()
        except Exception as e:  # noqa: BLE001 - on attend volontairement toute erreur d'init
            log.info("attente de %s (%s)...", what, e)
            await asyncio.sleep(2)
    raise RuntimeError(f"{what} indisponible après {attempts} tentatives")


async def _load_agent_catalog(sm) -> None:
    """Charge le catalogue d'agents (Agent Registry) depuis Postgres dans fsm.AGENT_MAP.

    Écrase systématiquement le map — même vide. Sans ça, des agents supprimés
    ressuscitaient via les défauts hardcodés de `fsm.AGENT_MAP`.
    """
    async with sm() as s:
        rows = (await s.execute(select(AgentType))).scalars().all()
    fsm.AGENT_MAP = {
        a.id: {
            "hard_timeout_s": a.hard_timeout_s,
            "discipline": a.discipline,
            "description": a.description,
            "when_to_use": a.when_to_use,
            "transport": a.transport,
        }
        for a in rows
    }
    log.info("catalogue d'agents : %s", list(fsm.AGENT_MAP) or "(vide)")


async def main() -> None:
    """Initialise les dépendances et lance les boucles concurrentes de l'orchestrateur."""
    settings.service_name = "orchestrator"
    await _retry(lambda: init_db(settings.postgres_dsn), "Postgres")
    sm = get_sessionmaker(settings.postgres_dsn)
    await _load_agent_catalog(sm)

    nc = await nats_io.connect(f"orchestrator-{settings.instance_id}")
    js = nc.jetstream()
    await nats_io.ensure_topology(js)
    kv = await js.key_value(subjects.KV_LOCKS)
    # Le KV `tools_config` est lu (jamais écrit) par l'orchestrateur au planning.
    fsm.TOOLS_KV = await js.key_value(subjects.KV_TOOLS_CONFIG)
    # Connexion NATS pour le relais live du « thinking » (canal éphémère orch.progress).
    fsm.NC = nc

    log.info("orchestrateur %s prêt", settings.instance_id)
    await asyncio.gather(
        consumers.run_main_consumer(nc, js, kv, sm),
        consumers.run_lease_consumer(nc, js, sm),
        sweepers.run_sweepers(nc, js, sm),
        a2a_bridge.run_a2a_bridge(js, sm),
        consumers.run_events_reconciler(js, sm),
    )


if __name__ == "__main__":
    asyncio.run(main())
