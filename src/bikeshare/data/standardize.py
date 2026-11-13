import pandas as pd

from bikeshare.data.schema import (
    DOMAIN_COLUMNS,
    FEELS_LIKE_SCALE_C,
    HUMIDITY_SCALE_PCT,
    TARGET,
    TEMPERATURE_SCALE_C,
    TIMESTAMP,
    WINDSPEED_SCALE_KMH,
)


def to_domain(source: pd.DataFrame) -> pd.DataFrame:
    """Passe du format UCI (valeurs normalisées) à des unités physiques.

    casual et registered sont écartées : leur somme est exactement la cible.
    """
    timestamp = pd.to_datetime(source["dteday"], format="%Y-%m-%d") + pd.to_timedelta(
        source["hr"], unit="h"
    )
    domain = pd.DataFrame(
        {
            TIMESTAMP: timestamp,
            "holiday": source["holiday"].astype(bool),
            "weather_code": source["weathersit"].astype("int64"),
            "temperature_c": (source["temp"] * TEMPERATURE_SCALE_C).round(2),
            "feels_like_c": (source["atemp"] * FEELS_LIKE_SCALE_C).round(2),
            "humidity_pct": (source["hum"] * HUMIDITY_SCALE_PCT).round(2),
            "windspeed_kmh": (source["windspeed"] * WINDSPEED_SCALE_KMH).round(2),
            TARGET: source["cnt"].astype("int64"),
        }
    )
    return domain[list(DOMAIN_COLUMNS)].sort_values(TIMESTAMP, ignore_index=True)
