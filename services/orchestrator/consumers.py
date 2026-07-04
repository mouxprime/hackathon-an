"""Consumers de l'orchestrateur : le consumer principal (FSM) et le consumer de leases.

Consumer principal — pour chaque événement : lock conv → (préparation LLM HORS
transaction) → transition fencée → publication post-commit → ack. Tout est
idempotent : un message rejoué deux fois n'a aucun effet supplémentaire.

Consumer de leases — consomme les heartbeats `progress`, prolonge le lease des
tâches et relaie la progression en live. Pas de lock : UPDATE ciblé par corr_id.
"""

import asyncio
import logging
import random
from collections import defaultdict
from datetime import timedelta

from nats.errors import TimeoutError as NatsTimeoutError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm.exc import StaleDataError

import fsm
from fsm import ConvSnapshot, TransitionContext
from hemicycle_shared import nats_io, subjects
from hemicycle_shared.artifact_store import put_artifact
from hemicycle_shared.config import settings
from hemicycle_shared.db import Conversation, Event, ExecutionTrace, ProcessedMessage, Task
from hemicycle_shared.messages import (
    Progress,
    Result,
    TaskTimeout,
    UIEvent,
    UserInput,
    WriteReport,
)

log = logging.getLogger("orchestrator.consumers")


class _LostLock(Exception):
    """Levée si la version de la conv a bougé pendant notre appel LLM : le lock a été perdu."""


# Compteur in-memory des waiters par conv : borne l'empilement de messages d'une même conv
# en attente du lock (anti-famine, fetch(1)/pod → concurrence déjà limitée par le nb de pods).
_conv_waiters: dict[str, int] = defaultdict(int)


async def _lock_wait_heartbeat(msg) -> None:
    """Tâche asyncio INDÉPENDANTE : prolonge l'ack JetStream pendant l'attente du lock.

    Cadence ≤ ⅓ de `ack_wait` (`lock_wait_heartbeat_s`) → tant qu'on attend le lock, le
    serveur ne redélivre pas le message (on ne brûle aucune livraison). Jamais inline dans
    la boucle d'attente : annulée/nettoyée en `finally` par l'appelant quoi qu'il arrive.
    """
    try:
        while True:
            await asyncio.sleep(settings.lock_wait_heartbeat_s)
            try:
                await msg.in_progress()
            except Exception:
                pass
    except asyncio.CancelledError:
        pass


async def _acquire_with_wait(lock, msg, conv_id: str) -> bool:
    """Acquiert le lock conv avec une attente in-process BORNÉE, au lieu d'un nak immédiat.

    Le lock est tenu pendant tout l'appel LLM (30-180 s) ; un nak immédiat en boucle
    brûlerait les redélivrances du message concurrent → il disparaîtrait. On attend donc
    le lock sur place, en heartbeatant l'ack via une tâche indépendante.

    Retourne True si le lock est acquis (l'appelant DOIT le `release()`), False si on a
    renoncé — un `nak` a alors déjà été émis (redélivrance plus tard, DLQ'able via 3.2).
    """
    # Tentative immédiate : le cas non-contendu ne paie aucun surcoût.
    if await lock.acquire():
        return True
    # Lock occupé : borne le nombre de waiters de cette conv (rejet rapide au-delà → pas
    # d'empilement, pas de famine ; le message sera re-livré plus tard).
    if _conv_waiters[conv_id] >= settings.max_conv_waiters:
        await msg.nak(delay=random.uniform(0.5, 2.0))
        return False
    _conv_waiters[conv_id] += 1
    hb = asyncio.create_task(_lock_wait_heartbeat(msg))
    try:
        deadline = asyncio.get_running_loop().time() + settings.lock_wait_budget_s
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(settings.lock_wait_poll_s)
            # Un lock orphelin (détenteur crashé) expire via le TTL du bucket KV `locks`
            # (conv_lock_ttl_s=30 s ≪ budget=120 s) : `create()` finit par réussir, un waiter
            # reprend la main. Le fence Postgres reste le garde-fou final.
            if await lock.acquire():
                return True
        # Budget épuisé : ALORS SEULEMENT nak (sera DLQ'd, plus perdu, grâce au seuil 3.2).
        await msg.nak(delay=random.uniform(0.5, 2.0))
        return False
    finally:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
        # Décrément propre quoi qu'il arrive (pas de fuite du compteur).
        _conv_waiters[conv_id] -= 1
        if _conv_waiters[conv_id] <= 0:
            _conv_waiters.pop(conv_id, None)


