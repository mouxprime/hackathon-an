"""Recherche dans la mémoire interne long-terme (table `ltm_memory`).

Itér. 1 : **plus de pré-filtre**. On charge les N dernières lignes pour l'utilisateur
courant (+ celles partagées) et c'est le **LLM routeur** qui choisit lesquelles sont
pertinentes via `relevant_memory_ids` (cf. `llm._build_routing_prompt`). Le scoring
keyword (ILIKE + count de tokens) ratait les accents, le stemming et les acronymes ;
laisser le LLM trancher capture le sémantique gratuitement à ce volume.

pgvector est en place dans le schéma pour, au-delà de ~500 lignes, ajouter un
pré-filtre top-K **avant** la sélection LLM (itér. 2). L'interface `recall()` ne
change pas : `query` reste dans la signature pour cette extension future.
"""

from sqlalchemy import desc, or_, select

from hemicycle_shared.db import LtmMemory


async def recall(
    session,
    user_id: str | None,
    query: str,
    limit: int = 100,
    channel_id: str | None = None,
) -> list[dict]:
    """Retourne les N dernières mémoires pertinentes à présenter au routeur LLM.

    `query` est inutilisé en itér. 1 (le LLM filtre côté sélection) — gardé pour
    rester compatible avec un futur passage à pgvector où il pilotera le top-K.

    `channel_id` : appelé depuis un salon d'état-major → on charge la mémoire DE LA
    CELLULE (`channel_id == <id>`) **et** la mémoire globale (`channel_id IS NULL`),
    pour que Hémicycle tienne compte du contexte propre à la cellule en plus du global.
    """
    del query  # cf. docstring : conservé pour compat pgvector
    stmt = select(LtmMemory).order_by(desc(LtmMemory.created_at)).limit(limit)
    if channel_id:
        # Mémoire de cellule + mémoire globale (non rattachée à un salon).
        stmt = stmt.where(
            or_(LtmMemory.channel_id == channel_id, LtmMemory.channel_id.is_(None))
        )
    elif user_id:
        # Exclure explicitement les mémoires de cellule (channel_id non nul) pour
        # qu'une mémoire scopée à un salon ne fuite pas dans les conversations perso.
        stmt = stmt.where(
            or_(LtmMemory.user_id == user_id, LtmMemory.user_id.is_(None)),
            LtmMemory.channel_id.is_(None),
        )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "content": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]
