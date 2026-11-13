from bikeshare.pipeline.flow import training_pipeline
from bikeshare.settings import get_settings

DEPLOYMENT_NAME = "replay-mensuel"


def serve() -> None:
    settings = get_settings()
    training_pipeline.serve(
        name=DEPLOYMENT_NAME,
        interval=settings.pipeline_schedule_seconds,
        limit=1,
        version=settings.app_revision,
        description="Rejeu mensuel du dataset : ingestion, contrôles, entraînement, promotion.",
        tags=["bikeshare", settings.environment],
    )
