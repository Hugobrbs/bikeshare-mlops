import logging

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from bikeshare.db.ops import OpsStore
from bikeshare.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class Alert(BaseModel):
    subject: str
    body: str


def forward(url: str, alert: Alert, transport: httpx.BaseTransport | None = None) -> bool:
    try:
        with httpx.Client(timeout=10, transport=transport) as client:
            response = client.post(url, json={"text": f"{alert.subject}\n{alert.body}"})
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.warning("alerte non relayée : HTTP %s", e.response.status_code)
        return False
    except httpx.HTTPError as e:
        logger.warning("alerte non relayée : %s", type(e).__name__)
        return False
    return True


def create_app(
    settings: Settings | None = None,
    ops: OpsStore | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if ops is None:
        url = settings.ops_database_url
        ops = OpsStore(url.get_secret_value() if url else None)
    app = FastAPI(title="Bikeshare alerts", docs_url=None, redoc_url=None)

    @app.post("/alerts", status_code=202)
    def receive(alert: Alert) -> dict:
        logger.error("ALERTE %s | %s", alert.subject, alert.body)
        forwarded = False
        if settings.alert_forward_url:
            forwarded = forward(settings.alert_forward_url.get_secret_value(), alert, transport)
        ops.alert_received(alert.subject, alert.body, forwarded)
        return {"forwarded": forwarded}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
