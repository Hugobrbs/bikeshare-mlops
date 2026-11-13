import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from bikeshare.data.store import BatchRecord, BatchStore
from bikeshare.data.validation import DataValidationError, ValidationReport, validate_batch
from bikeshare.pipeline_config import ValidationConfig

logger = logging.getLogger(__name__)


def validate_file(
    path: Path, rules: ValidationConfig, previous_batch_id: str | None
) -> ValidationReport:
    try:
        batch = pd.read_csv(path)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as e:
        failure = {"check": "file_is_readable", "observed_value": str(e)}
        return ValidationReport(path.stem, False, 0, 1, [failure])
    return validate_batch(batch, path.stem, rules, previous_batch_id)


def ingest_pending(
    store: BatchStore,
    rules: ValidationConfig,
    on_report: Callable[[dict], None] | None = None,
) -> list[BatchRecord]:
    accepted = []
    for incoming in store.pending():
        known = store.accepted()
        try:
            if incoming.stem in {r.batch_id for r in known}:
                store.accept(incoming)
                continue
            report = validate_file(incoming, rules, known[-1].batch_id if known else None)
        except Exception as e:
            failure = {"check": "validation_crashed", "observed_value": repr(e)[:500]}
            report = ValidationReport(incoming.stem, False, 0, 1, [failure])

        store.save_report(report.to_dict())
        if on_report:
            on_report(report.to_dict())
        if not report.success:
            quarantined = store.quarantine(incoming, report.to_dict())
            logger.error("lot %s rejeté, déplacé vers %s", report.batch_id, quarantined)
            raise DataValidationError(report)

        record = store.accept(incoming)
        accepted.append(record)
        logger.info("lot %s accepté (%d lignes)", record.batch_id, record.rows)
    return accepted
