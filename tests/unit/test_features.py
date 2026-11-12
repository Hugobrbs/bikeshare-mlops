import pandas as pd
import pytest

from bikeshare.features import FEATURES, FeatureBuilder, build_features


def make_frame(**overrides: object) -> pd.DataFrame:
    row = {
        "timestamp": pd.Timestamp("2011-07-04 08:00"),
        "holiday": True,
        "weather_code": 1,
        "temperature_c": 28.7,
        "feels_like_c": 31.8,
        "humidity_pct": 65.0,
        "windspeed_kmh": 11.0,
    }
    return pd.DataFrame([{**row, **overrides}])


def test_calendar_features() -> None:
    row = build_features(make_frame()).iloc[0]

    assert row["hour"] == 8
    assert row["dayofweek"] == 0
    assert row["month"] == 7
    assert list(build_features(make_frame()).columns) == list(FEATURES)


@pytest.mark.parametrize(
    ("timestamp", "holiday", "workingday"),
    [
        ("2011-07-05 08:00", False, 1),
        ("2011-07-04 08:00", True, 0),
        ("2011-07-09 08:00", False, 0),
    ],
    ids=["mardi", "lundi-férié", "samedi"],
)
def test_workingday(timestamp: str, holiday: bool, workingday: int) -> None:
    frame = make_frame(timestamp=pd.Timestamp(timestamp), holiday=holiday)

    assert build_features(frame).iloc[0]["workingday"] == workingday


def test_unknown_weather_code_is_clipped() -> None:
    assert build_features(make_frame(weather_code=9)).iloc[0]["weather_code"] == 4
    assert build_features(make_frame(weather_code=0)).iloc[0]["weather_code"] == 1


def test_timestamp_can_be_a_string() -> None:
    features = build_features(make_frame(timestamp="2011-07-04T17:00:00"))

    assert features.iloc[0]["hour"] == 17


def test_target_is_ignored() -> None:
    frame = make_frame()
    frame["rentals"] = 120

    assert "rentals" not in build_features(frame)


def test_feature_builder_is_stateless() -> None:
    frame = make_frame()
    builder = FeatureBuilder()

    pd.testing.assert_frame_equal(builder.fit(frame).transform(frame), build_features(frame))
    assert list(builder.get_feature_names_out()) == list(FEATURES)
