"""Subjects NATS, leurs constructeurs, et la topologie (streams + buckets KV).

Centraliser ça ici garantit que producteurs et consommateurs parlent le même langage.
"""

# --- Templates de subjects ({} = placeholder à formater) ---
USER_INPUT = "user.input.{conv_id}"
AGENT_TASK = "agent.{agent_id}.task"
AGENT_PROGRESS = "agent.{agent_id}.progress.{corr_id}"
AGENT_RESULT = "agent.{agent_id}.result.{corr_id}"
AGENT_CANCEL = "agent.{agent_id}.cancel.{corr_id}"    # annulation ciblée d'une tâche en vol (core NATS, payload vide)
ORCH_EVENTS = "orchestrator.events.{conv_id}"        # événements UI durables, ordonnés (JetStream)
ORCH_PROGRESS = "orchestrator.progress.{conv_id}"    # progression live, éphémère (core NATS)
CHANNEL_EVENTS = "channel.events.{id}"               # messages de channel live, éphémères (core NATS)
DLQ = "dlq.agent.{agent_id}"
TASK_TIMEOUT = "orchestrator.internal.task_timeout"  # événement interne : lease/timeout expiré
WRITE_REPORT = "orchestrator.internal.write_report"  # événement interne : déclenche la rédaction

# --- Wildcards pour les consumers ---
USER_INPUT_ALL = "user.input.>"
AGENT_TASK_ALL = "agent.*.task"
AGENT_PROGRESS_ALL = "agent.*.progress.>"
AGENT_RESULT_ALL = "agent.*.result.>"
AGENT_CANCEL_ALL = "agent.*.cancel.>"                # tous les cancels (bridge A2A : sert plusieurs agent_ids)
ORCH_EVENTS_ALL = "orchestrator.events.>"
INTERNAL_ALL = "orchestrator.internal.>"

# --- Constructeurs (un par subject paramétré) ---
def user_input(conv_id: str) -> str:
    """Subject d'entrée utilisateur pour une conversation donnée."""
    return USER_INPUT.format(conv_id=conv_id)


def agent_task(agent_id: str) -> str:
    """Subject de dispatch de tâches vers un type d'agent (consommé en queue group)."""
    return AGENT_TASK.format(agent_id=agent_id)


def agent_progress(agent_id: str, corr_id: str) -> str:
    """Subject de progression/heartbeat d'une tâche en cours."""
    return AGENT_PROGRESS.format(agent_id=agent_id, corr_id=corr_id)


def agent_result(agent_id: str, corr_id: str) -> str:
    """Subject de résultat final d'une tâche."""
    return AGENT_RESULT.format(agent_id=agent_id, corr_id=corr_id)


def agent_cancel(agent_id: str, corr_id: str) -> str:
    """Subject d'annulation ciblée d'une tâche en vol (core NATS, payload vide).

    Le corr_id voyage DANS le subject (pas dans le payload) : un souscripteur
    l'extrait par `msg.subject.rsplit(".", 1)[-1]`. Hors JetStream (cf. `STREAMS`) :
    une annulation est utile « maintenant » ou jamais — pas de replay au reconnect.
    """
    return AGENT_CANCEL.format(agent_id=agent_id, corr_id=corr_id)


def agent_cancel_sub(agent_id: str) -> str:
    """Wildcard de souscription des annulations d'UN type d'agent (worker SDK)."""
    return f"agent.{agent_id}.cancel.>"


def orch_events(conv_id: str) -> str:
    """Subject des événements d'affichage durables et ordonnés (JetStream, replayables)."""
    return ORCH_EVENTS.format(conv_id=conv_id)


def orch_progress(conv_id: str) -> str:
    """Subject de progression live — core NATS, éphémère (la progression est lossy par nature)."""
    return ORCH_PROGRESS.format(conv_id=conv_id)


def channel_events(channel_id: str) -> str:
    """Subject de fan-out live d'un channel — core NATS, éphémère.

    Volontairement HORS JetStream (cf. `STREAMS`) : l'historique d'un channel vit
    en Postgres et se recharge par REST. Le bus ne sert qu'à pousser les nouveaux
    messages aux WS connectés — pas de replay au reconnect (qui serait lourd).
    """
    return CHANNEL_EVENTS.format(id=channel_id)


def dlq(agent_id: str) -> str:
    """Subject dead-letter pour un type d'agent."""
    return DLQ.format(agent_id=agent_id)


# --- Topologie JetStream : streams durables (audit + crash-recovery) ---
STREAMS = [
    {"name": "USER", "subjects": [USER_INPUT_ALL]},
    {"name": "TASKS", "subjects": [AGENT_TASK_ALL]},
    {"name": "PROGRESS", "subjects": [AGENT_PROGRESS_ALL]},
    {"name": "RESULTS", "subjects": [AGENT_RESULT_ALL]},
    {"name": "EVENTS", "subjects": [ORCH_EVENTS_ALL]},
    {"name": "DLQ", "subjects": ["dlq.agent.>"]},
    {"name": "INTERNAL", "subjects": [INTERNAL_ALL]},
]

# --- Buckets KV : état runtime volatil (lock par conv, liveness des pods, conf tools) ---
KV_LOCKS = "locks"
KV_PODS = "pods"
KV_TOOLS_CONFIG = "tools_config"          # clé {agent_id} → {tool_name: {enabled, paused}}