def _route(msg) -> tuple[str, str, object, str | None]:
    """Déduit (conv_id, event_type, message parsé, dedup_id) à partir d'un message brut du bus."""
    subj = msg.subject
    if subj.startswith("user.input."):
        m = UserInput.from_bytes(msg.data)
        # Discrimination par intent : cancel, plan_submit, compact, memory_decision → événements FSM distincts.
        event_type = {
            "cancel": "user_cancel",
            "plan_submit": "plan_submit",
            "compact": "user_compact",
            "memory_decision": "memory_decision",
        }.get(m.intent, "user_input")
        return m.conv_id, event_type, m, m.msg_id
    if subj.startswith("agent.") and ".result." in subj:
        m = Result.from_bytes(msg.data)
        return (
            m.conv_id,
            "subagent_result",
            m,
            None,
        )  # idempotence portée par tasks.status
    if subj == subjects.TASK_TIMEOUT:
        m = TaskTimeout.from_bytes(msg.data)
        return m.conv_id, "task_timeout", m, m.msg_id
    if subj == subjects.WRITE_REPORT:
        m = WriteReport.from_bytes(msg.data)
        return m.conv_id, "write_report", m, m.msg_id
    raise ValueError(f"subject inattendu: {subj}")


async def _to_dlq(js, msg, reason: str) -> None:
    """Envoie un message empoisonné en dead-letter — le dashboard le surveille."""
    await js.publish(
        subjects.dlq("orchestrator"),
        msg.data,
        headers={"X-DLQ-Reason": reason, "X-Orig-Subject": msg.subject},
    )
    log.warning("DLQ ← %s : %s", msg.subject, reason)


async def _run_transition(s, conv_id, snapshot, parsed, event_type, handler, prepared):
    """Dans la transaction ouverte : charge la conv, vérifie le fence inter-phases, applique.

    Accumule les tokens LLM dans `conv.context_tokens` après chaque transition,
    SAUF pour `user_compact` où `h_compact` pose lui-même la valeur finale (re-base).
    """
    conv = await s.get(Conversation, conv_id)
    if conv is None:
        conv = Conversation(
            conv_id=conv_id,
            user_id=snapshot.user_id,
            title=getattr(parsed, "text", "Conversation")[:60],
            lang=snapshot.lang,
        )
        s.add(conv)
        await s.flush()
    # Mémorise la langue courante (le drapeau a pu changer depuis le tour précédent).
    conv.lang = snapshot.lang
    # Fence inter-phases : la conv a-t-elle bougé pendant notre appel LLM ? Si oui, le
    # lock a été perdu → on n'écrit pas en aveugle (le fence est l'autorité, invariant n°3).
    if snapshot.version >= 0 and conv.version != snapshot.version:
        raise _LostLock(conv_id)
    ctx = TransitionContext(session=s, conv=conv, event=parsed, event_type=event_type)
    next_state = await handler(ctx, prepared)
    conv.fsm_state = next_state
    conv.lock_holder = settings.instance_id
    conv.lock_expires_at = fsm._now() + timedelta(seconds=settings.conv_lock_ttl_s)
    # Accumulation des tokens LLM — sauf user_compact qui re-base lui-même context_tokens.
    if event_type != "user_compact":
        extra_tokens = (prepared or {}).get("tokens") or 0
        conv.context_tokens = (conv.context_tokens or 0) + extra_tokens
    return ctx


