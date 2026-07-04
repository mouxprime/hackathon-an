"""Configuration des services, lue depuis l'environnement (valeurs par défaut = docker-compose)."""

import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Réglages partagés. Chaque service en hérite ; les valeurs viennent des variables d'env."""

    model_config = SettingsConfigDict(env_prefix="HEMI_", extra="ignore")

    # --- Infra ---
    nats_url: str = "nats://nats:4222"
    postgres_dsn: str = "postgresql+asyncpg://hemicycle:hemicycle@postgres:5432/hemicycle"
    elasticsearch_url: str = "http://elasticsearch:9200"
    agents_base_url: str = "http://localhost:18000"

    # --- LLM (framework pydantic-ai) : endpoint vLLM/litellm OpenAI-compatible ---
    # Le client `llm_client` parle désormais DIRECTEMENT à cet endpoint (bypass du
    # pool llm-stub). Mêmes variables d'env que celles que portait le llm-stub :
    #   HEMI_LLM_BASE_URL / HEMI_LLM_MODEL / HEMI_LLM_API_KEY
    llm_base_url: str = "http://litellm.default.svc.cluster.local:4000/v1"
    llm_model: str = "qwen3.6-35B-FP8"
    llm_api_key: str = "EMPTY"

    # --- Identité du service (sert au monitoring et au lock_holder) ---
    service_name: str = "unknown"
    instance_id: str = socket.gethostname()
    agent_id: str = "agent_votes"    # type de sub-agent (agent_votes | agent_deputes | agent_lois)

    # --- Tuning résilience (cf. invariants v1) ---
    conv_lock_ttl_s: int = 30        # TTL du lock par conversation (≥ p99 LLM)
    lease_ttl_s: int = 20            # TTL du lease d'une tâche (heartbeat sub-agent)
    pod_heartbeat_ttl_s: int = 12    # TTL de la liveness d'un pod dans KV `pods`
    ack_wait_s: int = 90             # ack_wait orchestrateur = attente_lock_max + traitement_max
    worker_ack_wait_s: int = 30      # ack_wait worker (court : in_progress() étend tant qu'il vit)
    # Cap serveur de redélivrance (large) : le serveur cesse de redélivrer à num_delivered =
    # max_deliver. Volontairement ≫ dlq_threshold pour que la branche DLQ applicative
    # (num_delivered > dlq_threshold) reste atteignable AVANT que le serveur n'abandonne.
    max_deliver: int = 64            # cap serveur de redélivrance (>> dlq_threshold)
    dlq_threshold: int = 16          # seuil DLQ applicatif (strictement < max_deliver)
    max_task_attempts: int = 3       # retries applicatifs avant échec définitif
    sweeper_interval_s: int = 5      # cadence des balayeurs (timeout + outbox)
    stuck_grace_s: int = 150         # grace « stuck » du sweeper (≥ p99 de rédaction 60-120 s)
    orchestrator_concurrency: int = 32   # convs traitées en parallèle par instance (audit point 1)
    # TTL du registre d'idempotence : ≥ horizon de redélivrance (ack_wait × max_deliver + naks)
    # avec max_deliver=64, sinon un message rejoué tardivement serait retraité.
    processed_messages_ttl_s: int = 3600  # TTL applicatif du registre d'idempotence (GC)

    # --- Attente in-process du lock conv (item 3.1) ---
    # Plutôt que de nak immédiatement quand le lock conv est tenu (l'appel LLM dure 30-180 s),
    # un message concurrent attend le lock in-process, en envoyant des heartbeats in_progress()
    # pour ne pas brûler ses redélivrances. Au-delà du budget → nak (DLQ'able grâce à 3.2).
    lock_wait_budget_s: float = 120.0     # budget d'attente max du lock avant nak (≫ conv_lock_ttl_s)
    lock_wait_heartbeat_s: float = 25.0   # cadence des heartbeats in_progress() (≤ ⅓ de ack_wait)
    lock_wait_poll_s: float = 0.5         # pause entre deux tentatives d'acquisition
    max_conv_waiters: int = 4             # waiters concurrents max par conv (anti-famine)

    # --- Dédup JetStream (item 3.4) ---
    # Fenêtre de dédup explicite des streams : assez courte pour ne pas avaler des retries
    # (qui régénèrent un msg_id distinct), assez longue pour l'idempotence de l'outbox.
    duplicate_window_s: int = 30          # fenêtre de dédup explicite des streams


settings = Settings()
