from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bikeshare.data.schema import SOURCE_COLUMNS


def make_source(months: list[str], seed: int = 0) -> pd.DataFrame:
    """Faux hour.csv : toutes les heures des mois demandés, valeurs plausibles."""
    rng = np.random.default_rng(seed)
    frames = []
    for month in months:
        hours = pd.date_range(f"{month}-01", periods=pd.Period(month).days_in_month * 24, freq="h")
        n = len(hours)
        casual = rng.integers(0, 40, n)
        registered = rng.integers(1, 200, n)
        frames.append(
            pd.DataFrame(
                {
                    "dteday": hours.strftime("%Y-%m-%d"),
                    "season": 1,
                    "yr": hours.year - 2011,
                    "mnth": hours.month,
                    "hr": hours.hour,
                    "holiday": 0,
                    "weekday": (hours.dayofweek + 1) % 7,
                    "workingday": (hours.dayofweek < 5).astype(int),
                    "weathersit": rng.choice([1, 2, 3], n, p=[0.7, 0.25, 0.05]),
                    "temp": rng.uniform(0.1, 0.9, n).round(2),
                    "atemp": rng.uniform(0.1, 0.9, n).round(4),
                    "hum": rng.uniform(0.2, 1.0, n).round(2),
                    "windspeed": rng.uniform(0.0, 0.5, n).round(4),
                    "casual": casual,
                    "registered": registered,
                    "cnt": casual + registered,
                }
            )
        )
    source = pd.concat(frames, ignore_index=True)
    source.insert(0, "instant", range(1, len(source) + 1))
    return source[list(SOURCE_COLUMNS)]


@pytest.fixture
def source_frame() -> pd.DataFrame:
    return make_source(["2011-01", "2011-02", "2011-03"])


@pytest.fixture
def source_file(tmp_path: Path, source_frame: pd.DataFrame) -> Path:
    path = tmp_path / "source" / "hour.csv"
    path.parent.mkdir()
    source_frame.to_csv(path, index=False)
    return path