async def _publish_outcome(js, nc, sm, ctx: TransitionContext) -> None:
    """Après commit : persiste les artefacts, publie les événements UI, les dispatches et l'interne."""
    # Artefacts d'abord : un worker aval ne sera dispatché qu'une fois cette étape `done`,
    # mais on stocke avant d'acker pour qu'un crash/rejeu re-stocke le même artefact (idempotent).
    for env in ctx.pending_artifacts:
        await put_artifact(js, env)
    for ev in ctx.pending_ui:
        # Nats-Msg-Id déterministe : rend le publish idempotent dans la fenêtre de dédup.
        # Clé miroir utilisée par run_events_reconciler → les deux publications sont
        # mutuellement idempotentes ; un re-publish dans la fenêtre ne duplique pas le stream.
        await js.publish(
            subjects.orch_events(ev.conv_id),
            ev.to_bytes(),
            headers={"Nats-Msg-Id": f"uiev:{ev.conv_id}:{ev.seq}"},
        )
    for td in ctx.pending_dispatches:
        # td.msg_id est None pour un dispatch initial (dédup par corr_id, idempotence
        # outbox) et `{corr_id}:{attempt}` pour un retry (franchit la fenêtre de dédup).
        await nats_io.publish_dispatch(
            js, subjects.agent_task(td.agent_type), td.to_bytes(), td.corr_id, td.msg_id
        )
        # Outbox light : la tâche est publiée → on horodate published_at.
        async with sm() as s:
            await s.execute(
                update(Task)
                .where(Task.corr_id == td.corr_id)
                .values(published_at=fsm._now())
            )
            await s.commit()
    for internal in ctx.pending_internal:
        subj = (
            subjects.TASK_TIMEOUT
            if isinstance(internal, TaskTimeout)
            else subjects.WRITE_REPORT
        )
        await js.publish(
            subj, internal.to_bytes(), headers={"Nats-Msg-Id": internal.msg_id}
        )


