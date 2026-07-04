"""Schémas Pydantic des messages qui circulent sur le bus NATS.

Tout message hérite de `BusMessage` qui fournit la (dé)sérialisation. Le contrat
de chaque message est figé ici : un changement de schéma se voit en un seul endroit.
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    """Horodatage UTC courant (factorisé pour les valeurs par défaut)."""
    return datetime.now(timezone.utc)


class BusMessage(BaseModel):
    """Base commune : sérialisation JSON ↔ bytes pour le transport NATS."""

    def to_bytes(self) -> bytes:
        """Sérialise le message en JSON encodé UTF-8 pour publication."""
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, data: bytes):
        """Reconstruit un message typé depuis le payload brut d'un message NATS."""
        return cls.model_validate_json(data)


class UserInput(BusMessage):
    """Message utilisateur entrant, publié par le gateway sur `user.input.{conv_id}`.

    `msg_id` (uuid généré par le gateway) est la clé d'idempotence : une
    redélivrance du même message est détectée et ignorée par l'orchestrateur.

    `intent` discrimine les natures de message :
      - "message"      (défaut) : prompt textuel — comportement existant ;
      - "cancel"                : annulation à la demande (double-Esc UI) — la FSM
                                  cancelle les tâches en vol et passe à FAILED ;
      - "plan_submit"           : décision de l'analyste sur le plan proposé
                                  (Approuver / Modifier) — `payload["steps"]`
                                  porte le plan final ordonné à exécuter.

    `payload` transporte les données structurées des intents non-textuels :
      - {"steps": [{agent_type, instruction}, ...]}  pour `plan_submit` ;
      - {"attachments": [{id, filename}, ...]}        pièces jointes d'un message ;
      - {"workflow_steps": [{agent_type, instruction}, ...]}  workflow pré-sélectionné.
    """

    msg_id: str
    conv_id: str
    user_id: str
    text: str
    intent: str = "message"
    # Langue de l'interface au moment de l'envoi ("en" | "fr") — pilote la langue
    # des prompts/LLM et des messages de la FSM. Voyage avec chaque message pour
    # qu'un basculement de drapeau en cours de conversation prenne effet au tour suivant.
    lang: str = "fr"
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=_now)


class ArtifactRef(BaseModel):
    """Référence vers un artefact persisté dans l'Object Store, résoluble par le worker aval.

    `artifact_id` = corr_id de la tâche productrice. `preview` est un extrait texte
    court qui suffit au routeur/LLM (et aux agents A2A) sans charger le payload complet.
    """

    artifact_id: str
    agent_type: str
    preview: str = ""


class TaskDispatch(BusMessage):
    """Tâche dispatchée vers un sub-agent sur `agent.{id}.task`. `corr_id` = clé de corrélation."""

    corr_id: str
    conv_id: str
    agent_type: str
    plan_step_id: str
    instruction: str  # ce que le sub-agent doit accomplir (langage naturel)
    lang: str = "fr"  # langue attendue de la sortie de l'agent ("en" | "fr")
    params: dict[str, Any] = {}
    # Sorties amont à charger PAR RÉFÉRENCE avant le handler (chaînage sans perte).
    # Le worker SDK résout chaque ref depuis l'Object Store et expose le payload
    # structuré complet via params["upstream_artifacts"].
    input_refs: list[ArtifactRef] = Field(default_factory=list)
    hard_timeout_s: int
    # Id de dédup JetStream à porter explicitement à la publication. None → l'appelant
    # retombe sur `corr_id` (dédup outbox idempotente). Une republication de retry pose
    # `f"{corr_id}:{attempt_count}"` pour ne PAS se faire avaler par la fenêtre de dédup
    # (les tentatives partagent le corr_id mais doivent franchir la fenêtre).
    msg_id: str | None = None
    ts: datetime = Field(default_factory=_now)


