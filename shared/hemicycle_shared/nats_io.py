"""I/O NATS JetStream : connexion, topologie, lock par conversation, consumers durables.

C'est ici que vit la machinerie de résilience bas niveau partagée par les services.
"""

import asyncio
import logging

import nats
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DiscardPolicy, KeyValueConfig, StreamConfig
from nats.js.errors import BadRequestError
from nats.js.kv import KeyValue

from . import subjects
from .artifact_store import ensure_artifact_store
from .config import settings

log = logging.getLogger("hemicycle.nats")


# ---------------------------------------------------------------------------
# Plafonds JetStream par stream — filet de sécurité anti-gel (cf. N1)
# ---------------------------------------------------------------------------
# Serveur : max_file_store=2 GiB. Sans limite, le stream PROGRESS (~70 % du
# volume : heartbeats de lease + traces + draft_chunk un message durable par
# token d'agent) sature le serveur → TOUTE publication refusée, gel total
# non auto-réparable en air-gap. Ces plafonds évincent les vieux messages
# (DiscardOld, déjà le défaut de nats-py) sans refuser les nouvelles
# publications → dégradation gracieuse au lieu de gel catastrophique.
#
# EVENTS : PAS de limite ici. Le replay UI vient aujourd'hui de ce stream ;
# un max_age court tronquerait l'historique des conversations actives. La
# bascule replay → Postgres (gateway, item G2) est un PRÉREQUIS DÉFÉRÉ (N1) ;
# borner EVENTS seulement après cette bascule.
#
# TOUTES les valeurs numériques ci-dessous sont des PLACEHOLDERS à calibrer
# en production via `nats server report jetstream` / jsz (cf. N1). Viser au
# moins 2× le volume observé sur la fenêtre de rétention choisie.
# max_age : en secondes (nats-py convertit en ns côté serveur).
# max_bytes : en octets ; None = illimité (défaut StreamConfig).
_STREAM_LIMITS: dict[str, dict] = {
    # Principal vecteur de volume : heartbeats de lease + traces d'exécution
    # + (avant N1) draft_chunk un message durable par token. 24 h suffit :
    # les tokens/traces d'un agent sont sans valeur après la session.
    # placeholder à calibrer via jsz, cf. N1
    "PROGRESS": {"max_age": 24 * 3600, "max_bytes": 800 * 1024 * 1024},  # 24 h / 800 MiB
    # Dispatches de tâches : consommés rapidement, rétention 72 h par sécurité.
    # placeholder à calibrer via jsz, cf. N1
    "TASKS":    {"max_age": 72 * 3600, "max_bytes": 200 * 1024 * 1024},  # 72 h / 200 MiB
    # Résultats finaux d'agents : faible volume, 72 h par sécurité.
    # placeholder à calibrer via jsz, cf. N1
    "RESULTS":  {"max_age": 72 * 3600, "max_bytes": 100 * 1024 * 1024},  # 72 h / 100 MiB
    # Entrées utilisateur : faible volume, 72 h par sécurité.
    # placeholder à calibrer via jsz, cf. N1
    "USER":     {"max_age": 72 * 3600, "max_bytes":  50 * 1024 * 1024},  # 72 h /  50 MiB
    # Événements internes orchestrateur : faible volume, 72 h par sécurité.
    # placeholder à calibrer via jsz, cf. N1
    "INTERNAL": {"max_age": 72 * 3600, "max_bytes":  50 * 1024 * 1024},  # 72 h /  50 MiB
    # Dead-letter : faible volume, 72 h pour audit post-incident.
    # placeholder à calibrer via jsz, cf. N1
    "DLQ":      {"max_age": 72 * 3600, "max_bytes":  50 * 1024 * 1024},  # 72 h /  50 MiB
    # EVENTS : délibérément absent → aucune limite posée (cf. bloc ci-dessus).
}


async def connect(name: str) -> nats.NATS:
    """Ouvre une connexion NATS nommée, avec reconnexion infinie (résilience réseau)."""
    return await nats.connect(
        settings.nats_url, name=name, max_reconnect_attempts=-1, reconnect_time_wait=1
    )


