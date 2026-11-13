import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pandas as pd

from bikeshare.data.acquisition import sha256_file

logger = logging.getLogger(__name__)


class Corruption(StrEnum):
    MISSING_HOURS = "missing-hours"
    NEGATIVE_RENTALS = "negative-rentals"
    DROPPED_COLUMN = "dropped-column"
    FROZEN_SENSOR = "frozen-sensor"
    DUPLICATED_ROWS = "duplicated-rows"


@dataclass(frozen=True)
class PublishedBatch:
    batch_id: str
    path: Path
    rows: int
    sha256: str
    corruption: Corruption | None = None


def month_of(dates: pd.Series) -> pd.Series:
    return dates.astype(str).str.slice(0, 7)


def list_batch_ids(source: pd.DataFrame) -> list[str]:
    return sorted(month_of(source["dteday"]).unique())


def next_batch_ids(available: list[str], delivered: set[str], count: int) -> list[str]:
    return [b for b in available if b not in delivered][:count]


def corrupt(batch: pd.DataFrame, corruption: Corruption, seed: int = 0) -> pd.DataFrame:
    damaged = batch.copy()
    match corruption:
        case Corruption.MISSING_HOURS:
            return damaged.iloc[: len(damaged) // 2]
        case Corruption.NEGATIVE_RENTALS:
            rows = damaged.sample(frac=0.05, random_state=seed).index
            damaged.loc[rows, "cnt"] = -damaged.loc[rows, "cnt"]
        case Corruption.DROPPED_COLUMN:
            return damaged.drop(columns=["weathersit"])
        case Corruption.FROZEN_SENSOR:
            damaged["temp"] = damaged["temp"].iloc[0]
        case Corruption.DUPLICATED_ROWS:
            return pd.concat([damaged, damaged.iloc[:48]], ignore_index=True)
    return damaged


def publish_batches(
    source_file: Path,
    incoming_dir: Path,
    *,
    delivered: set[str],
    count: int = 1,
    corruption: Corruption | None = None,
) -> list[PublishedBatch]:
    source = pd.read_csv(source_file)
    months = month_of(source["dteday"])
    incoming_dir.mkdir(parents=True, exist_ok=True)

    published = []
    for batch_id in next_batch_ids(list_batch_ids(source), delivered, count):
        batch = source[months == batch_id]
        if corruption:
            batch = corrupt(batch, corruption)
        path = incoming_dir / f"{batch_id}.csv"
        tmp = path.with_name(path.name + ".part")
        batch.to_csv(tmp, index=False)
        tmp.replace(path)
        published.append(PublishedBatch(batch_id, path, len(batch), sha256_file(path), corruption))
        logger.info("lot %s déposé (%d lignes, défaut=%s)", batch_id, len(batch), corruption)
    return published
