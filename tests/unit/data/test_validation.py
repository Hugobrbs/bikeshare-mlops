import pandas as pd
import pytest

from bikeshare.data.replay import Corruption, corrupt
from bikeshare.data.validation import (
    DataValidationError,
    expected_hours,
    following_month,
    validate_batch,
)
from bikeshare.pipeline_config import ValidationConfig
from tests.unit.data.conftest import make_source

RULES = ValidationConfig()


@pytest.fixture(scope="module")
def january() -> pd.DataFrame:
    return make_source(["2011-01"])


def failed_checks(batch: pd.DataFrame, batch_id: str = "2011-01", **kwargs: str) -> set[str]:
    report = validate_batch(batch, batch_id, RULES, **kwargs)
    return {f["check"] for f in report.failures}


def test_clean_batch_passes(january: pd.DataFrame) -> None:
    report = validate_batch(january, "2011-01", RULES)

    assert report.success
    assert report.failures == []
    assert report.rows == 744
    assert report.checks > 30


@pytest.mark.parametrize(
    ("corruption", "expected"),
    [
        (Corruption.MISSING_HOURS, "expect_table_row_count_to_be_between"),
        (Corruption.NEGATIVE_RENTALS, "expect_column_values_to_be_between"),
        (Corruption.DROPPED_COLUMN, "expect_column_to_exist"),
        (Corruption.FROZEN_SENSOR, "expect_column_stdev_to_be_between"),
        (Corruption.DUPLICATED_ROWS, "expect_compound_columns_to_be_unique"),
    ],
)
def test_each_corruption_is_caught(
    january: pd.DataFrame, corruption: Corruption, expected: str
) -> None:
    assert expected in failed_checks(corrupt(january, corruption))


def test_failure_gives_details(january: pd.DataFrame) -> None:
    report = validate_batch(corrupt(january, Corruption.NEGATIVE_RENTALS), "2011-01", RULES)

    [failure] = [f for f in report.failures if f.get("column") == "cnt"]
    assert failure["unexpected_count"] == 37
    assert all(value < 0 for value in failure["examples"])


def test_batch_from_another_month_is_rejected(january: pd.DataFrame) -> None:
    assert "expect_column_values_to_match_regex" in failed_checks(january, "2011-03")


def test_missing_values_are_rejected(january: pd.DataFrame) -> None:
    batch = january.copy()
    batch.loc[batch.index[:10], "hum"] = None

    assert "expect_column_values_to_not_be_null" in failed_checks(batch)


def test_unknown_weather_code_is_rejected(january: pd.DataFrame) -> None:
    batch = january.copy()
    batch.loc[batch.index[0], "weathersit"] = 7

    assert "expect_column_values_to_be_in_set" in failed_checks(batch)


def test_inconsistent_total_is_rejected(january: pd.DataFrame) -> None:
    batch = january.copy()
    batch.loc[batch.index[:3], "cnt"] += 1

    assert "cnt_equals_casual_plus_registered" in failed_checks(batch)


def test_gap_between_batches_is_rejected(january: pd.DataFrame) -> None:
    assert failed_checks(january, previous_batch_id="2010-12") == set()
    assert failed_checks(january, previous_batch_id="2010-10") == {"batch_follows_previous_one"}


def test_invalid_batch_id() -> None:
    with pytest.raises(ValueError, match="AAAA-MM"):
        validate_batch(pd.DataFrame(), "janvier", RULES)


def test_error_message_lists_failed_checks(january: pd.DataFrame) -> None:
    report = validate_batch(corrupt(january, Corruption.MISSING_HOURS), "2011-01", RULES)

    error = DataValidationError(report)

    assert "2011-01" in str(error)
    assert "expect_table_row_count_to_be_between" in str(error)
    assert error.report is report


def test_month_helpers() -> None:
    assert expected_hours("2012-02") == 29 * 24
    assert following_month("2011-12") == "2012-01"
