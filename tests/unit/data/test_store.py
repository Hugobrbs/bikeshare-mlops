import json
from pathlib import Path

import pandas as pd
import pytest

from bikeshare.data.replay import publish_batches
from bikeshare.data.store import BatchConflictError, BatchStore


@pytest.fixture
def store(tmp_path: Path) -> BatchStore:
    return BatchStore(tmp_path)


def deliver(store: BatchStore, source_file: Path, count: int = 1) -> list[Path]:
    publish_batches(source_file, store.incoming_dir, delivered=store.delivered_ids(), count=count)
    return store.pending()


def test_empty_store(store: BatchStore) -> None:
    assert store.pending() == []
    assert store.accepted() == []
    with pytest.raises(FileNotFoundError):
        store.load()


def test_accept_moves_batch_to_curated(store: BatchStore, source_file: Path) -> None:
    [incoming] = deliver(store, source_file)

    record = store.accept(incoming)

    assert record.batch_id == "2011-01"
    assert record.rows == 31 * 24
    assert record.first_timestamp == "2011-01-01T00:00:00"
    assert not incoming.exists()
    assert (store.curated_dir / "2011-01.csv").is_file()
    assert [r.batch_id for r in store.accepted()] == ["2011-01"]


def test_accepting_same_batch_twice_is_a_noop(store: BatchStore, source_file: Path) -> None:
    [incoming] = deliver(store, source_file)
    content = incoming.read_bytes()
    first = store.accept(incoming)
    incoming.write_bytes(content)

    second = store.accept(incoming)

    assert second == first
    assert len(store.accepted()) == 1


def test_accepted_batch_cannot_be_overwritten(store: BatchStore, source_file: Path) -> None:
    [incoming] = deliver(store, source_file)
    content = incoming.read_text()
    store.accept(incoming)
    incoming.write_text(content.replace("2011-01-01", "2011-01-02", 1))

    with pytest.raises(BatchConflictError):
        store.accept(incoming)


def test_quarantine_keeps_file_and_report(store: BatchStore, source_file: Path) -> None:
    [incoming] = deliver(store, source_file)

    target = store.quarantine(incoming, {"success": False, "failures": ["volume"]})

    assert not incoming.exists()
    assert target.parent == store.quarantine_dir
    assert json.loads(target.with_suffix(".report.json").read_text())["failures"] == ["volume"]
    assert store.accepted() == []
    # un lot rejeté n'est pas "livré" : il sera redéposé
    assert store.delivered_ids() == set()


def test_load_returns_most_recent_batches(store: BatchStore, source_file: Path) -> None:
    for incoming in deliver(store, source_file, count=3):
        store.accept(incoming)

    everything = store.load()
    last_two = store.load(last=2)

    assert len(everything) == (31 + 28 + 31) * 24
    assert last_two["timestamp"].min() == pd.Timestamp("2011-02-01")
    assert pd.api.types.is_datetime64_any_dtype(everything["timestamp"])


def test_fingerprint_changes_with_data(store: BatchStore, source_file: Path) -> None:
    first, second = deliver(store, source_file, count=2)
    store.accept(first)
    before = store.fingerprint()
    store.accept(second)

    assert store.fingerprint() != before
    assert store.fingerprint(last=1) != store.fingerprint()
    assert store.fingerprint() == store.fingerprint()
