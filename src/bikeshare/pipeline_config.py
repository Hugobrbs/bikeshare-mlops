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


class PipelineConfig(Section):
    dataset: DatasetConfig
    replay: ReplayConfig = ReplayConfig()


def load_pipeline_config(path: Path) -> PipelineConfig:
    with path.open(encoding="utf-8") as f:
        return PipelineConfig.model_validate(yaml.safe_load(f) or {})
