import pandas as pd
import pytest
from mlflow.tracking import MlflowClient

from bikeshare.evaluation import evaluate
from bikeshare.pipeline_config import ModelConfig, TrainingConfig
from bikeshare.registry.tracking import TrainingRun, flatten, load_model, reload_delta
from bikeshare.training import train

NAME = "bikeshare-test"


@pytest.fixture
def run(tracking_uri: str) -> TrainingRun:
    return TrainingRun.start(tracking_uri, "tests", "run-de-test", {"batch_id": "2011-03"})


def test_flatten() -> None:
    report = {"mae": 12.5, "rows": 168, "segments": {"night": {"mae": 3.0, "rows": 42}}}

    assert flatten(report, "test") == {"test.mae": 12.5, "test.rows": 168.0, "test.night.mae": 3.0}


def test_run_keeps_params_metrics_tags_and_data(
    run: TrainingRun, client: MlflowClient, domain_frame: pd.DataFrame
) -> None:
    run.log_params({"seed": 42, "window_months": 12})
    run.log_metrics({"test.mae": 21.5, "test.r2": float("nan")})
    run.set_tags({"data_fingerprint": "abc"})
    run.log_json({"success": True}, "validation/2011-03.json")
    run.log_dataset(domain_frame, "train", "training")
    run.finish()

    stored = client.get_run(run.run_id)
    assert stored.data.params == {"seed": "42", "window_months": "12"}
    assert stored.data.metrics == {"test.mae": 21.5}
    assert stored.data.tags["batch_id"] == "2011-03"
    assert stored.data.tags["data_fingerprint"] == "abc"
    assert stored.inputs.dataset_inputs[0].dataset.name == "train"
    assert stored.info.status == "FINISHED"
    assert "validation" in [a.path for a in client.list_artifacts(run.run_id)]


def test_model_is_registered_and_reloads_identically(
    run: TrainingRun, client: MlflowClient, domain_frame: pd.DataFrame
) -> None:
    model = train(domain_frame, TrainingConfig(model=ModelConfig(max_iter=20))).model

    version = run.log_model(model, domain_frame.head(50), NAME, {"feature_version": "1"})

    assert version.uri == f"models:/{NAME}/1"
    assert client.get_model_version(NAME, "1").tags == {"feature_version": "1"}
    assert client.get_model_version(NAME, "1").run_id == run.run_id
    assert client.get_run(run.run_id).info.status == "RUNNING"
    assert reload_delta(version, model, domain_frame.head(200)) == 0.0
    assert (
        evaluate(load_model(version.uri), domain_frame)["mae"]
        == evaluate(model, domain_frame)["mae"]
    )


def test_publishing_twice_keeps_a_single_version(
    run: TrainingRun, client: MlflowClient, domain_frame: pd.DataFrame
) -> None:
    model = train(domain_frame, TrainingConfig(model=ModelConfig(max_iter=20))).model

    first = run.log_model(model, domain_frame.head(50), NAME)
    second = run.log_model(model, domain_frame.head(50), NAME)

    assert second == first
    assert len(client.search_model_versions(f"name='{NAME}'")) == 1


def test_failed_run_is_marked_failed(run: TrainingRun, client: MlflowClient) -> None:
    run.finish("FAILED")

    assert client.get_run(run.run_id).info.status == "FAILED"
