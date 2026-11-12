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


class ModelConfig(Section):
    max_iter: int = Field(default=300, ge=1)
    learning_rate: float = Field(default=0.08, gt=0)
    max_leaf_nodes: int = Field(default=31, ge=2)
    min_samples_leaf: int = Field(default=20, ge=1)
    l2_regularization: float = Field(default=0.0, ge=0)


class TrainingConfig(Section):
    seed: int = 42
    window_months: int = Field(default=12, ge=1)
    test_days: int = Field(default=7, ge=1)
    model: ModelConfig = ModelConfig()


class PipelineConfig(Section):
    dataset: DatasetConfig
    replay: ReplayConfig = ReplayConfig()
    validation: ValidationConfig = ValidationConfig()
    training: TrainingConfig = TrainingConfig()


def load_pipeline_config(path: Path) -> PipelineConfig:
    with path.open(encoding="utf-8") as f:
        return PipelineConfig.model_validate(yaml.safe_load(f) or {})
