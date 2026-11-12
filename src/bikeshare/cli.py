import json

import typer

from bikeshare import __version__
from bikeshare.data.acquisition import AcquisitionError, IntegrityError, fetch_source
from bikeshare.data.replay import Corruption, publish_batches
from bikeshare.data.store import BatchStore
from bikeshare.logs import configure_logging
from bikeshare.pipeline_config import load_pipeline_config
from bikeshare.settings import get_settings

app = typer.Typer(
    help="Prévision de la demande de vélos en libre-service.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
data_app = typer.Typer(help="Téléchargement du dataset et rejeu par lots.", no_args_is_help=True)
registry_app = typer.Typer(help="Registre de modèles (MLflow).", no_args_is_help=True)
db_app = typer.Typer(help="Base PostgreSQL.", no_args_is_help=True)
app.add_typer(data_app, name="data")
app.add_typer(registry_app, name="registry")
app.add_typer(db_app, name="db")


@app.callback()
def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def version() -> None:
    """Affiche la version du paquet et la révision du code."""
    typer.echo(f"bikeshare {__version__} ({get_settings().app_revision})")


@app.command("show-config")
def show_config() -> None:
    """Affiche la configuration effective (secrets masqués)."""
    typer.echo(json.dumps(get_settings().model_dump(mode="json"), indent=2, ensure_ascii=False))


@data_app.command("download")
def data_download() -> None:
    """Télécharge le dataset UCI et vérifie son empreinte."""
    settings = get_settings()
    config = load_pipeline_config(settings.pipeline_config)
    try:
        snapshot = fetch_source(config.dataset, BatchStore(settings.data_dir).source_dir)
    except (AcquisitionError, IntegrityError) as e:
        typer.echo(f"échec : {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"{snapshot.path} sha256={snapshot.sha256[:12]} origine={snapshot.origin}")


@data_app.command("publish")
def data_publish(
    count: int = typer.Option(1, min=1, help="Nombre de mois à déposer."),
    corrupt: Corruption | None = typer.Option(None, help="Défaut à injecter dans le lot."),
) -> None:
    """Dépose les prochains lots mensuels dans data/incoming."""
    settings = get_settings()
    config = load_pipeline_config(settings.pipeline_config)
    store = BatchStore(settings.data_dir)
    source_file = store.source_dir / config.dataset.member
    if not source_file.is_file():
        typer.echo("dataset absent, lancer d'abord : bikeshare data download", err=True)
        raise typer.Exit(1)

    published = publish_batches(
        source_file,
        store.incoming_dir,
        delivered=store.delivered_ids(),
        count=count,
        corruption=corrupt,
    )
    if not published:
        typer.echo("plus aucun lot à livrer")
    for batch in published:
        typer.echo(f"{batch.batch_id} : {batch.rows} lignes -> {batch.path}")


@data_app.command("ingest")
def data_ingest() -> None:
    """Contrôle les lots en attente : acceptés, ou mis en quarantaine au premier défaut."""
    # import tardif : great_expectations est lent à charger et absent de l'image de l'API
    from bikeshare.data.ingestion import ingest_pending
    from bikeshare.data.validation import DataValidationError

    settings = get_settings()
    config = load_pipeline_config(settings.pipeline_config)
    try:
        accepted = ingest_pending(BatchStore(settings.data_dir), config.validation)
    except DataValidationError as e:
        typer.echo(f"échec : {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"lots acceptés : {[r.batch_id for r in accepted]}")


@data_app.command("status")
def data_status() -> None:
    """Liste les lots acceptés, en attente et en quarantaine."""
    store = BatchStore(get_settings().data_dir)
    status = {
        "accepted": [r.batch_id for r in store.accepted()],
        "pending": [p.stem for p in store.pending()],
        "quarantined": sorted(p.name for p in store.quarantine_dir.glob("*.csv")),
    }
    typer.echo(json.dumps(status, indent=2))


def make_registry():
    from mlflow.tracking import MlflowClient

    from bikeshare.registry.models import ModelRegistry

    settings = get_settings()
    client = MlflowClient(settings.mlflow_tracking_uri)
    return ModelRegistry(client, settings.model_name, settings.model_alias)


@registry_app.command("status")
def registry_status() -> None:
    """Liste les versions du modèle, leur statut et leurs alias."""
    registry = make_registry()
    versions = registry.client.search_model_versions(f"name='{registry.name}'")
    rows = [
        {
            "version": int(v.version),
            "status": v.tags.get("validation_status"),
            "aliases": list(registry.client.get_model_version(registry.name, v.version).aliases),
            "promoted_at": v.tags.get("promoted_at"),
            "reasons": v.tags.get("validation_reasons"),
        }
        for v in sorted(versions, key=lambda v: int(v.version))
    ]
    typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))


@registry_app.command("promote")
def registry_promote(
    version: str,
    reason: str = typer.Option(..., help="Pourquoi cette version doit être servie."),
    actor: str = typer.Option("manuel"),
) -> None:
    """Fait d'une version approuvée le nouveau champion."""
    from bikeshare.registry.models import PromotionRefusedError

    try:
        change = make_registry().promote(version, actor=actor, reason=reason)
    except PromotionRefusedError as e:
        typer.echo(f"refusé : {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"champion : v{change.from_version} -> v{change.to_version}")


@registry_app.command("rollback")
def registry_rollback(
    reason: str = typer.Option(..., help="Pourquoi on revient en arrière."),
    to_version: str | None = typer.Option(None, help="Par défaut : le champion précédent."),
    actor: str = typer.Option("manuel"),
) -> None:
    """Remet en service le champion précédent (ou une version approuvée précise)."""
    from bikeshare.registry.models import PromotionRefusedError

    try:
        change = make_registry().rollback(actor=actor, reason=reason, to_version=to_version)
    except PromotionRefusedError as e:
        typer.echo(f"refusé : {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"champion : v{change.from_version} -> v{change.to_version}")


@db_app.command("init")
def db_init() -> None:
    """Crée les rôles, les schémas et les tables (rejouable)."""
    from bikeshare.db.migrate import init_database

    settings = get_settings()
    secrets = {
        "ADMIN_DATABASE_URL": settings.admin_database_url,
        "API_DB_PASSWORD": settings.api_db_password,
        "OPS_DB_PASSWORD": settings.ops_db_password,
        "GRAFANA_DB_PASSWORD": settings.grafana_db_password,
    }
    missing = [name for name, value in secrets.items() if value is None]
    if missing:
        typer.echo(f"variables manquantes : {', '.join(missing)}", err=True)
        raise typer.Exit(2)
    init_database(
        settings.admin_database_url.get_secret_value(),
        {
            "api_writer": settings.api_db_password.get_secret_value(),
            "pipeline_writer": settings.ops_db_password.get_secret_value(),
            "grafana_reader": settings.grafana_db_password.get_secret_value(),
        },
    )
    typer.echo("base initialisée")
