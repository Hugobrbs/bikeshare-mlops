import calendar
import logging
import math
import re
from dataclasses import asdict, dataclass, field

import great_expectations as gx
import great_expectations.expectations as gxe
import pandas as pd
from great_expectations.data_context.types.base import ProgressBarsConfig

from bikeshare.data.schema import SOURCE_COLUMNS
from bikeshare.pipeline_config import ValidationConfig

REQUIRED = ("dteday", "hr", "weathersit", "temp", "atemp", "hum", "windspeed", "cnt")
NORMALIZED = ("temp", "atemp", "hum", "windspeed")
BATCH_ID = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass
class ValidationReport:
    batch_id: str
    success: bool
    rows: int
    checks: int
    failures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class DataValidationError(RuntimeError):
    def __init__(self, report: ValidationReport) -> None:
        checks = ", ".join(f["check"] for f in report.failures)
        super().__init__(f"lot {report.batch_id} rejeté : {checks}")
        self.report = report


def expected_hours(batch_id: str) -> int:
    year, month = map(int, batch_id.split("-"))
    return calendar.monthrange(year, month)[1] * 24


def following_month(batch_id: str) -> str:
    return str(pd.Period(batch_id, freq="M") + 1)


def build_expectations(batch_id: str, rules: ValidationConfig) -> list:
    hours = expected_hours(batch_id)
    days = hours // 24
    return [
        *[gxe.ExpectColumnToExist(column=c) for c in SOURCE_COLUMNS],
        # volume
        gxe.ExpectTableRowCountToBeBetween(
            min_value=math.ceil(rules.min_hour_coverage * hours), max_value=hours
        ),
        gxe.ExpectColumnUniqueValueCountToBeBetween(
            column="dteday", min_value=days - rules.max_missing_days, max_value=days
        ),
        # qualité technique
        *[gxe.ExpectColumnValuesToNotBeNull(column=c) for c in REQUIRED],
        gxe.ExpectColumnValuesToMatchRegex(column="dteday", regex=rf"^{batch_id}-\d{{2}}$"),
        gxe.ExpectCompoundColumnsToBeUnique(column_list=["dteday", "hr"]),
        gxe.ExpectColumnValuesToBeBetween(column="hr", min_value=0, max_value=23),
        gxe.ExpectColumnValuesToBeBetween(column="cnt", min_value=0, max_value=rules.max_rentals),
        *[
            gxe.ExpectColumnValuesToBeBetween(column=c, min_value=0, max_value=1)
            for c in NORMALIZED
        ],
        gxe.ExpectColumnValuesToBeInSet(column="weathersit", value_set=[1, 2, 3, 4]),
        gxe.ExpectColumnValuesToBeInSet(column="holiday", value_set=[0, 1]),
        # distribution
        gxe.ExpectColumnDistinctValuesToContainSet(column="hr", value_set=list(range(24))),
        gxe.ExpectColumnStdevToBeBetween(column="temp", min_value=rules.min_temperature_std),
        gxe.ExpectColumnMeanToBeBetween(
            column="cnt", min_value=rules.mean_rentals_min, max_value=rules.mean_rentals_max
        ),
    ]


def validate_batch(
    batch: pd.DataFrame,
    batch_id: str,
    rules: ValidationConfig,
    previous_batch_id: str | None = None,
) -> ValidationReport:
    if not BATCH_ID.match(batch_id):
        raise ValueError(f"identifiant de lot invalide : {batch_id!r} (attendu AAAA-MM)")

    # GX journalise en INFO chaque dossier temporaire qu'il crée
    logging.getLogger("great_expectations.data_context.types.base").setLevel(logging.WARNING)
    expectations = build_expectations(batch_id, rules)
    context = gx.get_context(mode="ephemeral")
    context.variables.progress_bars = ProgressBarsConfig(globally=False)
    suite = context.suites.add(gx.ExpectationSuite(name="batch"))
    for expectation in expectations:
        suite.add_expectation(expectation)
    asset = context.data_sources.add_pandas("pandas").add_dataframe_asset("batch")
    definition = context.validation_definitions.add(
        gx.ValidationDefinition(
            name="batch", data=asset.add_batch_definition_whole_dataframe("all"), suite=suite
        )
    )
    result = definition.run(batch_parameters={"dataframe": batch})

    failures = [_describe(e) for e in result.describe_dict()["expectations"] if not e["success"]]
    failures += _extra_checks(batch, batch_id, previous_batch_id)
    return ValidationReport(
        batch_id=batch_id,
        success=not failures,
        rows=len(batch),
        checks=len(expectations) + 2,
        failures=failures,
    )


def _describe(expectation: dict) -> dict:
    kwargs = expectation.get("kwargs", {})
    result = expectation.get("result", {})
    failure = {
        "check": expectation["expectation_type"],
        "column": kwargs.get("column") or kwargs.get("column_list"),
    }
    for key in ("observed_value", "unexpected_count"):
        if key in result:
            failure[key] = result[key]
    if result.get("partial_unexpected_list"):
        failure["examples"] = result["partial_unexpected_list"][:5]
    return failure


def _extra_checks(batch: pd.DataFrame, batch_id: str, previous_batch_id: str | None) -> list[dict]:
    """Contrôles qui ne s'expriment pas avec une expectation GX."""
    failures = []
    if previous_batch_id and batch_id != following_month(previous_batch_id):
        failures.append(
            {
                "check": "batch_follows_previous_one",
                "observed_value": batch_id,
                "expected": following_month(previous_batch_id),
            }
        )
    if {"cnt", "casual", "registered"} <= set(batch.columns):
        mismatch = int((batch["cnt"] != batch["casual"] + batch["registered"]).sum())
        if mismatch:
            failures.append(
                {"check": "cnt_equals_casual_plus_registered", "unexpected_count": mismatch}
            )
    return failures
