from pathlib import Path

import pytest
from mlflow.tracking import MlflowClient

from bikeshare.data.acquisition import sha256_file
from bikeshare.data.replay import Corruption, publish_batches
from bikeshare.data.store import BatchStore
from bikeshare.data.validation import DataValidationError
from bikeshare.pipeline.flow import training_pipeline
from tests.unit.data.conftest import make_source

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("prefect_server")]

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL = "bikeshare-demand"

CONFIG = """
dataset:
  name: test
  member: hour.csv
  member_sha256: {sha}
  urls: [http://127.0.0.1:9/absent.zip]
replay:
  bootstrap_batches: 2
training:
  test_days: 5
  model:
    max_iter: 40
"""


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BatchStore:
    store = BatchStore(tmp_path / "data")
    store.source_dir.mkdir(parents=True)
    source = store.source_dir / "hour.csv"
    make_source(["2011-01", "2011-02", "2011-03", "2011-04"]).to_csv(source, index=False)
    config = tmp_path / "pipeline.yaml"
    config.write_text(CONFIG.format(sha=sha256_file(source)))

    monkeypatch.setenv("DATA_DIR", str(store.source_dir.parent))
    monkeypatch.setenv("PIPELINE_CONFIG", str(config))
    monkeypatch.setenv("PROMOTION_POLICY", str(REPO_ROOT / "config" / "promotion-policy.yaml"))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path}/mlflow.db")
    return store


@pytest.fixture
def mlflow_client(tmp_path: Path) -> MlflowClient:
    return MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")


def champion(client: MlflowClient) -> int:
    return int(client.get_model_version_by_alias(MODEL, "champion").version)


def test_first_run_trains_and_promotes(store: BatchStore, mlflow_client: MlflowClient) -> None:
    summary = training_pipeline()

    assert summary["outcome"] == "promoted"
    assert summary["batch_ids"] == ["2011-01", "2011-02"]
    assert champion(mlflow_client) == 1

    run = mlflow_client.get_run(summary["mlflow_run_id"])
    assert run.info.status == "FINISHED"
    assert run.data.tags["batch_ids"] == "2011-01,2011-02"
    assert run.data.tags["training_batches"] == "2011-01,2011-02"
    assert run.data.tags["decision"] == "approved"
    assert run.data.params["window_months"] == "12"
    assert {"test.mae", "baseline.mae", "test.rush_hours.mae"} <= set(run.data.metrics)
    assert {i.dataset.name for i in run.inputs.dataset_inputs} == {"train", "test"}
    artifacts = {a.path for a in mlflow_client.list_artifacts(run.info.run_id, "evaluation")}
    assert "evaluation/previsions.png" in artifacts
    version = mlflow_client.get_model_version(MODEL, "1")
    assert version.tags["validation_status"] == "approved"
    assert version.tags["feature_version"] == "1"


def test_each_run_takes_the_next_month(store: BatchStore, mlflow_client: MlflowClient) -> None:
    training_pipeline()

    second = training_pipeline()

    assert second["batch_ids"] == ["2011-03"]
    assert second["metrics"]["champion_mae"] is not None
    assert second["outcome"] in ("promoted", "champion_kept")
    assert len(mlflow_client.search_model_versions(f"name='{MODEL}'")) == 2


def test_invalid_batch_stops_the_run_and_keeps_the_champion(
    store: BatchStore, mlflow_client: MlflowClient
) -> None:
    training_pipeline()
    publish_batches(
        store.source_dir / "hour.csv",
        store.incoming_dir,
        delivered=store.delivered_ids(),
        corruption=Corruption.NEGATIVE_RENTALS,
    )

    with pytest.raises(DataValidationError, match="2011-03"):
        training_pipeline()

    assert champion(mlflow_client) == 1
    assert len(mlflow_client.search_model_versions(f"name='{MODEL}'")) == 1
    assert len(list(store.quarantine_dir.glob("2011-03-*.csv"))) == 1

    assert training_pipeline()["batch_ids"] == ["2011-03"]


def test_rejected_batch_is_redelivered_before_the_ones_already_waiting(
    store: BatchStore, mlflow_client: MlflowClient
) -> None:
    training_pipeline()
    source = store.source_dir / "hour.csv"
    publish_batches(
        source,
        store.incoming_dir,
        delivered=store.delivered_ids(),
        corruption=Corruption.MISSING_HOURS,
    )
    publish_batches(source, store.incoming_dir, delivered=store.delivered_ids())
    with pytest.raises(DataValidationError, match="2011-03"):
        training_pipeline()

    summary = training_pipeline()

    assert summary["batch_ids"] == ["2011-03", "2011-04"]
    run = mlflow_client.get_run(summary["mlflow_run_id"])
    assert run.data.tags["training_batches"] == "2011-01,2011-02,2011-03,2011-04"


def test_nothing_to_do_once_the_source_is_exhausted(store: BatchStore) -> None:
    for _ in range(3):
        training_pipeline()

    assert training_pipeline() == {"outcome": "no_new_data"}
