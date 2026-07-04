"""Modèles ORM SQLAlchemy 2.0 — source de vérité unique du schéma Postgres.

Le schéma matérialise les invariants v1 :
- `conversations` porte l'état de la FSM, le compteur de version (fence optimiste)
  et `event_seq` (ordre des événements UI) ;
- `tasks` est la source de vérité de corrélation (`corr_id`) et porte le lease ;
- `agent_types` est le catalogue de l'« Agent Registry ».
"""

from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index,
    Integer, String, Text,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    """Horodatage UTC courant — défaut des colonnes temporelles."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base déclarative commune à tous les modèles."""


class Conversation(Base):
    """État d'une conversation : c'est CETTE ligne que le lock par conv protège.

    `version` est le *fencing token* : déclaré comme `version_id_col`, SQLAlchemy
    émet automatiquement `UPDATE … WHERE version = X` à chaque écriture et lève
    `StaleDataError` si la ligne a bougé — exactement le fence de l'invariant n°3.
    """

    __tablename__ = "conversations"

    conv_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, default="analyst-1")
    title: Mapped[str] = mapped_column(String, default="Nouvelle conversation")
    # Langue de la conversation ("en" | "fr") — défaut anglais. Mise à jour à chaque
    # entrée utilisateur (le drapeau du header voyage avec le message). Lue par la FSM
    # pour localiser ses messages et propagée aux prompts LLM + dispatch des agents.
    lang: Mapped[str] = mapped_column(String, default="en")
    fsm_state: Mapped[str] = mapped_column(String, default="IDLE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # fencing token
    event_seq: Mapped[int] = mapped_column(Integer, default=0)      # ordre des UIEvent
    lock_holder: Mapped[str | None] = mapped_column(String, nullable=True)
    lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan: Mapped[dict] = mapped_column(JSONB, default=dict)         # plan courant (liste d'étapes)
    # Taille de contexte LLM courante (tokens accumulés depuis la dernière compaction).
    context_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Borne de compaction : seq du message résumé (0 = pas encore compacté).
    # `_recent_chat_history` filtre Event.seq >= compacted_seq quand > 0.
    compacted_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    # SQLAlchemy gère `version` : incrément auto + clause WHERE version = X (le fence).
    __mapper_args__ = {"version_id_col": version}


class Task(Base):
    """Une tâche dispatchée à un sub-agent. `corr_id` = clé de corrélation du système."""

    __tablename__ = "tasks"

    corr_id: Mapped[str] = mapped_column(String, primary_key=True)
    conv_id: Mapped[str] = mapped_column(ForeignKey("conversations.conv_id"))
    plan_step_id: Mapped[str] = mapped_column(String)
    agent_type: Mapped[str] = mapped_column(String)
    # pending | running | done | failed | timeout | cancelled
    status: Mapped[str] = mapped_column(String, default="pending")
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    instruction: Mapped[str] = mapped_column(Text, default="")
    input_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)   # échecs RÉELS (≠ num_delivered)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)  # avancement live (lease consumer)
    progress_note: Mapped[str] = mapped_column(String, default="")
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # outbox
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentType(Base):
    """Catalogue unifié des agents — l'Agent Registry.

    `transport` détermine le canal de dispatch :
      - "nats" : worker interne consomme `agent.{id}.task` (sub-agents historiques).
      - "a2a"  : agent A2A externe, l'orchestrateur appelle `card_url` en HTTP.
    Pour un agent A2A, `card_url` doit pointer vers `.../.well-known/agent-card.json`.
    """

    __tablename__ = "agent_types"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    discipline: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    when_to_use: Mapped[str] = mapped_column(Text)          # pilote le routing LLM
    input_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    tools: Mapped[list] = mapped_column(JSONB, default=list)
    # `mcp_servers` est nullable : métadonnée optionnelle, non requise pour activer un agent.
    mcp_servers: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    cost_hint: Mapped[str] = mapped_column(String, default="medium")
    latency_p95_s: Mapped[float] = mapped_column(Float, default=30.0)
    hard_timeout_s: Mapped[int] = mapped_column(Integer, default=120)
    # Transport unifié — défaut "nats" pour compat avec les agents internes.
    transport: Mapped[str] = mapped_column(String, default="nats")
    card_url: Mapped[str | None] = mapped_column(String, nullable=True)
    agent_card: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Tool(Base):
    """Outil exposé par un type d'agent — entrée du catalogue éditable depuis l'UI.

    L'UI peut créer/supprimer un outil et le mettre en pause sans toucher au code.
    L'état (`enabled`, `paused`) est répliqué dans le bucket NATS KV `tools_config`
    par le gateway à chaque mutation, et l'orchestrateur l'injecte dans
    `TaskDispatch.params["tools"]` au planning — les workers ne consultent JAMAIS
    Postgres (invariant n°7).
    """

    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent_types.id"), index=True)
    name: Mapped[str] = mapped_column(String)               # ex. "corpus_search"
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    source: Mapped[str] = mapped_column(String, default="native")  # native | mcp | a2a
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LtmMemory(Base):
    """Mémoire interne long-terme (pgvector) : faits que le système a appris/produit."""

    __tablename__ = "ltm_memory"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conv_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Mémoire de cellule : non-NULL = fait propre à un salon d'état-major (ex. "g1").
    # NULL = mémoire globale/utilisateur (partagée par toutes les cellules).
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String, default="learned_fact")  # learned_fact | entity | preference
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(384))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Event(Base):
    """Journal des UIEvent publiés — sert au replay quand un client se reconnecte."""

    __tablename__ = "events"

    conv_id: Mapped[str] = mapped_column(String, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExecutionTrace(Base):
    """Trace d'exécution riche d'un sub-agent — alimente la pyramide UI.

    Chaque ligne est UN événement de la chaîne de raisonnement d'un agent :
    une pensée (`thinking`), un appel d'outil (`tool_call`), un résultat d'outil
    (`tool_result`). La table est append-only ; l'ordre est porté par `id`
    (auto-increment) et `ts`. Une tâche (`corr_id`) en produit typiquement 3 à 10.
    """

    __tablename__ = "execution_trace"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conv_id: Mapped[str] = mapped_column(String, index=True)
    corr_id: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)           # thinking | tool_call | tool_result
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Workflow(Base):
    """Workflow d'investigation pré-défini, composé par l'analyste sur le canvas.

    `graph` conserve la représentation visuelle React Flow (nodes/edges) pour
    rouvrir le workflow dans l'éditeur. `steps` est la liste **ordonnée** dérivée
    du graphe — c'est elle que l'orchestrateur propose puis exécute séquentiellement
    (un step = {agent_type, instruction}). Les deux sont stockées : `graph` pour
    l'édition, `steps` pour l'exécution (pas de re-tri côté orchestrateur).
    """

    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[str] = mapped_column(String, default="analyst-1")
    graph: Mapped[dict] = mapped_column(JSONB, default=dict)        # {nodes: [...], edges: [...]}
    steps: Mapped[list] = mapped_column(JSONB, default=list)        # [{agent_type, instruction}, ...]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Attachment(Base):
    """Pièce jointe d'une conversation : octets originaux dans le bucket + texte extrait.

    Au upload, le gateway pousse les **octets originaux** dans le bucket de transit
    MinIO `hemicycle` (via le s3-controller) et conserve l'URI dans `s3_uri` ; il extrait
    aussi le texte (`text_content`) pour l'aperçu UI et le contexte de planification.
    C'est l'`s3_uri` (référence courte) qui voyage entre agents — chacun récupère le
    document intégral via `pull_doc_from_bucket`. Les workers ne lisent jamais Postgres
    (invariant n°7) : ils lisent le bucket. `s3_uri`/`object_key` sont NULL si le push
    bucket a échoué — l'orchestrateur retombe alors sur l'inline texte legacy.
    """

    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conv_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String, default="text/plain")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    text_content: Mapped[str] = mapped_column(Text, default="")
    # Référence bucket des octets originaux (forme canonique "s3://hemicycle/<object_key>").
    # NULL = push bucket indisponible/échoué → fallback inline texte côté orchestrateur.
    s3_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    object_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProcessedMessage(Base):
    """Registre d'idempotence : tout message du bus traité une fois y laisse sa trace.

    C'est l'« état en base » de l'invariant n°4 pour les messages qui ne sont pas
    déjà dédupliqués par `corr_id` (entrées utilisateur, événements internes).
    Le handler fait un INSERT ON CONFLICT DO NOTHING en tout premier : si la ligne
    existe déjà, le message est un doublon → ack et drop.
    """

    __tablename__ = "processed_messages"

    msg_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Channel(Base):
    """Salon de discussion de groupe (type Discord) — DISTINCT des conversations FSM.

    Contrairement à `Conversation`, un channel n'a ni lock, ni fence `version`, ni
    plan, ni dispatch d'agents : c'est un fil append-only multi-auteurs. Les channels
    sont seedés de façon idempotente (cf. `hemicycle_shared.channels.CHANNEL_DEFS`) :
    le salon système « hemicycle » (alertes de l'orchestrateur, lecture seule) et les
    salons d'état-major G1..G10.
    """

    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String, primary_key=True)        # slug stable : "hemicycle", "g1".."g10"
    name: Mapped[str] = mapped_column(String)                        # libellé affiché ("G2 Renseignement")
    kind: Mapped[str] = mapped_column(String, default="staff")       # "staff" | "system"
    role: Mapped[str | None] = mapped_column(String, nullable=True)  # rôle d'état-major ("Renseignement")
    description: Mapped[str] = mapped_column(Text, default="")
    readonly: Mapped[bool] = mapped_column(Boolean, default=False)   # True pour "hemicycle" (analystes lecteurs)
    position: Mapped[int] = mapped_column(Integer, default=0)        # ordre d'affichage (system=0, staff 1..10)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChannelMessage(Base):
    """Message posté dans un channel — modèle append-only, ordonné par `id`.

    On ordonne par l'autoincrement `id` (PAS un `seq` par-channel calculé via
    `max(seq)+1`) : l'autoincrement Postgres absorbe nativement les écritures
    concurrentes (plusieurs analystes + orchestrateur) sans course. Le frontend
    déduplique par `id`. `dedup_key` + index unique partiel rendent les futures
    alertes de l'orchestrateur idempotentes face aux redélivrances NATS.
    """

    __tablename__ = "channel_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    author_id: Mapped[str] = mapped_column(String, default="analyst-1")
    author_role: Mapped[str] = mapped_column(String, default="Analyste")  # métadonnée d'affichage
    kind: Mapped[str] = mapped_column(String, default="message")          # "message" | "alert"
    text: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)            # ex. {"conv_id": "..."}
    dedup_key: Mapped[str | None] = mapped_column(String, nullable=True)  # idempotence alertes orchestrateur
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    # Index unique partiel : une seule alerte par (channel, dedup_key) malgré les
    # redélivrances. Partiel (WHERE dedup_key IS NOT NULL) → les messages analystes
    # (dedup_key = NULL) ne sont jamais contraints.
    __table_args__ = (
        Index(
            "uq_channel_messages_dedup",
            "channel_id",
            "dedup_key",
            unique=True,
            postgresql_where=sa_text("dedup_key IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# Données ouvertes de l'Assemblée nationale (17e législature) — « Hémicycle ».
#
# Ces tables sont alimentées par le seed (services/seed/main.py) depuis les ZIP
# officiels de data.assemblee-nationale.fr (AMO acteurs/mandats/organes, scrutins,
# dossiers législatifs). Elles sont la source de vérité LOCALE des sub-agents
# citoyens (agent_votes / agent_deputes / agent_lois).
#
# Recherche floue : les colonnes `*_normalise` (minuscules, sans accents) sont
# remplies au seed en Python (unicodedata) ; les index GIN pg_trgm dessus sont
# créés PAR LE SEED en SQL brut (CREATE INDEX IF NOT EXISTS … gin_trgm_ops) et
# non déclarés ici — sinon `create_all` échouerait au boot d'un service sur un
# volume existant où l'extension pg_trgm n'est pas encore installée.
# ---------------------------------------------------------------------------


class Organe(Base):
    """Organe de l'AN (groupe politique, commission, ministère…) — réf. `PO…`."""

    __tablename__ = "organes"

    uid: Mapped[str] = mapped_column(String, primary_key=True)          # "PO…"
    libelle: Mapped[str] = mapped_column(Text, default="")
    libelle_abrege: Mapped[str] = mapped_column(String, default="")
    type_organe: Mapped[str] = mapped_column(String, default="", index=True)  # GP, COMPER…
    couleur_associee: Mapped[str | None] = mapped_column(String, nullable=True)  # "#RRGGBB"
    effectif: Mapped[int | None] = mapped_column(Integer, nullable=True)  # nb de députés actifs (GP)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)               # JSON open data brut


class Depute(Base):
    """Député de la 17e législature — réf. `PA…`. `actif=False` = ancien député."""

    __tablename__ = "deputes"

    uid: Mapped[str] = mapped_column(String, primary_key=True)          # "PA…"
    nom: Mapped[str] = mapped_column(String, default="")
    prenom: Mapped[str] = mapped_column(String, default="")
    nom_complet: Mapped[str] = mapped_column(String, default="")
    # Version minuscule/sans accents de `nom_complet` — cible de l'index trgm (seed).
    nom_normalise: Mapped[str] = mapped_column(String, default="")
    groupe_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # organes.uid (GP)
    circo_departement: Mapped[str] = mapped_column(String, default="")
    circo_numero: Mapped[str] = mapped_column(String, default="")
    actif: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    url_an: Mapped[str] = mapped_column(String, default="")             # fiche officielle AN
    url_photo: Mapped[str] = mapped_column(String, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)              # JSON open data brut


class Scrutin(Base):
    """Scrutin public de l'AN — réf. `VTANR5L17V{n}`. Décomptes = syntheseVote officiel."""

    __tablename__ = "scrutins"

    uid: Mapped[str] = mapped_column(String, primary_key=True)          # "VTANR5L17V…"
    numero: Mapped[int] = mapped_column(Integer, index=True)
    date_scrutin: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    titre: Mapped[str] = mapped_column(Text, default="")
    # Version minuscule/sans accents de `titre` — cible de l'index trgm (seed).
    titre_normalise: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[str] = mapped_column(String, default="")               # "adopté" | "rejeté"
    demandeur: Mapped[str] = mapped_column(Text, default="")
    pour: Mapped[int] = mapped_column(Integer, default=0)
    contre: Mapped[int] = mapped_column(Integer, default=0)
    abstentions: Mapped[int] = mapped_column(Integer, default=0)
    non_votants: Mapped[int] = mapped_column(Integer, default=0)
    dossier_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # dossiers.uid
    url_an: Mapped[str] = mapped_column(String, default="")             # page publique du scrutin
    url_data: Mapped[str] = mapped_column(String, default="")           # page du jeu open data


class Vote(Base):
    """Position nominative d'un député sur un scrutin (ventilationVotes déplié).

    ⚠️ « non-votant » ≠ « abstention » (cf. skill vulgarisation-loi). Les non-votants
    nominatifs incluent les membres du gouvernement et le président de séance — le
    décompte agrégé `scrutins.non_votants` (officiel) peut donc être inférieur au
    nombre de lignes `nonVotant` ici.
    """

    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scrutin_uid: Mapped[str] = mapped_column(String, index=True)        # scrutins.uid
    acteur_ref: Mapped[str] = mapped_column(String, index=True)         # deputes.uid ("PA…")
    groupe_ref: Mapped[str] = mapped_column(String, default="")         # organes.uid (GP au moment du vote)
    position: Mapped[str] = mapped_column(String)                       # pour|contre|abstention|nonVotant
    par_delegation: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        CheckConstraint(
            "position IN ('pour', 'contre', 'abstention', 'nonVotant')",
            name="ck_votes_position",
        ),
    )


class Dossier(Base):
    """Dossier législatif (parcours d'un texte) — réf. `DLR5L17N…`, sélection curée."""

    __tablename__ = "dossiers"

    uid: Mapped[str] = mapped_column(String, primary_key=True)          # "DLR5L17N…"
    titre: Mapped[str] = mapped_column(Text, default="")
    titre_chemin: Mapped[str] = mapped_column(String, default="")       # slug AN (URL publique)
    # Version minuscule/sans accents de `titre` — cible de l'index trgm (seed).
    titre_normalise: Mapped[str] = mapped_column(Text, default="")
    statut: Mapped[str] = mapped_column(String, default="")             # dernier acte daté (libellé)
    actes: Mapped[dict] = mapped_column(JSONB, default=dict)            # arbre actesLegislatifs brut
    url_an: Mapped[str] = mapped_column(String, default="")             # page publique du dossier