async def _process(msg, nc, js, kv, sm) -> None:
    """Traite un message du consumer principal de bout en bout."""
    meta = msg.metadata
    # Seuil DLQ applicatif distinct (et strictement < max_deliver, le cap serveur) : sans ça,
    # le serveur cessait de redélivrer à num_delivered = max_deliver alors que ce test exigeait
    # num_delivered > max_deliver → branche INATTEIGNABLE, message perdu sans DLQ (off-by-one).
    if meta.num_delivered > settings.dlq_threshold:
        await _to_dlq(js, msg, f"num_delivered={meta.num_delivered} > dlq_threshold")
        await msg.term()
        return
    try:
        conv_id, event_type, parsed, dedup_id = _route(msg)
    except Exception as e:
        await _to_dlq(js, msg, f"message illisible: {e}")
        await msg.term()
        return

    # 1. Lock par conversation : sérialise tout le traitement de cette conv.
    # Contention : attente in-process bornée du lock (heartbeat indépendant) plutôt qu'un nak
    # immédiat qui brûlerait les redélivrances pendant que le détenteur fait son LLM (30-180 s).
    lock = nats_io.ConvLock(kv, conv_id, settings.instance_id)
    if not await _acquire_with_wait(lock, msg, conv_id):
        return  # budget épuisé ou trop de waiters → nak déjà émis (DLQ'able via 3.2)
    try:
        # --- Phase A : lecture du snapshot (hors transaction d'écriture) ---
        async with sm() as s:
            conv = await s.get(Conversation, conv_id)
            # Récupération anti-blocage : un write_report reçu alors que la conv est
            # ENCORE en WRITING_REPORT signale que la rédaction précédente n'a PAS abouti
            # (re-entrée sur le même turn, ou crash après le commit du registre). On le
            # RETRAITE au lieu de le dropper comme doublon — sinon la conv reste gelée
            # indéfiniment : le sweeper ré-émet en boucle un msg_id que le registre
            # processed_messages rejette systématiquement (cf. _sweep_stuck_conversations).
            recover_write_report = (
                event_type == "write_report"
                and conv is not None
                and conv.fsm_state == "WRITING_REPORT"
            )
            if (
                dedup_id
                and not recover_write_report
                and await s.get(ProcessedMessage, dedup_id) is not None
            ):
                await msg.ack()  # doublon déjà traité → drop
                return
            if conv is None and event_type != "user_input":
                await msg.ack()  # événement orphelin → drop
                return
            snapshot = ConvSnapshot(
                conv_id=conv_id,
                user_id=conv.user_id
                if conv
                else getattr(parsed, "user_id", "analyst-1"),
                fsm_state=conv.fsm_state if conv else "IDLE",
                version=conv.version if conv else -1,
                plan=dict(conv.plan) if conv else {},
                # Langue : celle du message courant (drapeau du header) prime, sinon
                # celle déjà mémorisée sur la conv, sinon anglais. Un message non-user
                # (Result/Timeout/WriteReport) n'a pas de lang → on garde celle de la conv.
                lang=(
                    getattr(parsed, "lang", None)
                    or (conv.lang if conv else None)
                    or "en"
                ),
            )

        handler = fsm.TRANSITIONS.get((snapshot.fsm_state, event_type))
        if handler is None:
            await msg.ack()  # pas de transition → drop idempotent
            return

        # --- Préparation LLM : HORS transaction (on ne tient jamais un txn pendant un LLM) ---
        # `sm` est passé pour les preparations qui ont besoin d'une session courte (mémoire).
        prep = fsm.LLM_PREP.get((snapshot.fsm_state, event_type))
        prepared = await prep(snapshot, parsed, sm) if prep else None

        # --- Phase B : transaction d'écriture courte et fencée ---
        ctx = None
        async with sm() as s:
            async with s.begin():
                if dedup_id and not recover_write_report:
                    res = await s.execute(
                        pg_insert(ProcessedMessage)
                        .values(msg_id=dedup_id, kind=event_type)
                        .on_conflict_do_nothing()
                    )
                    if res.rowcount == 0:
                        log.info("doublon tardif msg_id=%s → drop", dedup_id)
                    else:
                        ctx = await _run_transition(
                            s, conv_id, snapshot, parsed, event_type, handler, prepared
                        )
                else:
                    # event sans msg_id (idempotence portée ailleurs, ex. tasks.status),
                    # OU récupération d'un write_report encore bloqué en WRITING_REPORT :
                    # on (re)joue la transition. On upsert quand même le msg_id pour que
                    # les redélivrances ultérieures — une fois la conv sortie de
                    # WRITING_REPORT — soient bien dédupliquées.
                    if dedup_id:
                        await s.execute(
                            pg_insert(ProcessedMessage)
                            .values(msg_id=dedup_id, kind=event_type)
                            .on_conflict_do_nothing()
                        )
                    ctx = await _run_transition(
                        s, conv_id, snapshot, parsed, event_type, handler, prepared
                    )
            # commit ici : version_id_col refait le fence intra-transaction.

        if ctx is None:
            await msg.ack()  # doublon → drop
            return
        await _publish_outcome(js, nc, sm, ctx)
        await msg.ack()  # ack APRÈS commit + publication
    except (_LostLock, StaleDataError):
        log.warning("fence échoué conv=%s (lock perdu=%s) → nak", conv_id, lock.lost)
        await msg.nak(delay=random.uniform(0.5, 2.0))
    except Exception:
        log.exception("erreur de traitement conv=%s subj=%s", conv_id, msg.subject)
        await msg.nak(delay=random.uniform(1.0, 3.0))
    finally:
        await lock.release()


