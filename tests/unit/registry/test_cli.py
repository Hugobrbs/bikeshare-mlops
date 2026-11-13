import json

import pytest
from mlflow.tracking import MlflowClient
from typer.testing import CliRunner

from bikeshare.cli import app
from bikeshare.registry.models import ModelRegistry
from bikeshare.registry.policy import Decision, PromotionPolicy

runner = CliRunner()
NAME = "bikeshare-demand"
POLICY = PromotionPolicy(policy_id="test", version="1", min_r2=0.5, min_gain_vs_baseline_pct=15)


@pytest.fixture
def registry(
    client: MlflowClient, tracking_uri: str, monkeypatch: pytest.MonkeyPatch
) -> ModelRegistry:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", tracking_uri)
    client.create_registered_model(NAME)
    return ModelRegistry(client, NAME)


def add_version(registry: ModelRegistry, status: str) -> str:
    version = str(registry.client.create_model_version(NAME, source="file:///tmp/model").version)
    registry.record_decision(version, Decision(status, False, ["test"]), POLICY)
    return version


def test_status_lists_versions_and_aliases(registry: ModelRegistry) -> None:
    v1, v2 = add_version(registry, "approved"), add_version(registry, "rejected")
    registry.promote(v1, actor="pipeline", reason="premier")

    result = runner.invoke(app, ["registry", "status"])

    rows = json.loads(result.stdout)
    assert [(r["version"], r["status"], r["aliases"]) for r in rows] == [
        (int(v1), "approved", ["champion"]),
        (int(v2), "rejected", []),
    ]


def test_promote_then_rollback(registry: ModelRegistry) -> None:
    v1, v2 = add_version(registry, "approved"), add_version(registry, "approved")
    registry.promote(v1, actor="pipeline", reason="premier")

    promoted = runner.invoke(app, ["registry", "promote", v2, "--reason", "meilleur en test"])
    assert promoted.exit_code == 0
    assert str(registry.champion().version) == v2

    rolled_back = runner.invoke(app, ["registry", "rollback", "--reason", "régression"])
    assert rolled_back.exit_code == 0
    assert f"v{v2} -> v{v1}" in rolled_back.stdout
    assert str(registry.champion().version) == v1


def test_rejected_version_is_refused(registry: ModelRegistry) -> None:
    rejected = add_version(registry, "rejected")

    result = runner.invoke(app, ["registry", "promote", rejected, "--reason", "essai"])

    assert result.exit_code == 1
    assert registry.champion() is None


def test_rollback_without_history_is_refused(registry: ModelRegistry) -> None:
    assert runner.invoke(app, ["registry", "rollback", "--reason", "x"]).exit_code == 1
