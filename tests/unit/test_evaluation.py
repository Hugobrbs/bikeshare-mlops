import numpy as np
import pandas as pd
import pytest

from bikeshare.evaluation import evaluate, regression_metrics, segment_masks
from bikeshare.pipeline_config import ModelConfig, TrainingConfig
from bikeshare.training import temporal_split, train


class Constant:
    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.full(len(X), self.value)


def test_regression_metrics() -> None:
    metrics = regression_metrics(pd.Series([10, 20, 30]), np.array([12, 18, 33]))

    assert metrics["mae"] == pytest.approx(7 / 3)
    assert metrics["rmse"] == pytest.approx((17 / 3) ** 0.5)
    assert metrics["r2"] == pytest.approx(1 - 17 / 200)


def test_segments_cover_expected_hours(domain_frame: pd.DataFrame) -> None:
    masks = segment_masks(domain_frame)
    hours = domain_frame["timestamp"].dt.hour
    weekdays = domain_frame["timestamp"].dt.dayofweek

    assert set(hours[masks["rush_hours"]]) == {7, 8, 9, 17, 18, 19}
    assert (weekdays[masks["rush_hours"]] < 5).all()
    assert hours[masks["night"]].max() == 5
    assert (weekdays[masks["weekend_or_holiday"]] >= 5).all()


def test_evaluate_reports_global_and_segment_errors(domain_frame: pd.DataFrame) -> None:
    report = evaluate(Constant(100.0), domain_frame)

    assert report["rows"] == len(domain_frame)
    assert report["mae"] == pytest.approx((domain_frame["rentals"] - 100).abs().mean())
    assert set(report["segments"]) == {"rush_hours", "night", "weekend_or_holiday"}
    assert report["segments"]["night"]["rows"] == 60 * 6


def test_empty_segment_is_skipped(domain_frame: pd.DataFrame) -> None:
    afternoon = domain_frame[domain_frame["timestamp"].dt.hour.between(12, 15)]

    report = evaluate(Constant(100.0), afternoon)

    assert "night" not in report["segments"]
    assert "rush_hours" not in report["segments"]


def test_model_beats_baseline_on_unseen_days(domain_frame: pd.DataFrame) -> None:
    split = temporal_split(domain_frame, test_days=7)
    result = train(split.train, TrainingConfig(model=ModelConfig(max_iter=80)))

    model = evaluate(result.model, split.test)
    baseline = evaluate(result.baseline, split.test)

    assert model["mae"] < baseline["mae"]
    assert model["r2"] > 0.7