class Progress(BusMessage):
    """Progression d'une tâche, publiée par le worker. Sert AUSSI de heartbeat (lease).

    `kind` discrimine la nature de l'événement : `progress` est le heartbeat lease
    classique (met à jour `tasks.progress_pct/note`), les autres types sont des
    **traces d'exécution riches** (raisonnement, appels d'outils) qui alimentent
    la pyramide de visualisation côté UI. Les traces transitent par le même
    subject NATS (`agent.{id}.progress.{corr_id}`) → un seul tuyau, un seul
    consumer côté orchestrateur, rétrocompatibilité garantie.
    """

    corr_id: str
    conv_id: str
    agent_id: str
    worker_id: str
    pct: float = 0.0  # avancement 0.0 → 1.0 (significatif si kind="progress")
    note: str = ""  # message lisible ("interrogation des sources…")
    kind: Literal[
        "progress", "thinking", "tool_call", "tool_result", "report_chunk",
        # message_chunk : synthèse de chat de l'orchestrateur streamée (canal orch_progress).
        # draft_chunk : résumé d'un sous-agent en cours de rédaction (éphémère, non persisté).
        "message_chunk", "draft_chunk",
    ] = "progress"
    trace_data: dict[str, Any] | None = None  # payload structuré pour la pyramide
    ts: datetime = Field(default_factory=_now)


class Result(BusMessage):
    """Résultat d'une tâche, publié par le worker sur `agent.{id}.result.{corr_id}`.

    `status` :
      - "done" / "failed"     : issue terminale classique ;
      - "input_required"      : l'agent A2A a besoin d'un complément de l'analyste
        avant de poursuivre (état `input-required` de la spec A2A). `output` porte
        alors `{"question": <texte à afficher>, "context_id": <contextId A2A>}` —
        la FSM affiche la question et attend la réponse, puis re-dispatche le MÊME
        agent avec ce `context_id` pour reprendre la tâche (vrai round-trip A2A).
    """

    corr_id: str
    conv_id: str
    agent_id: str
    worker_id: str
    status: Literal["done", "failed", "input_required"]
    output: dict[
        str, Any
    ] = {}  # résultat structuré (forme dépendante de la discipline)
    # Id de l'artefact stocké pour cette sortie (= corr_id). Présent quand le worker a
    # déjà persisté l'output dans l'Object Store. Absent pour les agents A2A externes
    # (l'orchestrateur miroite alors leur résultat à la réception).
    artifact_id: str | None = None
    error: str | None = None
    ts: datetime = Field(default_factory=_now)


class TaskTimeout(BusMessage):
    """Événement interne émis par le balayeur quand le lease d'une tâche a expiré.

    `msg_id` déterministe (`timeout:{corr_id}:{attempt}`) → idempotent via le registre.
    """

    msg_id: str
    corr_id: str
    conv_id: str
    reason: Literal["lease_expired", "hard_timeout"]
    ts: datetime = Field(default_factory=_now)


class WriteReport(BusMessage):
    """Événement interne qui déclenche la rédaction du rapport (état WRITING_REPORT).

    `msg_id` déterministe (`write_report:{conv_id}:{turn}`) → idempotent via le registre.
    """

    msg_id: str
    conv_id: str
    turn: int
    ts: datetime = Field(default_factory=_now)


# Types de widgets pilotables par l'orchestrateur (server-driven UI).
WidgetType = Literal[
    "markdown",
    "task_progress",
    "agent_card",
    "source_list",
    "image_card",
    "map",
    "report",
    "memory_recall",
    "plan_proposal",
]


class UIEvent(BusMessage):
    """Événement d'affichage, publié sur `orchestrator.events.{conv_id}`.

    `seq` est strictement croissant par conversation : le frontend rend dans l'ordre.
    `type` discrimine le rendu ; `payload` est libre, interprété selon `type`.
    """

    conv_id: str
    seq: int
    type: Literal[
        "message", "widget", "widget_update", "status", "task_update", "spawn_widget"
    ]
    payload: dict[str, Any]
    ts: datetime = Field(default_factory=_now)
