from pathlib import Path

import mlflow
import pytest
from mlflow.tracking import MlflowClient


@pytest.fixture
def tracking_uri(tmp_path: Path) -> str:
    """MLflow réel, sur un fichier sqlite jetable."""
    uri = f"sqlite:///{tmp_path}/mlflow.db"
    mlflow.set_tracking_uri(uri)
    return uri


@pytest.fixture
def client(tracking_uri: str) -> MlflowClient:
    return MlflowClient(tracking_uri)
