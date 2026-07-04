"""Client du bucket de transit des documents inter-agents Hémicycle (front s3-controller).

PROBLÈME RÉSOLU
---------------
Historiquement, une pièce jointe voyageait *par valeur* : le gateway extrayait son
texte, l'orchestrateur l'inlinait (tronqué à 8000 chars) dans le prompt A2A, et la
matière source ne survivait pas au handoff entre agents. Désormais les fichiers
voyagent *par référence* : les octets originaux sont poussés dans le bucket MinIO
``hemicycle`` (via le s3-controller in-cluster, sans credentials côté client) et c'est
l'URI courte ``s3://hemicycle/<object_key>`` qui circule entre agents. Chaque agent
récupère le document intégral via ``pull_doc``.

CONTRAT s3-controller (validé live, v0.4.2)
-------------------------------------------
- ``POST /uploadFile/{bucket}`` multipart : champ ``preserveName=true`` + partie
  ``file`` dont le *filename* devient la clé d'objet **verbatim**. Passer ``objectKey``
  en champ de formulaire renvoie HTTP 500 — on contrôle la clé par le filename seul.
- ``GET /downloadFrom/{bucket}?objectKey=...`` : octets bruts.
- ``GET /listObjectsOf/{bucket}`` / ``DELETE /deleteFrom/{bucket}?objectKey=...``.
- Les clés d'objet doivent être **sans slash** (le champ ``directory`` du controller
  servirait pour des sous-chemins). D'où le format plat ``conv__uuid__filename``.

CONFIGURATION (env)
-------------------
- ``S3_CONTROLLER_URL`` — base, défaut ``http://s3-controller.default.svc.cluster.local:8080``.
  Vide/non défini → client désactivé (les push sont sautés ; pratique en dev/tests).
- ``HEMI_DOC_BUCKET`` — bucket cible, défaut ``hemicycle``.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_BUCKET = "hemicycle"
DEFAULT_CONTROLLER_URL = "http://s3-controller.default.svc.cluster.local:8080"
URL_ENV = "S3_CONTROLLER_URL"
BUCKET_ENV = "HEMI_DOC_BUCKET"

_URI_RE = re.compile(r"s3://([A-Za-z0-9._-]+)/([^\s]+)")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class S3DocError(RuntimeError):
    """Levée quand le s3-controller renvoie un statut non-2xx ou est injoignable."""


# ----------------------------------------------------------------------------
# Helpers d'URI / clé d'objet (purs, testables sans réseau)
# ----------------------------------------------------------------------------

def safe_filename(filename: str) -> str:
    """Réduit un nom de fichier à un composant de clé plat et sûr (≤ 80 chars, sans slash)."""
    base = os.path.basename(filename or "document")
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip("_")
    return (cleaned or "document")[:80]


def make_object_key(filename: str, conv_id: str | None = None) -> str:
    """Construit une clé d'objet plate, non-devinable et sans slash.

    Format : ``{conv|'shared'}__{uuid12}__{safe_filename}``. L'UUID rend la clé
    non-énumérable (le bucket n'a aucune auth côté client).
    """
    conv = safe_filename(conv_id) if conv_id else "shared"
    return f"{conv}__{uuid.uuid4().hex[:12]}__{safe_filename(filename)}"


def make_uri(object_key: str, bucket: str = DEFAULT_BUCKET) -> str:
    """Forme canonique ``s3://<bucket>/<object_key>``."""
    return f"s3://{bucket}/{object_key}"


def parse_uri(uri: str, default_bucket: str = DEFAULT_BUCKET) -> tuple[str, str]:
    """Résout ``s3://bucket/key`` OU une clé nue en ``(bucket, object_key)``.

    Lève ``ValueError`` si l'entrée est vide ou manifestement invalide.
    """
    if not uri or not uri.strip():
        raise ValueError("URI de document vide")
    raw = uri.strip()
    m = _URI_RE.fullmatch(raw)
    if m:
        return m.group(1), m.group(2)
    if "://" in raw:
        raise ValueError(f"URI de document non supportée : {raw!r}")
    # Clé nue (pas de schéma) → bucket par défaut.
    return default_bucket, raw


def find_doc_uris(text: str, bucket: str = DEFAULT_BUCKET) -> list[str]:
    """Extrait toutes les URIs ``s3://<bucket>/...`` d'un texte, dans l'ordre, dédupliquées."""
    if not text:
        return []
    out: list[str] = []
    for m in _URI_RE.finditer(text):
        if m.group(1) == bucket:
            uri = f"s3://{m.group(1)}/{m.group(2)}"
            if uri not in out:
                out.append(uri)
    return out


class S3DocClient:
    """Client stdlib (`urllib`) du bucket de transit — aucune dépendance tierce."""

    def __init__(self, base_url: str | None = None, bucket: str | None = None,
                 timeout: float = 60.0):
        raw_url = base_url if base_url is not None else os.environ.get(URL_ENV, DEFAULT_CONTROLLER_URL)
        self.base_url = (raw_url or "").strip().rstrip("/")
        self.bucket = bucket or os.environ.get(BUCKET_ENV) or DEFAULT_BUCKET
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        """False quand aucune URL de controller n'est configurée (push à sauter)."""
        return bool(self.base_url)

    # -- transport bas niveau ------------------------------------------------

    def _request(self, path: str, *, method: str = "GET", data: bytes | None = None,
                 headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise S3DocError(f"{method} {path} -> HTTP {exc.code}: {body[:300]}") from exc
        except urllib.error.URLError as exc:
            raise S3DocError(f"{method} {path} -> {exc.reason}") from exc

    # -- API publique --------------------------------------------------------

    def list_buckets(self) -> list[str]:
        import json
        _, body = self._request("/listBuckets")
        try:
            return [b.get("bucketName") for b in json.loads(body.decode("utf-8", "replace"))
                    if isinstance(b, dict)]
        except (ValueError, TypeError):
            return []

    def ensure_bucket(self) -> None:
        """Crée le bucket cible s'il n'existe pas (idempotent). No-op si désactivé."""
        if not self.enabled:
            return
        try:
            if self.bucket in self.list_buckets():
                return
            self._request(f"/createBucket/{self.bucket}", method="POST")
            log.info("s3-docs: bucket %r créé", self.bucket)
        except S3DocError as exc:
            log.warning("s3-docs: ensure_bucket(%r) a échoué : %s", self.bucket, exc)

    def push_bytes(self, content: bytes, object_key: str, *,
                   content_type: str = "application/octet-stream") -> str:
        """Pousse des octets sous ``object_key`` (clé sans slash) ; renvoie l'URI canonique.

        ``object_key`` est envoyé comme *filename* multipart et devient la clé verbatim.
        """
        boundary = "----hemicycledoc" + uuid.uuid4().hex
        crlf = b"\r\n"
        body = b"".join([
            f"--{boundary}".encode(), crlf,
            b'Content-Disposition: form-data; name="preserveName"', crlf, crlf,
            b"true", crlf,
            f"--{boundary}".encode(), crlf,
            (f'Content-Disposition: form-data; name="file"; '
             f'filename="{object_key}"').encode(), crlf,
            f"Content-Type: {content_type}".encode(), crlf, crlf,
            content, crlf,
            f"--{boundary}--".encode(), crlf,
        ])
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        self._request(f"/uploadFile/{self.bucket}", method="POST", data=body, headers=headers)
        return make_uri(object_key, self.bucket)

    def pull_bytes(self, uri: str, *, max_bytes: int = 10_000_000) -> tuple[bytes, bool]:
        """Récupère les octets d'un document par URI (ou clé nue).

        Renvoie ``(content, truncated)`` ; ``content`` est tronqué à ``max_bytes``.
        """
        bucket, key = parse_uri(uri, self.bucket)
        if bucket != self.bucket:
            # Le controller adresse n'importe quel bucket ; on respecte celui de l'URI.
            path = f"/downloadFrom/{bucket}?objectKey={urllib.parse.quote(key, safe='')}"
        else:
            path = f"/downloadFrom/{self.bucket}?objectKey={urllib.parse.quote(key, safe='')}"
        _, raw = self._request(path)
        truncated = len(raw) > max_bytes
        return (raw[:max_bytes] if truncated else raw), truncated

    def delete(self, uri: str) -> None:
        bucket, key = parse_uri(uri, self.bucket)
        self._request(
            f"/deleteFrom/{bucket}?objectKey={urllib.parse.quote(key, safe='')}",
            method="DELETE")


# ----------------------------------------------------------------------------
# API haut niveau — c'est ce que les services/tools appellent
# ----------------------------------------------------------------------------

def _looks_textual(raw: bytes) -> bool:
    """Heuristique : décodable UTF-8 et sans octet nul → traité comme texte."""
    if b"\x00" in raw:
        return False
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def push_doc(filename: str, content: str | bytes, *, encoding: str = "text",
             content_type: str = "application/octet-stream",
             conv_id: str | None = None, client: S3DocClient | None = None) -> dict:
    """Publie un document dans le bucket de transit ; renvoie ``{uri, object_key, size_bytes}``.

    ``content`` : ``bytes`` directs, ou ``str`` interprété selon ``encoding``
    (``"text"`` = UTF-8, ``"base64"`` = décodé). Lève ``S3DocError`` si le client est
    désactivé ou si l'upload échoue.
    """
    cli = client or S3DocClient()
    if not cli.enabled:
        raise S3DocError("bucket de documents non configuré (S3_CONTROLLER_URL vide)")
    if isinstance(content, bytes):
        raw = content
    elif encoding == "base64":
        raw = base64.b64decode(content)
    else:
        raw = content.encode("utf-8")
    object_key = make_object_key(filename, conv_id)
    uri = cli.push_bytes(raw, object_key, content_type=content_type)
    return {"uri": uri, "object_key": object_key, "size_bytes": len(raw)}


def pull_doc(uri: str, *, max_bytes: int = 10_000_000,
             client: S3DocClient | None = None) -> dict:
    """Récupère un document par URI ; renvoie le contenu décodé + métadonnées.

    Renvoie ``{filename, object_key, content_type, size_bytes, encoding, content, truncated}``.
    ``encoding`` vaut ``"text"`` si les octets sont décodables UTF-8 (``content`` = texte),
    sinon ``"base64"`` (``content`` = base64). À consommer **programmatiquement** : ne pas
    injecter un ``content`` base64 volumineux dans un prompt LLM.
    """
    cli = client or S3DocClient()
    bucket, object_key = parse_uri(uri, cli.bucket)
    raw, truncated = cli.pull_bytes(uri, max_bytes=max_bytes)
    # Clé plate = "{conv}__{uuid12}__{filename}" : le filename est tout ce qui suit le 2e "__"
    # (il peut lui-même contenir des "__" issus de l'assainissement).
    parts = object_key.split("__", 2)
    filename = parts[2] if len(parts) == 3 else object_key
    if _looks_textual(raw):
        return {"filename": filename, "object_key": object_key,
                "content_type": "text/plain", "size_bytes": len(raw),
                "encoding": "text", "content": raw.decode("utf-8"), "truncated": truncated}
    return {"filename": filename, "object_key": object_key,
            "content_type": "application/octet-stream", "size_bytes": len(raw),
            "encoding": "base64", "content": base64.b64encode(raw).decode("ascii"),
            "truncated": truncated}
