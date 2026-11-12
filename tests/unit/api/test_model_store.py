from pathlib import Path
from uuid import UUID, uuid4

import mlflow
import pandas as pd
import pytest
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from bikeshare.api.model_store import IncompatibleModelError, ModelStore
from bikeshare.data.schema import INPUT_COLUMNS
from bikeshare.pipeline_config import ModelConfig, TrainingConfig
from bikeshare.registry.tracking import RegisteredVersion, TrainingRun
from bikeshare.training import train

NAME = "bikeshare-test"


@pytest.fixture
def tracking_uri(tmp_path: Path) -> str:
    uri = f"sqlite:///{tmp_path}/mlflow.db"
    mlflow.set_tracking_uri(uri)
    return uri


@pytest.fixture
def registry(tracking_uri: str) -> MlflowClient:
    return MlflowClient(tracking_uri)


def publish(uri: str, frame: pd.DataFrame, feature_version: str = "1") -> RegisteredVersion:
    run = TrainingRun.start(uri, "tests", "run", {})
    model = train(frame, TrainingConfig(model=ModelConfig(max_iter=10))).model
    version = run.log_model(model, frame.head(20), NAME, {"feature_version": feature_version})
    run.finish()
    return version


def make_store(uri: str) -> tuple[ModelStore, list[tuple]]:
    registered: list[tuple] = []

    def register(tracking_uri: str, name: str, version: int, run_id: str) -> UUID:
        registered.append((name, version, run_id))
        return uuid4()

    return ModelStore(uri, NAME, "champion", register), registered


def test_loads_the_version_behind_the_alias(
    tracking_uri: str, registry: MlflowClient, domain_frame: pd.DataFrame
) -> None:
    version = publish(tracking_uri, domain_frame)
    registry.set_registered_model_alias(NAME, "champion", version.version)
    store, registered = make_store(tracking_uri)

    assert store.refresh() is True

    assert store.current.version == 1
    assert store.current.run_id == version.run_id
    assert registered == [(NAME, 1, version.run_id)]
    assert len(store.current.pipeline.predict(domain_frame[list(INPUT_COLUMNS)].head(5))) == 5


def test_refresh_does_nothing_while_the_alias_does_not_move(
    tracking_uri: str, registry: MlflowClient, domain_frame: pd.DataFrame
) -> None:
    registry.set_registered_model_alias(
        NAME, "champion", publish(tracking_uri, domain_frame).version
    )
    store, registered = make_store(tracking_uri)
    store.refresh()
    loaded = store.current

    assert store.refresh() is False
    assert store.current is loaded
    assert len(registered) == 1


def test_follows_promotion_and_rollback(
    tracking_uri: str, registry: MlflowClient, domain_frame: pd.DataFrame
) -> None:
    v1, v2 = publish(tracking_uri, domain_frame), publish(tracking_uri, domain_frame)
    registry.set_registered_model_alias(NAME, "champion", v1.version)
    store, _ = make_store(tracking_uri)
    store.refresh()

    registry.set_registered_model_alias(NAME, "champion", v2.version)
    assert store.refresh() is True
    assert store.current.version == 2

    registry.set_registered_model_alias(NAME, "champion", v1.version)
    assert store.refresh() is True
    assert store.current.version == 1


def test_no_champion_yet(tracking_uri: str) -> None:
    store, _ = make_store(tracking_uri)

    with pytest.raises(MlflowException):
        store.refresh()

    assert store.current is None
    assert "MlflowException" in store.last_error or "RestException" in store.last_error


def test_model_trained_with_other_features_is_refused(
    tracking_uri: str, registry: MlflowClient, domain_frame: pd.DataFrame
) -> None:
    good = publish(tracking_uri, domain_frame)
    registry.set_registered_model_alias(NAME, "champion", good.version)
    store, _ = make_store(tracking_uri)
    store.refresh()
    incompatible = publish(tracking_uri, domain_frame, feature_version="0")
    registry.set_registered_model_alias(NAME, "champion", incompatible.version)

    with pytest.raises(IncompatibleModelError):
        store.refresh()

    # le modèle compatible déjà chargé continue de servir
    assert store.current.version == 1
    assert "IncompatibleModelError" in store.last_error


def test_registry_outage_keeps_the_loaded_model(
    tracking_uri: str,
    registry: MlflowClient,
    domain_frame: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # sans ça le client mlflow réessaie pendant plusieurs minutes
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "2")
    registry.set_registered_model_alias(
        NAME, "champion", publish(tracking_uri, domain_frame).version
    )
    store, _ = make_store(tracking_uri)
    store.refresh()
    store.client = MlflowClient("http://127.0.0.1:9")

    with pytest.raises(MlflowException):
        store.refresh()

    assert store.current.version == 1
    assert store.last_error is not None
