import numpy as np
import pandas as pd
import pytest

from bikeshare.data.schema import DOMAIN_COLUMNS


def make_domain_frame(days: int = 60, start: str = "2011-03-01", seed: int = 0) -> pd.DataFrame:
    """Observations horaires avec un vrai signal : pics aux heures de pointe, moins le week-end."""
    rng = np.random.default_rng(seed)
    timestamp = pd.date_range(start, periods=days * 24, freq="h")
    hour = timestamp.hour.to_numpy()
    working = timestamp.dayofweek.to_numpy() < 5
    temperature = 15 + 10 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 2, len(timestamp))
    rush = np.isin(hour, (8, 17, 18)) & working
    expected = 20 + 6 * np.clip(hour - 5, 0, 12) + 150 * rush + 3 * temperature - 40 * ~working
    frame = pd.DataFrame(
        {
            "timestamp": timestamp,
            "holiday": False,
            "weather_code": rng.choice([1, 2, 3], len(timestamp), p=[0.7, 0.25, 0.05]),
            "temperature_c": temperature.round(2),
            "feels_like_c": (temperature + 2).round(2),
            "humidity_pct": rng.uniform(30, 95, len(timestamp)).round(1),
            "windspeed_kmh": rng.uniform(0, 30, len(timestamp)).round(1),
            "rentals": rng.poisson(np.clip(expected, 1, None)),
        }
    )
    return frame[list(DOMAIN_COLUMNS)]


@pytest.fixture(scope="session")
def domain_frame() -> pd.DataFrame:
    return make_domain_frame()