async def run_main_consumer(nc, js, kv, sm) -> None:
    """Boucle du consumer principal : multiplexe USER + RESULTS + INTERNAL.

    Traitement **concurrent** des messages avec une borne (sémaphore) : chaque
    message est dispatché dans une tâche, donc deux conversations différentes
    n'attendent jamais l'une sur l'autre. Le lock par conv (KV) sérialise quand
    deux messages visent la même conv ; la contention donne un nak+jitter, pas
    une attente bloquante du loop.
    """
    subs = [
        await nats_io.durable_pull(js, "USER", "orch-user", subjects.USER_INPUT_ALL),
        await nats_io.durable_pull(
            js, "RESULTS", "orch-result", subjects.AGENT_RESULT_ALL
        ),
        await nats_io.durable_pull(
            js, "INTERNAL", "orch-internal", subjects.INTERNAL_ALL
        ),
    ]
    log.info(
        "consumer principal démarré (concurrence=%d)", settings.orchestrator_concurrency
    )
    sem = asyncio.Semaphore(settings.orchestrator_concurrency)
    inflight: set[asyncio.Task] = set()

    async def _bounded(msg) -> None:
        """Exécute `_process` sous la borne de concurrence."""
        async with sem:
            try:
                await _process(msg, nc, js, kv, sm)
            except Exception:
                log.exception(
                    "_process : exception non gérée (msg ack/nak peut avoir manqué)"
                )

    while True:
        idle = True
        for sub in subs:
            try:
                msgs = await sub.fetch(8, timeout=1)
            except (NatsTimeoutError, asyncio.TimeoutError):
                continue
            for msg in msgs:
                idle = False
                t = asyncio.create_task(_bounded(msg))
                inflight.add(t)
                t.add_done_callback(inflight.discard)
        if idle:
            await asyncio.sleep(0.05)


# Kinds de trace relayés en live mais NON persistés dans execution_trace : les
# tokens d'un résumé d'agent en cours de rédaction (haut débit). La pyramide ne garde
# que thinking/tool_call/tool_result ; le résumé final vit dans le widget résultat durable.
_EPHEMERAL_TRACE_KINDS = {"draft_chunk"}


async def run_lease_consumer(nc, js, sm) -> None:
    """Consomme les heartbeats `progress` : prolonge le lease et relaie la progression en live."""
    sub = await nats_io.durable_pull(
        js, "PROGRESS", "orch-lease", subjects.AGENT_PROGRESS_ALL
    )
    log.info("consumer de leases démarré")
    while True:
        try:
            msgs = await sub.fetch(20, timeout=2)
        except (NatsTimeoutError, asyncio.TimeoutError):
            continue
        for msg in msgs:
            try:
                prog = Progress.from_bytes(msg.data)
                # Chunks de tokens d'un agent en cours de rédaction : relayés en live
                # mais JAMAIS persistés (un INSERT par token exploserait execution_trace).
                # On court-circuite la session DB — relais direct, comme le report streaming.
                if prog.kind in _EPHEMERAL_TRACE_KINDS:
                    await nc.publish(subjects.orch_progress(prog.conv_id), msg.data)
                    await msg.ack()
                    continue
                async with sm() as s:
                    if prog.kind == "progress":
                        # Heartbeat lease classique : prolonge le lease + maj du Task.
                        # UPDATE ciblé par corr_id, idempotent — pas besoin du lock conv.
                        # Contrat 3.3 : le dispatch est inséré en `queued` avec lease_until=NULL
                        # (le lease ne court PAS en file) ; c'est CE premier heartbeat qui démarre
                        # le lease — bascule queued/pending → running ET pose lease_until.
                        # Un lease_until NULL initial est géré sans souci (on l'écrase ici).
                        await s.execute(
                            update(Task)
                            .where(
                                Task.corr_id == prog.corr_id,
                                Task.status.in_(["queued", "pending", "running"]),
                            )
                            .values(
                                status="running",
                                worker_id=prog.worker_id,
                                progress_pct=prog.pct,
                                progress_note=prog.note,
                                lease_until=fsm._now()
                                + timedelta(seconds=settings.lease_ttl_s),
                            )
                        )
                    else:
                        # Trace riche : INSERT idempotent par guard (corr_id, kind, ts).
                        # prog.ts est le timestamp de création du message côté worker —
                        # stable sur redélivrance NATS (mêmes bytes → même ts). Deux traces
                        # légitimement distinctes du même agent ont des ts différents
                        # (production séquentielle) → pas de faux dédoublonnage.
                        # On passe ts=prog.ts explicitement pour rendre la colonne ts
                        # déterministe (anciennement default=_now, non idempotent).
                        exists = await s.scalar(
                            select(ExecutionTrace.id).where(
                                ExecutionTrace.corr_id == prog.corr_id,
                                ExecutionTrace.kind == prog.kind,
                                ExecutionTrace.ts == prog.ts,
                            ).limit(1)
                        )
                        if exists is None:
                            s.add(
                                ExecutionTrace(
                                    conv_id=prog.conv_id,
                                    corr_id=prog.corr_id,
                                    agent_id=prog.agent_id,
                                    kind=prog.kind,
                                    data=prog.trace_data or {},
                                    ts=prog.ts,
                                )
                            )
                    await s.commit()
                # Relai live : core NATS, éphémère (la progression/trace est lossy par nature).
                await nc.publish(subjects.orch_progress(prog.conv_id), msg.data)
                await msg.ack()
            except Exception:
                log.exception("lease: échec sur %s", msg.subject)
                await msg.nak(delay=1.0)


