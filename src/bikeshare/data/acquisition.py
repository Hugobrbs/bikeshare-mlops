import hashlib
import io
import json
import logging
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bikeshare.pipeline_config import DatasetConfig

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class AcquisitionError(RuntimeError):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class IntegrityError(RuntimeError):
    """Le fichier reçu n'a pas l'empreinte attendue."""


@dataclass(frozen=True)
class SourceSnapshot:
    path: Path
    sha256: str
    origin: str
    fetched_at: str


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_source(
    dataset: DatasetConfig,
    source_dir: Path,
    *,
    transport: httpx.BaseTransport | None = None,
) -> SourceSnapshot:
    target = source_dir / dataset.member
    if target.is_file() and sha256_file(target) == dataset.member_sha256:
        logger.info("source déjà présente : %s", target)
        return _cached_snapshot(source_dir, target, dataset.member_sha256)

    failures = []
    transient = False
    corrupted = False
    for url in dataset.urls:
        try:
            archive = _download(url, dataset.timeout_seconds, transport)
            content = _extract(archive, dataset.member)
        except AcquisitionError as e:
            logger.warning("source indisponible %s : %s", url, e)
            failures.append(f"{url}: {e}")
            transient = transient or e.transient
            continue
        except IntegrityError as e:
            logger.warning("archive inexploitable %s : %s", url, e)
            failures.append(f"{url}: {e}")
            corrupted = True
            continue

        digest = sha256_bytes(content)
        if digest != dataset.member_sha256:
            logger.warning("empreinte inattendue pour %s : %s", url, digest)
            failures.append(f"{url}: empreinte {digest} inattendue")
            corrupted = True
            continue

        snapshot = SourceSnapshot(
            path=target,
            sha256=digest,
            origin=url,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        write_atomic(target, content)
        manifest = {**asdict(snapshot), "path": target.name}
        write_atomic(source_dir / MANIFEST_NAME, json.dumps(manifest, indent=2).encode())
        logger.info("source téléchargée depuis %s (%d octets)", url, len(content))
        return snapshot

    details = " ; ".join(failures)
    if corrupted and not transient:
        raise IntegrityError(f"aucune source ne fournit le bon fichier. {details}")
    raise AcquisitionError(f"aucune source disponible. {details}", transient=transient)


def write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(content)
    tmp.replace(path)


def _download(url: str, timeout: float, transport: httpx.BaseTransport | None) -> bytes:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, transport=transport) as client:
            response = client.get(url)
    except httpx.TransportError as e:
        raise AcquisitionError(f"erreur réseau ({e!r})", transient=True) from e
    if response.status_code != 200:
        raise AcquisitionError(
            f"HTTP {response.status_code}", transient=response.status_code in RETRYABLE_STATUS
        )
    return response.content


def _extract(archive: bytes, member: str) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            return z.read(member)
    except zipfile.BadZipFile as e:
        raise IntegrityError("le contenu reçu n'est pas un zip") from e
    except KeyError as e:
        raise IntegrityError(f"{member} absent de l'archive") from e


def _cached_snapshot(source_dir: Path, target: Path, digest: str) -> SourceSnapshot:
    origin, fetched_at = "copie locale", "inconnue"
    manifest = source_dir / MANIFEST_NAME
    if manifest.is_file():
        recorded = json.loads(manifest.read_text())
        if recorded.get("sha256") == digest:
            origin = recorded.get("origin", origin)
            fetched_at = recorded.get("fetched_at", fetched_at)
    return SourceSnapshot(path=target, sha256=digest, origin=origin, fetched_at=fetched_at)
