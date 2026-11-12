import time
from dataclasses import dataclass

import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline

from bikeshare.data.schema import INPUT_COLUMNS, TARGET, TIMESTAMP
from bikeshare.features import FeatureBuilder, build_features
from bikeshare.pipeline_config import TrainingConfig


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    test: pd.DataFrame
    cutoff: pd.Timestamp


@dataclass(frozen=True)
class TrainingResult:
    model: Pipeline
    baseline: "HourlyProfileBaseline"
    duration_seconds: float


def temporal_split(frame: pd.DataFrame, test_days: int) -> Split:
    """Les `test_days` derniers jours servent de test, tout ce qui précède d'entraînement."""
    cutoff = frame[TIMESTAMP].max().normalize() - pd.Timedelta(days=test_days - 1)
    train = frame[frame[TIMESTAMP] < cutoff]
    test = frame[frame[TIMESTAMP] >= cutoff]
    if train.empty or test.empty:
        raise ValueError(f"pas assez de données pour garder {test_days} jours de test")
    return Split(train=train, test=test, cutoff=cutoff)


class HourlyProfileBaseline(BaseEstimator, RegressorMixin):
    """Moyenne des locations par heure et type de jour (ouvré ou non)."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "HourlyProfileBaseline":
        keys = build_features(X)[["hour", "workingday"]]
        self.profile_ = y.groupby([keys["hour"], keys["workingday"]]).mean().to_dict()
        self.default_ = float(y.mean())
        return self

    def predict(self, X: pd.DataFrame) -> list[float]:
        keys = build_features(X)[["hour", "workingday"]]
        return [
            self.profile_.get((hour, workingday), self.default_)
            for hour, workingday in keys.itertuples(index=False)
        ]


def build_model(config: TrainingConfig) -> Pipeline:
    regressor = HistGradientBoostingRegressor(
        loss="poisson",
        random_state=config.seed,
        **config.model.model_dump(),
    )
    return Pipeline([("features", FeatureBuilder()), ("regressor", regressor)])


def train(train_frame: pd.DataFrame, config: TrainingConfig) -> TrainingResult:
    X = train_frame[list(INPUT_COLUMNS)]
    y = train_frame[TARGET]
    start = time.perf_counter()
    model = build_model(config).fit(X, y)
    baseline = HourlyProfileBaseline().fit(X, y)
    return TrainingResult(model, baseline, time.perf_counter() - start)
