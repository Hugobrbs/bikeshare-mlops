from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration lue depuis l'environnement (ou un fichier .env local)."""

    # protected_namespaces : sinon pydantic râle sur les champs model_name / model_alias
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    environment: str = "local"
    app_revision: str = "dev"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    data_dir: Path = Path("data")
    pipeline_config: Path = Path("config/pipeline.yaml")
    promotion_policy: Path = Path("config/promotion-policy.yaml")

    mlflow_tracking_uri: str = "http://localhost:7281"
    mlflow_experiment_name: str = "bikeshare-demand"
    model_name: str = "bikeshare-demand"
    model_alias: str = "champion"

    admin_database_url: SecretStr | None = None
    ops_database_url: SecretStr | None = None
    api_db_password: SecretStr | None = None
    ops_db_password: SecretStr | None = None
    grafana_db_password: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
