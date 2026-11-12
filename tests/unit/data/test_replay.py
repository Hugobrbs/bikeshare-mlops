from pathlib import Path

import pandas as pd
import pytest

from bikeshare.data.replay import (
    Corruption,
    corrupt,
    list_batch_ids,
    next_batch_ids,
    publish_batches,
)


def test_list_batch_ids(source_frame: pd.DataFrame) -> None:
    assert list_batch_ids(source_frame) == ["2011-01", "2011-02", "2011-03"]


def test_next_batch_ids_skips_delivered() -> None:
    available = ["2011-01", "2011-02", "2011-03", "2011-04"]

    assert next_batch_ids(available, {"2011-01"}, 2) == ["2011-02", "2011-03"]
    assert next_batch_ids(available, set(available), 1) == []


def test_publish_writes_one_file_per_month(source_file: Path, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"

    published = publish_batches(source_file, incoming, delivered=set(), count=2)

    assert [b.batch_id for b in published] == ["2011-01", "2011-02"]
    january = pd.read_csv(incoming / "2011-01.csv")
    assert len(january) == 31 * 24
    assert january["dteday"].str.startswith("2011-01").all()
    assert not list(incoming.glob("*.part"))


def test_publish_resumes_after_delivered(source_file: Path, tmp_path: Path) -> None:
    published = publish_batches(source_file, tmp_path / "incoming", delivered={"2011-01"})

    assert [b.batch_id for b in published] == ["2011-02"]


def test_publish_nothing_when_source_is_exhausted(source_file: Path, tmp_path: Path) -> None:
    delivered = {"2011-01", "2011-02", "2011-03"}

    assert publish_batches(source_file, tmp_path / "incoming", delivered=delivered) == []


def test_publish_can_inject_a_defect(source_file: Path, tmp_path: Path) -> None:
    [batch] = publish_batches(
        source_file, tmp_path / "incoming", delivered=set(), corruption=Corruption.MISSING_HOURS
    )

    assert batch.corruption is Corruption.MISSING_HOURS
    assert batch.rows == 31 * 24 // 2


@pytest.mark.parametrize("corruption", list(Corruption))
def test_corrupt_does_not_modify_the_original(
    source_frame: pd.DataFrame, corruption: Corruption
) -> None:
    before = source_frame.copy()

    damaged = corrupt(source_frame, corruption)

    pd.testing.assert_frame_equal(source_frame, before)
    assert not damaged.equals(before)


def test_corruptions_break_what_they_say(source_frame: pd.DataFrame) -> None:
    assert (corrupt(source_frame, Corruption.NEGATIVE_RENTALS)["cnt"] < 0).any()
    assert "weathersit" not in corrupt(source_frame, Corruption.DROPPED_COLUMN)
    assert corrupt(source_frame, Corruption.FROZEN_SENSOR)["temp"].nunique() == 1
    assert corrupt(source_frame, Corruption.DUPLICATED_ROWS).duplicated(["dteday", "hr"]).any()
