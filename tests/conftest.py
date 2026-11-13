import logging
from collections.abc import Iterator

import pytest

from bikeshare.settings import get_settings

ENV_VARS = (
    "APP_REVISION",
    "LOG_FORMAT",
    "LOG_LEVEL",
    "DATA_DIR",
    "PIPELINE_CONFIG",
    "MLFLOW_TRACKING_URI",
    "MODEL_NAME",
    "MODEL_ALIAS",
)


@pytest.fixture(autouse=True)
def clean_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # pas de .env du poste de dev dans les tests
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    get_settings.cache_clear()

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:], root.level = handlers, level
    get_settings.cache_clear()
