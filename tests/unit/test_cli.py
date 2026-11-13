import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bikeshare import __version__
from bikeshare.cli import app
from tests.unit.data.conftest import make_source

runner = CliRunner()

CONFIG = """
dataset:
  name: test
  member: hour.csv
  member_sha256: {sha}
  urls: [http://127.0.0.1:9/absent.zip]
  timeout_seconds: 1
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dossier de données avec un faux hour.csv déjà "téléchargé"."""
    source = tmp_path / "data" / "source" / "hour.csv"
    source.parent.mkdir(parents=True)
    make_source(["2011-01", "2011-02"]).to_csv(source, index=False)
    config = tmp_path / "pipeline.yaml"
    config.write_text(CONFIG.format(sha="f" * 64))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PIPELINE_CONFIG", str(config))
    return tmp_path


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


def test_publish_then_status(project: Path) -> None:
    published = runner.invoke(app, ["data", "publish", "--count", "2"])
    status = runner.invoke(app, ["data", "status"])

    assert published.exit_code == 0
    assert "2011-01 : 744 lignes" in published.stdout
    assert json.loads(status.stdout) == {
        "accepted": [],
        "pending": ["2011-01", "2011-02"],
        "quarantined": [],
    }


def test_publish_when_everything_is_delivered(project: Path) -> None:
    runner.invoke(app, ["data", "publish", "--count", "2"])

    result = runner.invoke(app, ["data", "publish"])

    assert result.exit_code == 0
    assert "plus aucun lot" in result.stdout


def test_publish_with_corruption(project: Path) -> None:
    result = runner.invoke(app, ["data", "publish", "--corrupt", "missing-hours"])

    assert result.exit_code == 0
    assert "372 lignes" in result.stdout


def test_ingest_accepts_valid_batches(project: Path) -> None:
    runner.invoke(app, ["data", "publish", "--count", "2"])

    result = runner.invoke(app, ["data", "ingest"])
    status = json.loads(runner.invoke(app, ["data", "status"]).stdout)

    assert result.exit_code == 0
    assert status["accepted"] == ["2011-01", "2011-02"]
    assert status["pending"] == []


def test_ingest_fails_on_invalid_batch(project: Path) -> None:
    runner.invoke(app, ["data", "publish", "--corrupt", "negative-rentals"])

    result = runner.invoke(app, ["data", "ingest"])
    status = json.loads(runner.invoke(app, ["data", "status"]).stdout)

    assert result.exit_code == 1
    assert status["accepted"] == []
    assert len(status["quarantined"]) == 1


def test_publish_without_dataset(project: Path) -> None:
    (project / "data" / "source" / "hour.csv").unlink()

    result = runner.invoke(app, ["data", "publish"])

    assert result.exit_code == 1


def test_download_failure_exits_with_error(project: Path) -> None:
    # le fichier local n'a pas la bonne empreinte et l'URL ne répond pas
    result = runner.invoke(app, ["data", "download"])

    assert result.exit_code == 1