async def ensure_topology(js: JetStreamContext) -> None:
    """Crée ou réconcilie tous les streams durables et les buckets KV du système.

    Stratégie idempotente : tenter `add_stream` / `create_key_value` d'abord ;
    si le stream/bucket existe déjà (BadRequestError 400), appeler `update_stream`
    avec la config déclarée pour appliquer les changements en prod (duplicate_window,
    subjects, etc.). Les champs immuables côté serveur (ex : type de stockage)
    déclenchent une seconde BadRequestError qui est loguée et ignorée (non-fatal).
    """
    for spec in subjects.STREAMS:
        # `duplicate_window` explicite (en secondes ; nats-py le convertit en nanos) :
        # plutôt que de dépendre du défaut serveur (≈ 2 min). Assez court pour ne PAS
        # avaler un retry (qui régénère un Nats-Msg-Id distinct), assez long pour que
        # l'outbox reste idempotent sur le corr_id.
        limits = _STREAM_LIMITS.get(spec["name"], {})
        cfg = StreamConfig(
            name=spec["name"],
            subjects=spec["subjects"],
            duplicate_window=settings.duplicate_window_s,
            # Plafonds de rétention (placeholder à calibrer via jsz, cf. N1).
            # max_age en secondes (nats-py convertit en ns) ; None = illimité (défaut).
            # discard=DiscardPolicy.OLD est déjà le défaut nats-py : quand un plafond
            # est atteint, les vieux messages sont évincés — pas de refus de publication.
            # Explicité ici pour les streams bornés afin de rendre l'intention visible.
            max_age=limits.get("max_age"),
            max_bytes=limits.get("max_bytes"),
            discard=DiscardPolicy.OLD if limits else None,
        )
        try:
            await js.add_stream(cfg)
            log.debug("stream %s : créé", spec["name"])
        except BadRequestError:
            # Stream déjà existant → réconcilier la config déclarée (duplicate_window, etc.).
            try:
                await js.update_stream(cfg)
                log.info("stream %s : config réconciliée", spec["name"])
            except BadRequestError as exc:
                # Champ immuable (ex : type de stockage) : non fatal, on continue.
                log.warning(
                    "stream %s : update_stream refusé (champ immuable ?) — %s",
                    spec["name"], exc,
                )
            except Exception as exc:
                log.error("stream %s : erreur inattendue update_stream — %s", spec["name"], exc)
        except Exception as exc:
            log.error("stream %s : erreur inattendue add_stream — %s", spec["name"], exc)

    # `locks` : TTL = durée du lock par conversation. `pods` : TTL = fraîcheur d'un pod.
    # `tools_config` : sans TTL (config persistante) — réplique de la table Postgres `tools`.
    for bucket, ttl in ((subjects.KV_LOCKS, settings.conv_lock_ttl_s),
                        (subjects.KV_PODS, settings.pod_heartbeat_ttl_s),
                        (subjects.KV_TOOLS_CONFIG, 0)):
        try:
            await js.create_key_value(config=KeyValueConfig(bucket=bucket, ttl=ttl))
            log.debug("bucket KV %s : créé", bucket)
        except BadRequestError:
            # Bucket déjà existant → réconcilier le TTL via le stream sous-jacent (KV_{bucket}).
            # On lit la config serveur et on ne touche que max_age pour ne pas écraser
            # les autres champs gérés par nats-py (deny_delete, allow_rollup_hdrs, etc.).
            kv_stream = f"KV_{bucket}"
            try:
                si = await js.stream_info(kv_stream)
                # ttl=0 signifie « pas de TTL » → max_age=None dans la config NATS.
                await js.update_stream(si.config.evolve(max_age=ttl or None))
                log.info("bucket KV %s : TTL réconcilié", bucket)
            except BadRequestError as exc:
                log.warning(
                    "bucket KV %s : update_stream refusé (champ immuable ?) — %s",
                    bucket, exc,
                )
            except Exception as exc:
                log.error("bucket KV %s : erreur inattendue update — %s", bucket, exc)
        except Exception as exc:
            log.error("bucket KV %s : erreur inattendue create_key_value — %s", bucket, exc)

    # Object Store des artefacts : payloads d'agents adressés par corr_id (chaînage par référence).
    await ensure_artifact_store(js)


