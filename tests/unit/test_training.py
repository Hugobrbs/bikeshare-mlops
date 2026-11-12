import numpy as np
import pandas as pd
import pytest

from bikeshare.data.schema import INPUT_COLUMNS, TARGET
from bikeshare.pipeline_config import ModelConfig, TrainingConfig
from bikeshare.training import HourlyProfileBaseline, build_model, temporal_split, train

CONFIG = TrainingConfig(model=ModelConfig(max_iter=60))


def test_split_keeps_the_last_days_for_test(domain_frame: pd.DataFrame) -> None:
    split = temporal_split(domain_frame, test_days=7)

    assert len(split.test) == 7 * 24
    assert split.train["timestamp"].max() < split.cutoff <= split.test["timestamp"].min()
    assert len(split.train) + len(split.test) == len(domain_frame)


def test_split_needs_enough_data(domain_frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="pas assez de données"):
        temporal_split(domain_frame.head(48), test_days=7)


def test_baseline_is_the_mean_per_hour_and_day_type(domain_frame: pd.DataFrame) -> None:
    X, y = domain_frame[list(INPUT_COLUMNS)], domain_frame[TARGET]
    monday_8am = (domain_frame["timestamp"].dt.hour == 8) & (
        domain_frame["timestamp"].dt.dayofweek < 5
    )

    baseline = HourlyProfileBaseline().fit(X, y)

    assert baseline.predict(X[monday_8am].head(1))[0] == pytest.approx(y[monday_8am].mean())


def test_baseline_falls_back_to_global_mean(domain_frame: pd.DataFrame) -> None:
    daytime = domain_frame[domain_frame["timestamp"].dt.hour >= 12]
    night = domain_frame[domain_frame["timestamp"].dt.hour == 3].head(1)

    baseline = HourlyProfileBaseline().fit(daytime[list(INPUT_COLUMNS)], daytime[TARGET])

    assert baseline.predict(night)[0] == pytest.approx(daytime[TARGET].mean())


def test_training_is_reproducible(domain_frame: pd.DataFrame) -> None:
    X = domain_frame[list(INPUT_COLUMNS)]

    first = train(domain_frame, CONFIG).model.predict(X)
    second = train(domain_frame, CONFIG).model.predict(X)

    np.testing.assert_array_equal(first, second)


def test_model_predicts_from_raw_inputs(domain_frame: pd.DataFrame) -> None:
    result = train(domain_frame, CONFIG)

    predictions = result.model.predict(domain_frame[list(INPUT_COLUMNS)].head(24))

    assert predictions.shape == (24,)
    assert (predictions >= 0).all()
    assert result.duration_seconds > 0


def test_model_config_reaches_the_regressor() -> None:
    config = TrainingConfig(seed=7, model=ModelConfig(max_iter=10, learning_rate=0.2))

    regressor = build_model(config).named_steps["regressor"]

    assert regressor.max_iter == 10
    assert regressor.learning_rate == 0.2
    assert regressor.random_state == 7
