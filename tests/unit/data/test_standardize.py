import pandas as pd

from bikeshare.data.schema import DOMAIN_COLUMNS
from bikeshare.data.standardize import to_domain

ROW = {
    "instant": 1,
    "dteday": "2011-01-01",
    "season": 1,
    "yr": 0,
    "mnth": 1,
    "hr": 17,
    "holiday": 0,
    "weekday": 6,
    "workingday": 0,
    "weathersit": 2,
    "temp": 0.5,
    "atemp": 0.5,
    "hum": 0.81,
    "windspeed": 0.2,
    "casual": 3,
    "registered": 13,
    "cnt": 16,
}


def test_units_are_converted() -> None:
    row = to_domain(pd.DataFrame([ROW])).iloc[0]

    assert row["timestamp"] == pd.Timestamp("2011-01-01 17:00")
    assert row["temperature_c"] == 20.5
    assert row["feels_like_c"] == 25.0
    assert row["humidity_pct"] == 81.0
    assert row["windspeed_kmh"] == 13.4
    assert row["weather_code"] == 2
    assert row["rentals"] == 16
    assert not row["holiday"]


def test_leaky_columns_are_dropped(source_frame: pd.DataFrame) -> None:
    domain = to_domain(source_frame)

    assert list(domain.columns) == list(DOMAIN_COLUMNS)
    assert "casual" not in domain
    assert "registered" not in domain


def test_rows_are_sorted_by_time(source_frame: pd.DataFrame) -> None:
    shuffled = source_frame.sample(frac=1, random_state=1)

    domain = to_domain(shuffled)

    assert domain["timestamp"].is_monotonic_increasing
    assert len(domain) == len(source_frame)
