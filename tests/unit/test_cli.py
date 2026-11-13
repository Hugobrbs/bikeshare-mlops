import json

import pytest
from typer.testing import CliRunner

from bikeshare import __version__
from bikeshare.cli import app

runner = CliRunner()


def test_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_REVISION", "abc1234")

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout
    assert "abc1234" in result.stdout


def test_show_config_prints_json() -> None:
    result = runner.invoke(app, ["show-config"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["environment"] == "local"
