import json
import logging
import sys

import pytest

from bikeshare.logs import JsonFormatter, configure_logging


def make_record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("bikeshare.test", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_json_formatter_outputs_one_line_with_extras() -> None:
    line = JsonFormatter().format(make_record("requête traitée", request_id="abc", status=200))

    payload = json.loads(line)
    assert "\n" not in line
    assert payload["message"] == "requête traitée"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "abc"
    assert payload["status"] == 200


def test_json_formatter_includes_exception() -> None:
    record = make_record("échec")
    try:
        raise ValueError("lot invalide")
    except ValueError:
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: lot invalide" in payload["exception"]


def test_configure_logging_replaces_handlers(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    configure_logging("WARNING", "json")

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.WARNING

    logging.getLogger("bikeshare.test").warning("alerte")
    assert json.loads(capsys.readouterr().err)["message"] == "alerte"
