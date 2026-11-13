from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
    api_database_url: SecretStr | None = None
    ops_database_url: SecretStr | None = None
    api_db_password: SecretStr | None = None
    ops_db_password: SecretStr | None = None
    grafana_db_password: SecretStr | None = None

    api_db_pool_min: int = Field(default=1, ge=1)
    api_db_pool_max: int = Field(default=5, ge=1)
    api_host: str = "127.0.0.1"
    api_port: int = 8001
    api_workers: int = Field(default=1, ge=1)
    model_refresh_seconds: float = Field(default=30, gt=0)
    heartbeat_seconds: float = Field(default=15, gt=0)
    admin_token: SecretStr | None = None

    pipeline_schedule_seconds: int = Field(default=600, ge=30)
    alert_sink_url: str = "http://localhost:8002/alerts"
    alert_sink_port: int = 8002
    alert_forward_url: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
