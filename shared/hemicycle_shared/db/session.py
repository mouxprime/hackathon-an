"""Gestion du moteur et des sessions async SQLAlchemy.

Le moteur est un singleton par process : un pool de connexions partagé, et des
transactions courtes (jamais tenues pendant un appel LLM — cf. invariant v1 n°3).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker | None = None

# Migrations légères idempotentes : `create_all` crée les tables manquantes mais
# n'AJOUTE PAS une colonne à une table déjà existante. Pour qu'un volume Postgres
# existant prenne un nouveau champ sans `make down/up`, on liste ici les ALTER
# `ADD COLUMN IF NOT EXISTS` (no-op si la colonne est déjà là).
_COLUMN_MIGRATIONS = (
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lang VARCHAR NOT NULL DEFAULT 'en'",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS context_tokens integer NOT NULL DEFAULT 0",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS compacted_seq integer NOT NULL DEFAULT 0",
    # Mémoire de cellule : scope un fait ltm_memory à un salon d'état-major (NULL = global).
    "ALTER TABLE ltm_memory ADD COLUMN IF NOT EXISTS channel_id VARCHAR",
    "CREATE INDEX IF NOT EXISTS ix_ltm_memory_channel_id ON ltm_memory (channel_id)",
    # Partage de fichiers inter-agents : référence bucket des octets originaux d'une PJ.
    "ALTER TABLE attachments ADD COLUMN IF NOT EXISTS s3_uri VARCHAR",
    "ALTER TABLE attachments ADD COLUMN IF NOT EXISTS object_key VARCHAR",
)


def get_engine(dsn: str) -> AsyncEngine:
    """Retourne le moteur async (créé une seule fois par process)."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(dsn, echo=False, pool_pre_ping=True, pool_size=10)
    return _engine


def get_sessionmaker(dsn: str) -> async_sessionmaker:
    """Retourne la fabrique de sessions async (singleton par process)."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(dsn), expire_on_commit=False)
    return _sessionmaker


async def init_db(dsn: str) -> None:
    """Crée les tables manquantes + applique les migrations de colonnes (idempotent).

    Appelé au démarrage de chaque service écrivain. `create_all` couvre les tables
    neuves ; `_COLUMN_MIGRATIONS` rattrape les colonnes ajoutées à une table existante.
    """
    engine = get_engine(dsn)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _COLUMN_MIGRATIONS:
            await conn.execute(text(stmt))
