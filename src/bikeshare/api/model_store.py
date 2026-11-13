import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import mlflow
from mlflow.tracking import MlflowClient
from sklearn.pipeline import Pipeline

from bikeshare.features import FEATURE_VERSION

logger = logging.getLogger(__name__)

RegisterModel = Callable[[str, str, int, str], UUID]


class IncompatibleModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedModel:
    model_id: UUID
    name: str
    version: int
    run_id: str
    alias: str
    pipeline: Pipeline
    loaded_at: datetime


class ModelStore:
    """Garde en mémoire le modèle désigné par l'alias et suit ses déplacements.

    L'alias est relu régulièrement : une promotion ou un retour arrière dans MLflow
    change le modèle servi sans redéployer l'API. Si MLflow ne répond plus, le
    modèle déjà chargé continue de servir.
    """

    def __init__(self, tracking_uri: str, name: str, alias: str, register: RegisterModel) -> None:
        self.tracking_uri = tracking_uri
        self.name = name
        self.alias = alias
        self.register = register
        self.client = MlflowClient(tracking_uri)
        self.current: LoadedModel | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def refresh(self) -> bool:
        """Recharge si l'alias pointe sur une autre version. Retourne True si le modèle a changé."""
        with self._lock:
            try:
                changed = self._refresh()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                raise
            self.last_error = None
            return changed

    def _refresh(self) -> bool:
        target = self.client.get_model_version_by_alias(self.name, self.alias)
        version = int(target.version)
        if self.current is not None and self.current.version == version:
            return False

        trained_with = target.tags.get("feature_version")
        if trained_with != FEATURE_VERSION:
            raise IncompatibleModelError(
                f"version {version} entraînée avec les features v{trained_with}, "
                f"l'API calcule les features v{FEATURE_VERSION}"
            )

        mlflow.set_tracking_uri(self.tracking_uri)
        pipeline = mlflow.sklearn.load_model(f"models:/{self.name}/{version}")
        model_id = self.register(self.tracking_uri, self.name, version, target.run_id)
        previous = self.current.version if self.current else None
        # l'ancien modèle sert jusqu'à cette affectation : pas de trou de service
        self.current = LoadedModel(
            model_id=model_id,
            name=self.name,
            version=version,
            run_id=target.run_id,
            alias=self.alias,
            pipeline=pipeline,
            loaded_at=datetime.now(UTC),
        )
        logger.info("modèle %s v%s chargé (précédent : %s)", self.name, version, previous)
        return True
