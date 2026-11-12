import warnings
from dataclasses import dataclass

import mlflow
import numpy as np
import pandas as pd
from mlflow.entities import DatasetInput, InputTag, Metric, Param
from mlflow.tracking import MlflowClient
from sklearn.pipeline import Pipeline

from bikeshare.data.schema import INPUT_COLUMNS, TARGET

# mlflow prévient qu'une colonne entière ne peut pas contenir de valeur manquante :
# sans objet ici, l'API valide les entrées avant le modèle
warnings.filterwarnings("ignore", message=".*Inferred schema contains integer column.*")

# Le format skops ne charge que les types listés ici (contrairement à pickle,
# qui exécute n'importe quel code au chargement).
SKOPS_TRUSTED_TYPES = [
    "bikeshare.features.FeatureBuilder",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
]


@dataclass(frozen=True)
class RegisteredVersion:
    name: str
    version: str
    run_id: str

    @property
    def uri(self) -> str:
        return f"models:/{self.name}/{self.version}"


def flatten(report: dict, prefix: str) -> dict[str, float]:
    """{"mae": 1, "segments": {"night": {"mae": 2}}} -> {"test.mae": 1, "test.night.mae": 2}"""
    flat = {}
    for key, value in report.items():
        if key == "segments":
            for name, segment in value.items():
                flat[f"{prefix}.{name}.mae"] = float(segment["mae"])
        elif isinstance(value, int | float):
            flat[f"{prefix}.{key}"] = float(value)
    return flat


class TrainingRun:
    """Run MLflow manipulé par son run_id, sans dépendre du run actif global."""

    def __init__(self, client: MlflowClient, run_id: str) -> None:
        self.client = client
        self.run_id = run_id

    @classmethod
    def start(cls, tracking_uri: str, experiment: str, name: str, tags: dict) -> "TrainingRun":
        mlflow.set_tracking_uri(tracking_uri)
        client = MlflowClient(tracking_uri)
        existing = client.get_experiment_by_name(experiment)
        experiment_id = existing.experiment_id if existing else client.create_experiment(experiment)
        run = client.create_run(
            experiment_id, run_name=name, tags={k: str(v) for k, v in tags.items()}
        )
        return cls(client, run.info.run_id)

    def log_params(self, params: dict) -> None:
        self.client.log_batch(self.run_id, params=[Param(k, str(v)) for k, v in params.items()])

    def log_metrics(self, metrics: dict[str, float]) -> None:
        finite = {k: v for k, v in metrics.items() if np.isfinite(v)}
        self.client.log_batch(self.run_id, metrics=[Metric(k, v, 0, 0) for k, v in finite.items()])

    def set_tags(self, tags: dict) -> None:
        for key, value in tags.items():
            self.client.set_tag(self.run_id, key, str(value))

    def log_json(self, payload: dict, artifact_file: str) -> None:
        self.client.log_dict(self.run_id, payload, artifact_file)

    def log_figure(self, figure: object, artifact_file: str) -> None:
        self.client.log_figure(self.run_id, figure, artifact_file)

    def log_dataset(self, frame: pd.DataFrame, name: str, context: str) -> None:
        dataset = mlflow.data.from_pandas(frame, name=name, targets=TARGET)
        tag = InputTag(key="mlflow.data.context", value=context)
        self.client.log_inputs(self.run_id, [DatasetInput(dataset._to_mlflow_entity(), [tag])])

    def log_model(
        self,
        model: Pipeline,
        sample: pd.DataFrame,
        registered_name: str,
        version_tags: dict[str, str] | None = None,
    ) -> RegisteredVersion:
        # si la publication est rejouée après un échec partiel, on ne crée pas une 2e version
        existing = self.client.search_model_versions(f"run_id='{self.run_id}'")
        if existing:
            return RegisteredVersion(registered_name, str(existing[0].version), self.run_id)

        inputs = sample[list(INPUT_COLUMNS)]
        with mlflow.start_run(run_id=self.run_id):
            info = mlflow.sklearn.log_model(
                sk_model=model,
                name="model",
                registered_model_name=registered_name,
                signature=mlflow.models.infer_signature(inputs, model.predict(inputs)),
                input_example=inputs.head(3),
                skops_trusted_types=SKOPS_TRUSTED_TYPES,
            )
        # sortir du with clôt le run, on le rouvre : il reste des choses à journaliser
        self.client.update_run(self.run_id, status="RUNNING")
        version = str(info.registered_model_version)
        for key, value in (version_tags or {}).items():
            self.client.set_model_version_tag(registered_name, version, key, value)
        return RegisteredVersion(registered_name, version, self.run_id)

    def finish(self, status: str = "FINISHED") -> None:
        self.client.set_terminated(self.run_id, status=status)


def load_model(uri: str) -> Pipeline:
    return mlflow.sklearn.load_model(uri)


def reload_delta(version: RegisteredVersion, model: Pipeline, sample: pd.DataFrame) -> float:
    """Écart maximal entre le modèle en mémoire et le même modèle rechargé du registre."""
    inputs = sample[list(INPUT_COLUMNS)]
    reloaded = load_model(version.uri)
    return float(np.abs(reloaded.predict(inputs) - model.predict(inputs)).max())
