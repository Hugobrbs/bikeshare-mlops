from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration lue depuis l'environnement (ou un fichier .env local)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    app_revision: str = "dev"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    data_dir: Path = Path("data")
    pipeline_config: Path = Path("config/pipeline.yaml")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