async def run_events_reconciler(js, sm) -> None:
    """Réconciliateur PG → EVENTS : garantit que tout UIEvent committé en Postgres
    est également publié dans le stream JetStream EVENTS.

    Mécanisme : à chaque cycle, scan des `Event` récents (ts ≥ now − duplicate_window_s)
    et re-publication avec un Nats-Msg-Id déterministe `uiev:{conv_id}:{seq}`. JetStream
    ignore les messages dont l'ID est déjà dans la fenêtre de dédup ; seuls les manquants
    sont ajoutés au stream.

    Cela ferme la fenêtre de perte entre le commit Postgres et le `js.publish` post-commit
    dans `_publish_outcome` : tout crash dans cet intervalle est récupéré dès le prochain
    cycle (≤ duplicate_window_s / 3 secondes après l'incident, soit ≤ 10 s par défaut).

    Prérequis : `_publish_outcome` utilise la même clé `uiev:{conv_id}:{seq}` comme
    Nats-Msg-Id → les deux publications sont mutuellement idempotentes dans la fenêtre.
    Hors fenêtre (crash > duplicate_window_s sans redémarrage) : duplicats possibles dans
    EVENTS, mais le frontend dédoublonne par `seq` et N1 rendra le stream éphémère
    (max_age court) — PG REST `GET /api/events/{conv}` devient alors l'autorité de replay.
    """
    interval_s = max(settings.duplicate_window_s / 3, 10.0)
    log.info(
        "réconciliateur UIEvents démarré (intervalle %.0fs, fenêtre dédup %.0fs)",
        interval_s,
        settings.duplicate_window_s,
    )
    while True:
        await asyncio.sleep(interval_s)
        try:
            cutoff = fsm._now() - timedelta(seconds=settings.duplicate_window_s)
            async with sm() as s:
                rows = (
                    await s.execute(
                        select(Event)
                        .where(Event.ts >= cutoff)
                        .order_by(Event.conv_id, Event.seq)
                    )
                ).scalars().all()
            count = 0
            for ev in rows:
                # Reconstruit un UIEvent fidèle à l'original (timestamps préservés)
                # pour que le stream EVENTS soit strictement identique à ce qu'aurait
                # produit _publish_outcome au moment du commit.
                uiev = UIEvent(
                    conv_id=ev.conv_id,
                    seq=ev.seq,
                    type=ev.type,
                    payload=ev.payload,
                    ts=ev.ts,
                )
                await js.publish(
                    subjects.orch_events(ev.conv_id),
                    uiev.to_bytes(),
                    headers={"Nats-Msg-Id": f"uiev:{ev.conv_id}:{ev.seq}"},
                )
                count += 1
            if count:
                log.debug(
                    "réconciliation EVENTS : %d événements re-publiés depuis PG (fenêtre %.0fs)",
                    count,
                    settings.duplicate_window_s,
                )
        except Exception:
            log.exception("réconciliateur UIEvents : cycle en échec")
