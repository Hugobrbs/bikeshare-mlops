from pathlib import Path

import pytest
from pydantic import ValidationError

from bikeshare.pipeline_config import load_pipeline_config

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID = """
dataset:
  name: demo
  member: hour.csv
  member_sha256: {sha}
  urls: [https://example.org/data.zip]
"""


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "pipeline.yaml"
    path.write_text(content)
    return path


def test_repository_config_is_valid() -> None:
    config = load_pipeline_config(REPO_ROOT / "config" / "pipeline.yaml")

    assert config.dataset.member == "hour.csv"
    assert len(config.dataset.urls) >= 2


def test_defaults_are_applied(tmp_path: Path) -> None:
    config = load_pipeline_config(write(tmp_path, VALID.format(sha="a" * 64)))

    assert config.dataset.timeout_seconds == 30
    assert config.replay.bootstrap_batches == 6


def test_bad_checksum_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="member_sha256"):
        load_pipeline_config(write(tmp_path, VALID.format(sha="pas-un-sha")))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    content = VALID.format(sha="a" * 64) + "trainning:\n  seed: 1\n"

    with pytest.raises(ValidationError, match="trainning"):
        load_pipeline_config(write(tmp_path, content))
