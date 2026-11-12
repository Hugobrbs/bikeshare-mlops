import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from bikeshare.data.acquisition import sha256_file, write_atomic
from bikeshare.data.schema import TIMESTAMP
from bikeshare.data.standardize import to_domain


class BatchConflictError(RuntimeError):
    """Un lot déjà accepté est relivré avec un contenu différent."""


@dataclass(frozen=True)
class BatchRecord:
    batch_id: str
    rows: int
    source_sha256: str
    first_timestamp: str
    last_timestamp: str
    accepted_at: str


class BatchStore:
    """Dossiers de données : incoming (arrivée), curated (acceptés), quarantine (rejetés)."""

    def __init__(self, data_dir: Path) -> None:
        self.source_dir = data_dir / "source"
        self.incoming_dir = data_dir / "incoming"
        self.curated_dir = data_dir / "curated"
        self.quarantine_dir = data_dir / "quarantine"
        self.reports_dir = data_dir / "reports"
        self.manifest_path = self.curated_dir / "manifest.json"

    def pending(self) -> list[Path]:
        return sorted(self.incoming_dir.glob("*.csv"))

    def accepted(self) -> list[BatchRecord]:
        if not self.manifest_path.is_file():
            return []
        batches = json.loads(self.manifest_path.read_text())["batches"]
        return [BatchRecord(**b) for b in batches]

    def delivered_ids(self) -> set[str]:
        return {r.batch_id for r in self.accepted()} | {p.stem for p in self.pending()}

    def accept(self, incoming_file: Path) -> BatchRecord:
        batch_id = incoming_file.stem
        digest = sha256_file(incoming_file)
        records = {r.batch_id: r for r in self.accepted()}

        if batch_id in records:
            if records[batch_id].source_sha256 != digest:
                raise BatchConflictError(f"lot {batch_id} déjà accepté avec un autre contenu")
            # même lot rejoué : rien à refaire
            incoming_file.unlink()
            return records[batch_id]

        domain = to_domain(pd.read_csv(incoming_file))
        write_atomic(self.curated_dir / f"{batch_id}.csv", domain.to_csv(index=False).encode())
        record = BatchRecord(
            batch_id=batch_id,
            rows=len(domain),
            source_sha256=digest,
            first_timestamp=domain[TIMESTAMP].min().isoformat(),
            last_timestamp=domain[TIMESTAMP].max().isoformat(),
            accepted_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        records[batch_id] = record
        manifest = {"batches": [asdict(records[k]) for k in sorted(records)]}
        write_atomic(self.manifest_path, json.dumps(manifest, indent=2).encode())
        incoming_file.unlink()
        return record

    def quarantine(self, incoming_file: Path, report: dict) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        target = self.quarantine_dir / f"{incoming_file.stem}-{stamp}.csv"
        shutil.move(incoming_file, target)
        target.with_suffix(".report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str)
        )
        return target

    def save_report(self, report: dict) -> Path:
        path = self.reports_dir / f"{report['batch_id']}.json"
        write_atomic(path, json.dumps(report, indent=2, ensure_ascii=False, default=str).encode())
        return path

    def load(self, last: int | None = None) -> pd.DataFrame:
        """Concatène les lots acceptés (les `last` plus récents si précisé)."""
        records = self._select(last)
        if not records:
            raise FileNotFoundError("aucun lot accepté")
        frames = [
            pd.read_csv(self.curated_dir / f"{r.batch_id}.csv", parse_dates=[TIMESTAMP])
            for r in records
        ]
        return pd.concat(frames, ignore_index=True)

    def fingerprint(self, last: int | None = None) -> str:
        """Empreinte du jeu d'entraînement : change dès qu'un lot change."""
        content = "\n".join(f"{r.batch_id}:{r.source_sha256}" for r in self._select(last))
        return hashlib.sha256(content.encode()).hexdigest()

    def _select(self, last: int | None) -> list[BatchRecord]:
        records = self.accepted()
        return records[-last:] if last else records
