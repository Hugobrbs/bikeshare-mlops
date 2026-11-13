import json
from pathlib import Path

import pytest

from bikeshare.data.ingestion import ingest_pending
from bikeshare.data.replay import Corruption, publish_batches
from bikeshare.data.store import BatchStore
from bikeshare.data.validation import DataValidationError
from bikeshare.pipeline_config import ValidationConfig

RULES = ValidationConfig()


@pytest.fixture
def store(tmp_path: Path) -> BatchStore:
    return BatchStore(tmp_path)


def deliver(
    store: BatchStore, source_file: Path, count: int = 1, corruption: Corruption | None = None
) -> None:
    publish_batches(
        source_file,
        store.incoming_dir,
        delivered=store.delivered_ids(),
        count=count,
        corruption=corruption,
    )


def test_valid_batches_are_accepted_in_order(store: BatchStore, source_file: Path) -> None:
    deliver(store, source_file, count=3)

    accepted = ingest_pending(store, RULES)

    assert [r.batch_id for r in accepted] == ["2011-01", "2011-02", "2011-03"]
    assert store.pending() == []
    assert json.loads((store.reports_dir / "2011-02.json").read_text())["success"] is True


def test_nothing_pending(store: BatchStore) -> None:
    assert ingest_pending(store, RULES) == []


def test_invalid_batch_is_quarantined_and_blocks(store: BatchStore, source_file: Path) -> None:
    deliver(store, source_file, corruption=Corruption.NEGATIVE_RENTALS)

    with pytest.raises(DataValidationError) as error:
        ingest_pending(store, RULES)

    assert error.value.report.batch_id == "2011-01"
    assert store.accepted() == []
    assert store.pending() == []
    [quarantined] = store.quarantine_dir.glob("*.csv")
    assert quarantined.name.startswith("2011-01-")
    assert json.loads((store.reports_dir / "2011-01.json").read_text())["success"] is False


def test_batches_after_an_invalid_one_stay_pending(store: BatchStore, source_file: Path) -> None:
    deliver(store, source_file)
    deliver(store, source_file, corruption=Corruption.MISSING_HOURS)
    deliver(store, source_file)

    with pytest.raises(DataValidationError):
        ingest_pending(store, RULES)

    assert [r.batch_id for r in store.accepted()] == ["2011-01"]
    assert [p.stem for p in store.pending()] == ["2011-03"]


def test_recovery_after_a_clean_redelivery(store: BatchStore, source_file: Path) -> None:
    deliver(store, source_file, corruption=Corruption.FROZEN_SENSOR)
    with pytest.raises(DataValidationError):
        ingest_pending(store, RULES)

    deliver(store, source_file)
    accepted = ingest_pending(store, RULES)

    assert [r.batch_id for r in accepted] == ["2011-01"]


def test_unreadable_file_is_rejected(store: BatchStore) -> None:
    store.incoming_dir.mkdir(parents=True)
    (store.incoming_dir / "2011-01.csv").write_bytes(b"")

    with pytest.raises(DataValidationError, match="file_is_readable"):
        ingest_pending(store, RULES)


def test_skipped_month_is_rejected(store: BatchStore, source_file: Path) -> None:
    deliver(store, source_file, count=3)
    (store.incoming_dir / "2011-02.csv").unlink()

    with pytest.raises(DataValidationError, match="batch_follows_previous_one"):
        ingest_pending(store, RULES)

    assert [r.batch_id for r in store.accepted()] == ["2011-01"]


def test_every_validated_batch_is_reported(store: BatchStore, source_file: Path) -> None:
    deliver(store, source_file)
    deliver(store, source_file, corruption=Corruption.MISSING_HOURS)
    reports: list[dict] = []

    with pytest.raises(DataValidationError):
        ingest_pending(store, RULES, on_report=reports.append)

    assert [(r["batch_id"], r["success"]) for r in reports] == [
        ("2011-01", True),
        ("2011-02", False),
    ]
