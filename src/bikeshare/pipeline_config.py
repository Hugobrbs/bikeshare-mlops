from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetConfig(Section):
    name: str
    member: str
    member_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    urls: list[str] = Field(min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)


class ReplayConfig(Section):
    bootstrap_batches: int = Field(default=6, ge=1)


class ValidationConfig(Section):
    min_hour_coverage: float = Field(default=0.90, gt=0, le=1)
    max_missing_days: int = Field(default=1, ge=0)
    max_rentals: int = Field(default=5000, gt=0)
    mean_rentals_min: float = Field(default=5, ge=0)
    mean_rentals_max: float = Field(default=1500, gt=0)
    min_temperature_std: float = Field(default=0.02, ge=0)


class PipelineConfig(Section):
    dataset: DatasetConfig
    replay: ReplayConfig = ReplayConfig()
    validation: ValidationConfig = ValidationConfig()


def load_pipeline_config(path: Path) -> PipelineConfig:
    with path.open(encoding="utf-8") as f:
        return PipelineConfig.model_validate(yaml.safe_load(f) or {})
