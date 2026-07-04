"""SDK worker des sub-agents.

Un worker consomme `agent.{id}.task` en queue group, exécute la tâche via un
handler de discipline, et publie progress puis result sur le bus. Invariant n°7 :
**il ne touche JAMAIS Postgres** — bus + Elasticsearch + LLM pool uniquement.

Mécanismes de résilience portés par le SDK :
- heartbeat `progress` régulier (renouvelle le lease orchestrateur) même sans avancement ;
- `msg.in_progress()` à chaque heartbeat → étend l'ack JetStream tant que le pod vit ;
- liveness du pod écrite dans le bucket KV `pods` (TTL) → consommée par le dashboard ;
- poison message → DLQ après `max_deliver`.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from nats.errors import TimeoutError as NatsTimeoutError

from hemicycle_shared import nats_io, subjects
from hemicycle_shared.a2a import mount_a2a
from hemicycle_shared.artifacts import ArtifactEnvelope, validate_output
from hemicycle_shared.artifact_store import get_artifact, put_artifact
from hemicycle_shared.config import settings
from hemicycle_shared.manifests import AgentManifest
from hemicycle_shared.messages import Progress, Result, TaskDispatch

log = logging.getLogger("subagent.sdk")


class Worker:
    """Boucle d'exécution d'un pod sub-agent (un type d'agent, un pod = une instance)."""

    def __init__(self, agent_id: str, handler, manifest: AgentManifest | None = None):
        """`handler` : async (TaskDispatch, progress, trace) -> dict — produit l'output structuré.

        `manifest` (optionnel mais recommandé) sert à monter le serveur A2A à côté
        du consumer NATS — l'agent est alors consommable comme agent A2A externe.
        """
        self.agent_id = agent_id
        self.handler = handler
        self.manifest = manifest
        self.worker_id = settings.instance_id
        self.status = "starting"          # starting | idle | busy
        self.current_corr: str | None = None
        self._cur_pct = 0.0
        self._cur_note = ""
        self.nc = None
        self.js = None
        self.pods_kv = None
        # Handlers en vol indexés par corr_id : permet d'annuler la task asyncio
        # d'un handler quand l'orchestrateur publie un cancel (réorientation/annulation).
        self._inflight: dict[str, asyncio.Task] = {}
        self._cancel_sub = None

    async def run(self) -> None:
        """Connecte le worker au bus et lance les boucles (liveness + consume + A2A HTTP)."""
        self.nc = await nats_io.connect(f"subagent-{self.agent_id}-{self.worker_id}")
        self.js = self.nc.jetstream()
        await nats_io.ensure_topology(self.js)
        self.pods_kv = await self.js.key_value(subjects.KV_PODS)
        # Souscription d'annulation en CORE NATS (pas JetStream) : un cancel est utile
        # « maintenant » ou jamais, on ne veut ni durabilité ni ack. Couvre tous les
        # corr_id de CE type d'agent (`agent.{id}.cancel.>`).
        self._cancel_sub = await self.nc.subscribe(
            subjects.agent_cancel_sub(self.agent_id), cb=self._on_cancel
        )
        self.status = "idle"
        log.info("worker %s/%s prêt", self.agent_id, self.worker_id)
        coros = [self._liveness_loop(), self._consume_loop()]
        if self.manifest is not None:
            coros.append(self._a2a_server_loop())
        await asyncio.gather(*coros)

    async def _a2a_server_loop(self) -> None:
        """3e boucle : sert l'interface A2A v1.0 (Agent Card + JSON-RPC + SSE) sur `:8600`."""
        port = int(os.environ.get("HEMI_A2A_PORT", "8600"))
        # base_url advertised in Agent Card — overridable via env to expose Tailscale FQDN.
        base_url = os.environ.get("HEMI_A2A_BASE_URL", f"http://localhost:{port}")
        app = FastAPI(title=f"Hémicycle A2A — {self.agent_id}",
                       description="Sub-agent Hémicycle exposé en A2A v1.0")
        mount_a2a(app, self.manifest, self.handler, base_url=base_url)
        cfg = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning",
                              access_log=False)
        server = uvicorn.Server(cfg)
        log.info("A2A server %s écoute sur :%d (base_url=%s)", self.agent_id, port, base_url)
        await server.serve()

    async def _liveness_loop(self) -> None:
        """Écrit la liveness du pod dans KV `pods` (TTL) — alimente la vue Fleet du dashboard."""
        key = f"{self.agent_id}.{self.worker_id}"
        while True:
            info = {
                "agent_id": self.agent_id, "worker_id": self.worker_id, "status": self.status,
                "current_corr": self.current_corr, "ts": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await self.pods_kv.put(key, json.dumps(info).encode())
            except Exception:
                log.exception("liveness: échec d'écriture KV")
            await asyncio.sleep(settings.pod_heartbeat_ttl_s / 3)

    async def _consume_loop(self) -> None:
        """Consomme `agent.{id}.task` en queue group (durable partagé entre pods = load-balancing)."""
        sub = await nats_io.durable_pull(
            self.js, "TASKS", f"{self.agent_id}-workers",
            subjects.agent_task(self.agent_id), ack_wait=settings.worker_ack_wait_s,
        )
        while True:
            try:
                msgs = await sub.fetch(1, timeout=2)
            except NatsTimeoutError:
                continue
            for msg in msgs:
                await self._handle_one(msg)

    async def _handle_one(self, msg) -> None:
        """Exécute une tâche : heartbeat de fond, handler de discipline, publication du résultat."""
        # Seuil DLQ applicatif distinct du cap serveur max_deliver (strictement inférieur) :
        # la branche DLQ reste atteignable avant que le serveur ne cesse de redélivrer.
        if msg.metadata.num_delivered > settings.dlq_threshold:
            await self._to_dlq(msg, f"num_delivered={msg.metadata.num_delivered}")
            await msg.term()
            return
        try:
            td = TaskDispatch.from_bytes(msg.data)
        except Exception as e:
            await self._to_dlq(msg, f"dispatch illisible: {e}")
            await msg.term()
            return

        self.status, self.current_corr = "busy", td.corr_id
        self._cur_pct, self._cur_note = 0.0, "démarrage"
        heartbeat = asyncio.create_task(self._heartbeat(msg, td))
        cancelled = False
        try:
            async def progress(pct: float, note: str) -> None:
                """Callback du handler : publie un Progress et étend l'ack JetStream."""
                self._cur_pct, self._cur_note = pct, note
                await self._publish_progress(td)
                await msg.in_progress()

            async def trace(kind: str, data: dict) -> None:
                """Callback du handler : publie une trace riche (thinking/tool_call/tool_result).

                Passe par le même subject que `progress` (un seul tuyau côté consumer),
                mais avec `kind != "progress"` pour discriminer côté orchestrateur.
                """
                await self._publish_trace(td, kind, data)
                await msg.in_progress()

            # Chaînage par référence : on charge les payloads amont COMPLETS depuis
            # l'Object Store et on les expose au handler (structuré, plus aucun résumé tronqué).
            td.params["upstream_artifacts"] = await self._resolve_upstream(td)
            # Le handler tourne dans une task dédiée, suivie par corr_id : un cancel
            # publié par l'orchestrateur l'interrompt (`task.cancel()` → CancelledError
            # dans le handler) sans toucher les autres tâches du pod.
            handler_task = asyncio.create_task(self.handler(td, progress, trace))
            self._inflight[td.corr_id] = handler_task
            output = await handler_task
            # Frontière STRICTE : un output mal formé lève ici (→ Result failed) au lieu
            # de produire un artefact qui perdrait silencieusement des champs en aval.
            payload, preview = validate_output(self.agent_id, output)
            env = ArtifactEnvelope(artifact_id=td.corr_id, agent_type=self.agent_id,
                                   schema_name=self.agent_id, payload=payload, preview=preview)
            await put_artifact(self.js, env)          # le producteur stocke son artefact
            result = Result(corr_id=td.corr_id, conv_id=td.conv_id, agent_id=self.agent_id,
                            worker_id=self.worker_id, status="done", output=payload,
                            artifact_id=td.corr_id)
        except asyncio.CancelledError:
            # Annulation reçue (réorientation/annulation analyste) : la tâche est déjà
            # tombstonée en base → on ne publie PAS de Result (il serait ignoré), mais on
            # ACK pour ne pas redélivrer indéfiniment le dispatch.
            cancelled = True
            log.info("tâche %s annulée → ack sans publier de Result", td.corr_id)
        except Exception as e:
            log.exception("tâche %s en échec", td.corr_id)
            result = Result(corr_id=td.corr_id, conv_id=td.conv_id, agent_id=self.agent_id,
                            worker_id=self.worker_id, status="failed", output={}, error=str(e))
        finally:
            heartbeat.cancel()
            self._inflight.pop(td.corr_id, None)
            self.status, self.current_corr = "idle", None

        if cancelled:
            await msg.ack()
            return
        await self.js.publish(subjects.agent_result(self.agent_id, td.corr_id), result.to_bytes())
        await msg.ack()

    async def _on_cancel(self, msg) -> None:
        """Callback core NATS : annule le handler en vol dont le corr_id est dans le subject.

        Subject reçu : `agent.{id}.cancel.{corr_id}` (payload vide). corr_id inconnu
        (tâche déjà terminée / servie par un autre pod) → ignoré silencieusement.
        """
        corr_id = msg.subject.rsplit(".", 1)[-1]
        task = self._inflight.get(corr_id)
        if task is not None and not task.done():
            log.info("cancel reçu pour %s → annulation du handler", corr_id)
            task.cancel()

    async def _resolve_upstream(self, td: TaskDispatch) -> list[dict]:
        """Charge les payloads amont référencés depuis l'Object Store (chaînage sans perte).

        Si un artefact est absent/expiré, on retombe sur le `preview` texte de la
        référence — l'étape reste exécutable, dégradée plutôt que cassée.
        """
        out: list[dict] = []
        for ref in td.input_refs:
            env = await get_artifact(self.js, ref.artifact_id)
            out.append({"agent_type": ref.agent_type,
                        "payload": env.payload if env else {},
                        "preview": env.preview if env else ref.preview})
        return out

    async def _heartbeat(self, msg, td: TaskDispatch) -> None:
        """Publie un Progress régulier (lease + in_progress) même quand le handler n'avance pas."""
        try:
            while True:
                await asyncio.sleep(settings.lease_ttl_s / 3)
                await self._publish_progress(td)
                try:
                    await msg.in_progress()
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    async def _publish_progress(self, td: TaskDispatch) -> None:
        """Publie l'avancement courant sur `agent.{id}.progress.{corr_id}` (stream durable)."""
        prog = Progress(corr_id=td.corr_id, conv_id=td.conv_id, agent_id=self.agent_id,
                        worker_id=self.worker_id, pct=self._cur_pct, note=self._cur_note)
        await self.js.publish(subjects.agent_progress(self.agent_id, td.corr_id), prog.to_bytes())

    async def _publish_trace(self, td: TaskDispatch, kind: str, data: dict) -> None:
        """Publie une trace d'exécution (thinking/tool_call/tool_result) sur le même subject."""
        prog = Progress(
            corr_id=td.corr_id, conv_id=td.conv_id, agent_id=self.agent_id,
            worker_id=self.worker_id, pct=self._cur_pct, note=self._cur_note,
            kind=kind, trace_data=data,
        )
        await self.js.publish(subjects.agent_progress(self.agent_id, td.corr_id), prog.to_bytes())

    async def _to_dlq(self, msg, reason: str) -> None:
        """Envoie un dispatch empoisonné en dead-letter (`dlq.agent.{id}`)."""
        await self.js.publish(subjects.dlq(self.agent_id), msg.data,
                              headers={"X-DLQ-Reason": reason})
        log.warning("DLQ ← agent.%s : %s", self.agent_id, reason)