async def durable_pull(
    js: JetStreamContext, stream: str, durable: str, subject: str, ack_wait: int | None = None
) -> JetStreamContext.PullSubscription:
    """Crée/attache un consumer durable en mode pull (ack explicite, ack_wait calibré).

    Plusieurs subscribers partageant le même `durable` forment un queue group
    (load-balancing) — c'est ainsi qu'un pool de pods sub-agents se répartit les tâches.
    """
    return await js.pull_subscribe(
        subject,
        durable=durable,
        stream=stream,
        config=ConsumerConfig(
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=ack_wait or settings.ack_wait_s,
            max_deliver=settings.max_deliver,
        ),
    )


async def publish_dispatch(
    js: JetStreamContext, subject: str, data: bytes, corr_id: str, msg_id: str | None = None
) -> None:
    """Publie un dispatch de tâche avec `Nats-Msg-Id = msg_id or corr_id`.

    Par défaut (`msg_id=None`) la dédup porte sur le `corr_id` : c'est ce qui rend le
    republish du balayeur outbox idempotent (republication d'un dispatch jamais publié).
    Une republication de RETRY passe un `msg_id` distinct (`{corr_id}:{attempt}`) pour ne
    PAS se faire avaler par la fenêtre de dédup — sinon `max_task_attempts` est un no-op.
    """
    await js.publish(subject, data, headers={"Nats-Msg-Id": msg_id or corr_id})


class ConvLock:
    """Lock distribué par conversation, sur le bucket KV `locks`.

    Acquisition atomique (`create`), renouvellement périodique (`update` avec
    révision), libération. Si un renouvellement échoue, le lock est considéré
    PERDU (`.lost`) : un autre orchestrateur a pu le reprendre après expiration
    du TTL. Le garde-fou final reste le fence `WHERE version = X` côté Postgres.
    """

    def __init__(self, kv: KeyValue, conv_id: str, holder: str):
        self.kv = kv
        self.conv_id = conv_id
        self.holder = holder.encode()
        self._revision: int | None = None
        self._renew_task: asyncio.Task | None = None
        self._lost = False

    @property
    def lost(self) -> bool:
        """Vrai si un renouvellement a échoué — l'appelant doit alors s'abstenir d'écrire en aveugle."""
        return self._lost

    async def acquire(self) -> bool:
        """Tente l'acquisition atomique. Retourne False si la conv est déjà verrouillée."""
        try:
            self._revision = await self.kv.create(self.conv_id, self.holder)
        except Exception:
            # Clé déjà présente (lock tenu) ou erreur transitoire : dans les deux
            # cas la bonne réponse de l'appelant est nak + retry.
            return False
        self._renew_task = asyncio.create_task(self._renew_loop())
        return True

    async def _renew_loop(self) -> None:
        """Renouvelle le lock toutes les TTL/3 ; marque `.lost` au premier échec."""
        interval = settings.conv_lock_ttl_s / 3
        while True:
            await asyncio.sleep(interval)
            try:
                self._revision = await self.kv.update(
                    self.conv_id, self.holder, last=self._revision
                )
            except Exception:
                self._lost = True
                return

    async def release(self) -> None:
        """Arrête le renouvellement et libère le lock (sans effet s'il est déjà perdu)."""
        if self._renew_task:
            self._renew_task.cancel()
        if not self._lost:
            try:
                await self.kv.delete(self.conv_id)
            except Exception:
                pass


async def kv_entries(kv: KeyValue) -> dict[str, bytes]:
    """Lit toutes les paires d'un bucket KV (utilisé par le dashboard pour la liveness des pods)."""
    out: dict[str, bytes] = {}
    try:
        keys = await kv.keys()
    except Exception:
        return out  # bucket vide
    for key in keys:
        try:
            entry = await kv.get(key)
            if entry.value is not None:
                out[key] = entry.value
        except Exception:
            pass
    return out
