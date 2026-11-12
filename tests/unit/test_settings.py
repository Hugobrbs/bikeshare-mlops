from pathlib import Path

import pytest
from pydantic import ValidationError

from bikeshare.settings import Settings, get_settings


def test_defaults() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.log_format == "text"
    assert settings.data_dir == Path("data")


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("DATA_DIR", "/srv/data")

    settings = Settings()

    assert settings.log_format == "json"
    assert settings.data_dir == Path("/srv/data")


def test_unknown_log_format_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "xml")

    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_settings()
    monkeypatch.setenv("LOG_FORMAT", "json")

    assert get_settings() is first
