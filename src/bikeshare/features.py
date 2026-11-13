import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

from bikeshare.data.schema import TIMESTAMP, WEATHER_CODES

# à incrémenter dès que build_features change
FEATURE_VERSION = "1"

FEATURES = (
    "hour",
    "dayofweek",
    "month",
    "holiday",
    "workingday",
    "weather_code",
    "temperature_c",
    "feels_like_c",
    "humidity_pct",
    "windspeed_kmh",
)


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    timestamp = pd.to_datetime(frame[TIMESTAMP])
    holiday = frame["holiday"].astype(bool)
    features = pd.DataFrame(
        {
            "hour": timestamp.dt.hour,
            "dayofweek": timestamp.dt.dayofweek,
            "month": timestamp.dt.month,
            "holiday": holiday.astype(int),
            "workingday": ((timestamp.dt.dayofweek < 5) & ~holiday).astype(int),
            "weather_code": frame["weather_code"].clip(WEATHER_CODES[0], WEATHER_CODES[-1]),
            "temperature_c": frame["temperature_c"],
            "feels_like_c": frame["feels_like_c"],
            "humidity_pct": frame["humidity_pct"],
            "windspeed_kmh": frame["windspeed_kmh"],
        },
        index=frame.index,
    )
    return features[list(FEATURES)].astype("float64")


class FeatureBuilder(BaseEstimator, TransformerMixin):
    def fit(self, X: pd.DataFrame, y: object = None) -> "FeatureBuilder":
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        return build_features(X)

    def get_feature_names_out(self, input_features: object = None) -> np.ndarray:
        return np.array(FEATURES)
