"""Channels (salons de discussion de groupe) — définitions seed + helpers d'écriture.

Les channels sont DISTINCTS des conversations FSM (cf. `db.models.Channel`) : un fil
append-only multi-auteurs, sans lock/fence/plan. Ce module centralise :
- `CHANNEL_DEFS` : les 4 salons seedés (système « hemicycle » + « Suivis » citoyens) ;
- `ensure_channels` : seed idempotent (upsert ON CONFLICT DO NOTHING — ne TRUNCATE jamais) ;
- `post_channel_message` : écrit un message en DB puis le diffuse en live sur le bus.

`post_channel_message` est partagé : le gateway l'utilise pour les posts utilisateurs
(REST/WS), et l'orchestrateur pourra l'appeler pour publier des alertes dans « hemicycle »
(en session SÉPARÉE de sa transaction fencée — cf. plan, piège fence/version).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import Channel, ChannelMessage
from . import subjects


# Les 4 salons. « hemicycle » (système, lecture seule — id conservé pour la
# rétro-compatibilité du gateway et des alertes orchestrateur) en tête, puis les
# « Suivis » citoyens : un salon par domaine de données de l'Assemblée nationale.
# Chaque `description` est le MANDAT du salon, injecté dans la planification ET la
# réponse de l'orchestrateur quand on le sollicite depuis ce salon (cf. orchestrator
# `_prep_planning` / `_prep_report`). Rédigés pour PERSONNALISER vraiment la réponse :
# chaque salon a un domaine prioritaire et un hors-périmètre explicite. Éditables
# par l'utilisateur via le pop-up réglages (PATCH /api/channels/{id}) — non écrasés
# au redémarrage (ensure_channels = ON CONFLICT DO NOTHING).
SUIVI_PROMPTS: dict[str, str] = {
    "suivi-scrutins": (
        "Salon « Suivi des scrutins » — les votes de l'hémicycle expliqués aux citoyens. "
        "Domaine : scrutins publics de l'Assemblée nationale (17e législature) — résultat "
        "d'un vote (adopté/rejeté, pour/contre/abstentions), votes nominatifs des députés, "
        "positions des groupes politiques. "
        "PRIORISE la restitution factuelle et sourcée d'un scrutin : qui a voté quoi, avec "
        "quels décomptes officiels ; rappelle toujours qu'un « non-votant » n'est PAS une "
        "abstention. Reste strictement neutre et non partisan. "
        "HORS PÉRIMÈTRE sauf lien avec un vote : la biographie des députés ou le contenu "
        "détaillé des textes ne t'intéressent que pour éclairer un scrutin précis."
    ),
    "mes-deputes": (
        "Salon « Mes députés » — qui siège à l'Assemblée nationale et qui me représente. "
        "Domaine : fiches des députés (17e législature) — identité, groupe politique, "
        "circonscription et département, mandat en cours ou terminé (ancien député), "
        "composition et effectifs des groupes. "
        "PRIORISE l'identification claire d'un député (nom, groupe, circonscription, lien "
        "vers sa fiche officielle assemblee-nationale.fr) et les statistiques factuelles "
        "de participation. Reste strictement neutre : aucun jugement de valeur. "
        "HORS PÉRIMÈTRE sauf lien avec un député : le détail d'un scrutin ou d'un texte ne "
        "t'intéresse que pour situer l'activité de la personne."
    ),
    "parcours-lois": (
        "Salon « Parcours des lois » — où en est un texte et comment la loi se fabrique. "
        "Domaine : dossiers législatifs (17e législature) — dépôt, examens en commission "
        "et en séance, navette entre Assemblée et Sénat, adoption, promulgation. "
        "PRIORISE la chronologie pédagogique d'un texte (timeline des actes, étape "
        "actuelle, prochaine étape) en langage clair : explique le jargon (navette, CMP, "
        "49.3…) au lieu de le supposer connu, et cite le dossier officiel. "
        "HORS PÉRIMÈTRE sauf lien avec un texte : les votes nominatifs et les fiches de "
        "députés ne t'intéressent que comme jalons du parcours d'une loi."
    ),
}


CHANNEL_DEFS: list[dict[str, Any]] = [
    {"id": "hemicycle", "name": "Hémicycle", "kind": "system", "role": "Orchestrateur",
     "readonly": True, "position": 0,
     "description": "Alertes de l'orchestrateur — visibles par tous, lecture seule."},
    {"id": "suivi-scrutins", "name": "Suivi des scrutins", "kind": "staff", "role": "Votes",
     "readonly": False, "position": 1, "description": SUIVI_PROMPTS["suivi-scrutins"]},
    {"id": "mes-deputes", "name": "Mes députés", "kind": "staff", "role": "Députés",
     "readonly": False, "position": 2, "description": SUIVI_PROMPTS["mes-deputes"]},
    {"id": "parcours-lois", "name": "Parcours des lois", "kind": "staff", "role": "Lois",
     "readonly": False, "position": 3, "description": SUIVI_PROMPTS["parcours-lois"]},
]


async def ensure_channels(sm) -> None:
    """Seed idempotent des 4 channels. Upsert ON CONFLICT(id) DO NOTHING.

    Ne TRUNCATE jamais et ne touche jamais `channel_messages` : rejouable à chaque
    démarrage sans effacer les discussions. `sm` = sessionmaker async.
    """
    async with sm() as s:
        for d in CHANNEL_DEFS:
            await s.execute(
                pg_insert(Channel).values(**d).on_conflict_do_nothing(index_elements=["id"])
            )
        await s.commit()


async def post_channel_message(
    sm,
    nc,
    *,
    channel_id: str,
    text: str,
    author_id: str,
    author_role: str,
    kind: str = "message",
    payload: dict | None = None,
    dedup_key: str | None = None,
) -> dict[str, Any] | None:
    """Persiste un message de channel (session SÉPARÉE) puis le diffuse en live.

    Renvoie le message sérialisé, ou `None` si `dedup_key` a heurté un doublon
    (ON CONFLICT DO NOTHING) — dans ce cas on ne republie rien. `nc` = client NATS
    core (le fan-out est éphémère : seuls les WS connectés reçoivent).
    """
    async with sm() as s:
        stmt = pg_insert(ChannelMessage).values(
            channel_id=channel_id,
            author_id=author_id,
            author_role=author_role,
            kind=kind,
            text=text,
            payload=payload or {},
            dedup_key=dedup_key,
        )
        if dedup_key is not None:
            # Index PARTIEL (uq_channel_messages_dedup ... WHERE dedup_key IS NOT NULL) :
            # Postgres ne peut l'inférer comme arbitre ON CONFLICT que si l'INSERT
            # répète le même prédicat. Sans `index_where`, il lève « no unique or
            # exclusion constraint matching the ON CONFLICT specification ».
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["channel_id", "dedup_key"],
                index_where=sa_text("dedup_key IS NOT NULL"),
            )
        row = (
            await s.execute(
                stmt.returning(
                    ChannelMessage.id,
                    ChannelMessage.channel_id,
                    ChannelMessage.author_id,
                    ChannelMessage.author_role,
                    ChannelMessage.kind,
                    ChannelMessage.text,
                    ChannelMessage.payload,
                    ChannelMessage.created_at,
                )
            )
        ).mappings().first()
        await s.commit()
    if row is None:
        return None  # doublon (dedup_key) → déjà publié, ne pas rejouer
    msg = {
        "id": row["id"],
        "channel_id": row["channel_id"],
        "author_id": row["author_id"],
        "author_role": row["author_role"],
        "kind": row["kind"],
        "text": row["text"],
        "payload": row["payload"] or {},
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    await nc.publish(subjects.channel_events(channel_id), json.dumps(msg).encode())
    return msg
