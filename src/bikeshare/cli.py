import json

import typer

from bikeshare import __version__
from bikeshare.logs import configure_logging
from bikeshare.settings import get_settings

app = typer.Typer(
    help="Prévision de la demande de vélos en libre-service.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


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
