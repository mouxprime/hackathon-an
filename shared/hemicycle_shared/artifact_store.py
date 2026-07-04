"""Object Store JetStream pour les artefacts d'agents (chaînage sans perte par référence).

Un agent produit un payload structuré → on le persiste UNE fois sous son `corr_id`,
et seule une référence voyage vers l'étape aval. Le worker aval recharge le payload
COMPLET par référence. Respecte l'invariant n°7 (les workers ne touchent jamais
Postgres) : l'Object Store fait partie du bus NATS, déjà accessible aux workers.
"""

import logging

from nats.js import JetStreamContext

from .artifacts import ArtifactEnvelope

log = logging.getLogger("hemicycle.artifacts")

ARTIFACTS_BUCKET = "artifacts"


async def ensure_artifact_store(js: JetStreamContext, ttl_s: int = 48 * 3600) -> None:
    """Crée (idempotent) le bucket Object Store des artefacts. Appelé dans ensure_topology.

    TTL 48 h (au lieu de 6 h) : couvre un cycle de travail humain complet (investigation
    reprise le lendemain matin). En dessous de 24 h, une étape `awaiting_input` de nuit
    expire les `input_refs` avant la reprise et le chaînage retombe silencieusement sur
    le preview 4 k (régression item 2.2). 48 h est la valeur minimale prudente.

    Le nom passe par l'argument `bucket=` (pas via `config=` : nats-py écraserait
    alors le nom par None). En cas d'échec de création, on tente le bind : si le
    bucket existe déjà tout va bien, sinon l'erreur réelle remonte (pas de masquage).
    """
    try:
        await js.create_object_store(bucket=ARTIFACTS_BUCKET, ttl=ttl_s)
    except Exception:
        await js.object_store(ARTIFACTS_BUCKET)         # doit exister, sinon l'erreur remonte


async def put_artifact(js: JetStreamContext, env: ArtifactEnvelope) -> str:
    """Persiste une enveloppe (clé = artifact_id). Idempotent : re-put = même clé, même contenu."""
    os_ = await js.object_store(ARTIFACTS_BUCKET)
    await os_.put(env.artifact_id, env.to_bytes())
    return env.artifact_id


async def get_artifact(js: JetStreamContext, artifact_id: str) -> ArtifactEnvelope | None:
    """Charge une enveloppe par id. None si absente/expirée → l'appelant retombe sur le preview."""
    os_ = await js.object_store(ARTIFACTS_BUCKET)
    try:
        res = await os_.get(artifact_id)
    except Exception:
        return None
    return ArtifactEnvelope.from_bytes(res.data)
