"""Balayeurs périodiques de l'orchestrateur — la part « auto-réparatrice » de la résilience.

Trois balayeurs, exécutés en boucle :
- timeouts : leases expirés → événement `task_timeout` (la FSM décide retry ou échec) ;
- outbox   : tâches insérées mais jamais publiées (crash entre commit et publish) → republie ;
- stuck    : conv bloquées en WRITING_REPORT (write_report perdu) → ré-émet l'événement.

Les ré-émissions `timeout`/`outbox` portent un `Nats-Msg-Id` déterministe → idempotentes
(registre `processed_messages` + dedup window NATS). Le `stuck` fait EXCEPTION : il porte un
`Nats-Msg-Id` unique par tentative (l'idempotence applicative reste sur `WriteReport.msg_id`,
retraité par le consumer tant que la conv est en WRITING_REPORT) — voir _sweep_stuck_conversations.
"""

import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import delete, select, update

import fsm
from hemicycle_shared import nats_io, subjects
from hemicycle_shared.config import settings
from hemicycle_shared.db import Conversation, ProcessedMessage, Task
from hemicycle_shared.messages import ArtifactRef, TaskDispatch, TaskTimeout, WriteReport

log = logging.getLogger("orchestrator.sweepers")


async def _sweep_timeouts(js, sm) -> None:
    """Tâches dont le lease a expiré → publie un `task_timeout` (lease_expired ou hard_timeout)."""
    now = fsm._now()
    async with sm() as s:
        # lease_until IS NULL = tâche en file, pas encore prise en charge (le lease ne court
        # qu'à partir du premier heartbeat) → jamais déclarée lease_expired (sinon abandon
        # fantôme + consommation indue d'attempt_count). En SQL `NULL < now` est déjà faux,
        # mais on l'explicite pour rendre l'intention claire et robuste.
        rows = (await s.execute(select(Task).where(
            Task.status.in_(["pending", "running"]),
            Task.lease_until.isnot(None),
            Task.lease_until < now))).scalars().all()
    for task in rows:
        # Distinguer lease expiré (le pod est mort → re-dispatcher utile) de hard timeout
        # (la tâche est juste trop longue → re-dispatcher ne sert à rien).
        hard = fsm.AGENT_MAP.get(task.agent_type, {}).get("hard_timeout_s", 120)
        reason = "lease_expired"
        if task.dispatched_at and (now - task.dispatched_at).total_seconds() > hard:
            reason = "hard_timeout"
        tt = TaskTimeout(msg_id=f"timeout:{task.corr_id}:{task.attempt_count}",
                         corr_id=task.corr_id, conv_id=task.conv_id, reason=reason)
        await js.publish(subjects.TASK_TIMEOUT, tt.to_bytes(),
                         headers={"Nats-Msg-Id": tt.msg_id})
        log.info("timeout: %s (%s)", task.corr_id, reason)


async def _sweep_outbox(js, sm) -> None:
    """Tâches `pending` jamais publiées → republie (Nats-Msg-Id = corr_id rend l'op idempotente)."""
    grace = fsm._now() - timedelta(seconds=settings.sweeper_interval_s)
    async with sm() as s:
        # JOIN avec Conversation pour récupérer la langue réelle de la conv — `lang` n'est
        # PAS stocké dans Task.input_payload (la FSM crée le TaskDispatch avec lang=conv.lang
        # mais ne le persiste pas dans la ligne Task). Sans ce join, la re-publication
        # defaultait silencieusement à "en" pour toute conv française.
        rows = (await s.execute(
            select(Task, Conversation.lang)
            .outerjoin(Conversation, Task.conv_id == Conversation.conv_id)
            .where(
                Task.status == "pending",
                Task.published_at.is_(None),
                Task.dispatched_at < grace)
        )).all()
    for task, conv_lang in rows:
        hard = fsm.AGENT_MAP.get(task.agent_type, {}).get("hard_timeout_s", 120)
        # Restitue les input_refs persistées (chaînage par référence) à la re-publication.
        saved = dict(task.input_payload or {})
        refs = [ArtifactRef(**r) for r in saved.pop("input_refs", [])]
        td = TaskDispatch(corr_id=task.corr_id, conv_id=task.conv_id, agent_type=task.agent_type,
                          plan_step_id=task.plan_step_id, instruction=task.instruction,
                          lang=conv_lang or "en",
                          params=saved, input_refs=refs, hard_timeout_s=hard)
        await nats_io.publish_dispatch(js, subjects.agent_task(task.agent_type),
                                       td.to_bytes(), task.corr_id)
        async with sm() as s:
            await s.execute(update(Task).where(Task.corr_id == task.corr_id)
                            .values(published_at=fsm._now()))
            await s.commit()
        log.info("outbox: re-publication de %s", task.corr_id)


async def _sweep_stuck_conversations(js, sm) -> None:
    """Conv figées en WRITING_REPORT (write_report perdu après crash) → ré-émet l'événement."""
    # Grace ≥ p99 de rédaction : les rapports durent légitimement 60–120 s, la garde ne doit
    # plus se déclencher en fonctionnement normal (avant : sweeper_interval_s * 3 = 15 s).
    grace = fsm._now() - timedelta(seconds=settings.stuck_grace_s)
    async with sm() as s:
        rows = (await s.execute(select(Conversation).where(
            Conversation.fsm_state == "WRITING_REPORT",
            Conversation.updated_at < grace))).scalars().all()
    for conv in rows:
        turn = conv.plan.get("turn", 1)
        wr = WriteReport(msg_id=f"write_report:{conv.conv_id}:{turn}",
                         conv_id=conv.conv_id, turn=turn)
        # Nats-Msg-Id UNIQUE par ré-émission : un id déterministe resterait dans la
        # fenêtre de dédup NATS (duplicate_window ≫ sweeper_interval_s), donc le ré-émis
        # n'entrerait JAMAIS dans le stream tant que le sweeper le rafraîchit toutes les
        # 5 s → la conv resterait gelée à vie. L'idempotence applicative est portée par
        # `wr.msg_id` (registre processed_messages), que le consumer retraite tant que la
        # conv est encore en WRITING_REPORT puis dédupe une fois passée en DONE.
        await js.publish(subjects.WRITE_REPORT, wr.to_bytes(),
                         headers={"Nats-Msg-Id": f"{wr.msg_id}:retry-{uuid.uuid4().hex[:12]}"})
        log.info("stuck: ré-émission write_report pour %s", conv.conv_id)


async def _sweep_processed_messages(sm) -> None:
    """GC du registre d'idempotence — supprime les entrées plus vieilles que le TTL applicatif.

    Sans ça, la table grossit indéfiniment (correctness OK mais croissance illimitée).
    Le TTL doit être plus grand que la fenêtre de redélivrance NATS pour que le
    registre reste autoritaire sur les doublons.
    """
    cutoff = fsm._now() - timedelta(seconds=settings.processed_messages_ttl_s)
    async with sm() as s:
        await s.execute(delete(ProcessedMessage).where(ProcessedMessage.processed_at < cutoff))
        await s.commit()


async def run_sweepers(nc, js, sm) -> None:
    """Boucle unique qui exécute les balayeurs à intervalle régulier."""
    log.info("balayeurs démarrés (intervalle %ds)", settings.sweeper_interval_s)
    while True:
        await asyncio.sleep(settings.sweeper_interval_s)
        try:
            await _sweep_timeouts(js, sm)
            await _sweep_outbox(js, sm)
            await _sweep_stuck_conversations(js, sm)
            await _sweep_processed_messages(sm)
        except Exception:
            log.exception("balayeur: cycle en échec")
