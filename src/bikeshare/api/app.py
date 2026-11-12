import asyncio
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pandas as pd
import psutil
import psycopg
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from starlette.concurrency import run_in_threadpool

from bikeshare import __version__
from bikeshare.api.errors import ApiError, model_unavailable, storage_unavailable
from bikeshare.api.model_store import LoadedModel, ModelStore
from bikeshare.api.repository import FeedbackRow, PredictionRow, RequestTrace, TraceRepository
from bikeshare.api.schemas import (
    BatchPredictRequest,
    BatchPredictResponse,
    ErrorResponse,
    FeedbackRequest,
    FeedbackResponse,
    Health,
    ModelIdentity,
    ModelInfo,
    Observation,
    Prediction,
    PredictRequest,
    PredictResponse,
)
from bikeshare.data.schema import INPUT_COLUMNS
from bikeshare.features import FEATURE_VERSION
from bikeshare.logs import configure_logging
from bikeshare.settings import Settings, get_settings

logger = logging.getLogger(__name__)

TRACED_ENDPOINTS = {"/predict", "/predict/batch", "/feedback"}
STORAGE_ERRORS = (psycopg.Error, PoolTimeout)

DESCRIPTION = """
Prévision du nombre de locations de vélos pour une heure donnée, à partir de la date et de la météo.

Le modèle servi est celui que désigne l'alias `champion` du registre MLflow. Chaque réponse
indique la version exacte utilisée, et chaque prédiction peut recevoir un feedback.

Toutes les erreurs ont la même forme :
`{"request_id": "...", "error": {"code": "...", "message": "..."}}`.
"""

TAGS = [
    {"name": "Prédiction", "description": "Prédictions unitaires et par lot."},
    {"name": "Feedback", "description": "Retours sur une prédiction : avis ou valeur réelle."},
    {"name": "Modèle", "description": "Version servie et rechargement."},
    {"name": "Santé", "description": "Sondes de disponibilité."},
]

ERRORS = {
    422: {"model": ErrorResponse, "description": "Entrée invalide"},
    503: {"model": ErrorResponse, "description": "Modèle ou stockage indisponible"},
}


class Service:
    """Ce dont un worker a besoin pour répondre : config, base, modèle et son identité."""

    def __init__(self, settings: Settings, repository: TraceRepository, models: ModelStore) -> None:
        self.settings = settings
        self.repository = repository
        self.models = models
        self.instance_id = uuid4()
        self.pid = os.getpid()

    def trace(
        self,
        request: Request,
        status: int,
        *,
        item_count: int | None = None,
        inference_ms: float | None = None,
        error_code: str | None = None,
    ) -> RequestTrace:
        state = request.state
        return RequestTrace(
            request_id=state.request_id,
            started_at=state.started_at,
            endpoint=request.url.path,
            http_status=status,
            processing_duration_ms=(time.perf_counter() - state.t0) * 1000,
            worker_pid=self.pid,
            instance_id=self.instance_id,
            api_revision=self.settings.app_revision,
            campaign_id=state.campaign_id,
            item_count=item_count,
            inference_duration_ms=inference_ms,
            error_code=error_code,
        )


def get_service(request: Request) -> Service:
    return request.app.state.service


def identity(model: LoadedModel) -> ModelIdentity:
    return ModelIdentity(
        name=model.name, version=model.version, run_id=model.run_id, alias=model.alias
    )


def predict_and_trace(
    service: Service, request: Request, observations: list[Observation]
) -> tuple[LoadedModel, list[Prediction]]:
    model = service.models.current
    if model is None:
        raise model_unavailable()

    frame = pd.DataFrame([o.model_dump() for o in observations], columns=list(INPUT_COLUMNS))
    start = time.perf_counter()
    values = model.pipeline.predict(frame)
    inference_ms = (time.perf_counter() - start) * 1000

    rows = [
        PredictionRow(uuid4(), index, observation.model_dump(mode="json"), round(float(value), 2))
        for index, (observation, value) in enumerate(zip(observations, values, strict=True))
    ]
    trace = service.trace(request, 200, item_count=len(rows), inference_ms=inference_ms)
    try:
        # on ne répond 200 qu'une fois la requête et toutes ses prédictions écrites
        service.repository.save_predictions(trace, model.model_id, rows)
    except STORAGE_ERRORS as e:
        logger.error("prédictions non enregistrées, request_id=%s", trace.request_id)
        raise storage_unavailable() from e

    predictions = [
        Prediction(
            prediction_id=row.prediction_id,
            item_index=row.item_index,
            predicted_rentals=row.predicted_rentals,
        )
        for row in rows
    ]
    return model, predictions


async def keep_model_fresh(service: Service) -> None:
    """Relit l'alias à intervalle régulier, plus souvent tant qu'aucun modèle n'est chargé."""
    refresh = service.settings.model_refresh_seconds
    while True:
        await asyncio.sleep(refresh if service.models.current else min(refresh, 5))
        try:
            await run_in_threadpool(service.models.refresh)
        except Exception as e:
            logger.warning("alias non relu, le modèle en mémoire est conservé : %s", e)


async def send_heartbeats(service: Service) -> None:
    process = psutil.Process()
    process.cpu_percent()
    while True:
        await asyncio.sleep(service.settings.heartbeat_seconds)
        model = service.models.current
        try:
            await run_in_threadpool(
                service.repository.save_heartbeat,
                service.instance_id,
                service.pid,
                process.cpu_percent(),
                process.memory_info().rss / 1e6,
                model.version if model else None,
            )
        except STORAGE_ERRORS:
            logger.debug("heartbeat non enregistré")


def create_app(
    settings: Settings | None = None,
    repository: TraceRepository | None = None,
    models: ModelStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    # chaque worker uvicorn est un processus à part : il configure ses propres logs
    configure_logging(settings.log_level, settings.log_format)
    if repository is None:
        if settings.api_database_url is None:
            raise RuntimeError("API_DATABASE_URL n'est pas défini")
        repository = TraceRepository(
            settings.api_database_url.get_secret_value(),
            settings.api_db_pool_min,
            settings.api_db_pool_max,
        )
    if models is None:
        models = ModelStore(
            settings.mlflow_tracking_uri,
            settings.model_name,
            settings.model_alias,
            repository.register_model,
        )
    service = Service(settings, repository, models)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service.repository.open()
        try:
            await run_in_threadpool(service.models.refresh)
        except Exception as e:
            logger.warning("pas de modèle au démarrage, nouvel essai en arrière-plan : %s", e)
        tasks = [
            asyncio.create_task(keep_model_fresh(service)),
            asyncio.create_task(send_heartbeats(service)),
        ]
        yield
        for task in tasks:
            task.cancel()
        service.repository.close()

    app = FastAPI(
        title="Bikeshare Demand API",
        description=DESCRIPTION,
        version=__version__,
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    app.state.service = service

    @app.middleware("http")
    async def identify_request(request: Request, call_next: Callable) -> Response:
        request.state.request_id = uuid4()
        request.state.started_at = datetime.now(UTC)
        request.state.t0 = time.perf_counter()
        campaign = request.headers.get("x-test-campaign", "").strip()[:128]
        request.state.campaign_id = campaign or "manual"
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        return response

    async def error_response(
        request: Request, status: int, code: str, message: str
    ) -> JSONResponse:
        request_id: UUID = getattr(request.state, "request_id", None) or uuid4()
        should_trace = request.url.path in TRACED_ENDPOINTS and hasattr(request.state, "t0")
        # si c'est la base qui est en panne, inutile d'attendre une seconde fois une connexion
        lost = should_trace and code == "storage_unavailable"
        if should_trace and not lost:
            trace = service.trace(request, status, error_code=code)
            try:
                await run_in_threadpool(service.repository.save_request, trace)
            except STORAGE_ERRORS:
                lost = True
        if lost:
            # il ne reste que ce log, avec l'identifiant rendu au client
            logger.error(
                "erreur non tracée en base, request_id=%s statut=%s code=%s",
                request_id,
                status,
                code,
            )
        return JSONResponse(
            status_code=status,
            content={"request_id": str(request_id), "error": {"code": code, "message": message}},
            headers={"X-Request-ID": str(request_id)},
        )

    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, error: ApiError) -> JSONResponse:
        return await error_response(request, error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def on_invalid_input(request: Request, error: RequestValidationError) -> JSONResponse:
        problems = [
            f"{'.'.join(str(part) for part in e['loc'][1:])} : {e['msg']}" for e in error.errors()
        ]
        return await error_response(request, 422, "invalid_input", " ; ".join(problems[:5]))

    @app.exception_handler(Exception)
    async def on_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception("erreur inattendue sur %s", request.url.path)
        return await error_response(request, 500, "internal_error", "Erreur interne.")

    @app.post("/predict", response_model=PredictResponse, responses=ERRORS, tags=["Prédiction"])
    def predict(
        payload: PredictRequest, request: Request, service: Service = Depends(get_service)
    ) -> PredictResponse:
        """Prévoit le nombre de locations pour une heure."""
        model, predictions = predict_and_trace(service, request, [payload.observation])
        return PredictResponse(
            request_id=request.state.request_id, model=identity(model), prediction=predictions[0]
        )

    @app.post(
        "/predict/batch", response_model=BatchPredictResponse, responses=ERRORS, tags=["Prédiction"]
    )
    def predict_batch(
        payload: BatchPredictRequest, request: Request, service: Service = Depends(get_service)
    ) -> BatchPredictResponse:
        """Prévoit de 1 à 100 heures en une requête.

        Tout ou rien : une seule observation invalide rejette le lot entier.
        """
        model, predictions = predict_and_trace(service, request, payload.observations)
        return BatchPredictResponse(
            request_id=request.state.request_id, model=identity(model), predictions=predictions
        )

    @app.post(
        "/feedback",
        response_model=FeedbackResponse,
        status_code=201,
        tags=["Feedback"],
        responses={
            200: {"model": FeedbackResponse, "description": "Feedback déjà enregistré (rejeu)"},
            404: {"model": ErrorResponse, "description": "Prédiction inconnue"},
            409: {"model": ErrorResponse, "description": "Feedback différent déjà enregistré"},
            **ERRORS,
        },
    )
    def feedback(
        payload: FeedbackRequest, request: Request, service: Service = Depends(get_service)
    ) -> JSONResponse:
        """Enregistre un avis ou la valeur réellement observée pour une prédiction.

        Renvoyer exactement le même feedback est sans effet (200).
        """
        trace = service.trace(request, 201)
        row = FeedbackRow(**payload.model_dump())
        try:
            status = service.repository.save_feedback(trace, row)
        except STORAGE_ERRORS as e:
            raise storage_unavailable() from e
        body = FeedbackResponse(
            request_id=trace.request_id, feedback_id=payload.feedback_id, status=status
        )
        return JSONResponse(status_code=trace.http_status, content=body.model_dump(mode="json"))

    @app.get("/model", response_model=ModelInfo, responses={503: ERRORS[503]}, tags=["Modèle"])
    def served_model(service: Service = Depends(get_service)) -> ModelInfo:
        """Version actuellement servie par ce worker."""
        model = service.models.current
        if model is None:
            raise model_unavailable()
        return ModelInfo(
            **identity(model).model_dump(),
            loaded_at=model.loaded_at,
            feature_version=FEATURE_VERSION,
        )

    @app.post(
        "/admin/model/refresh",
        response_model=ModelInfo,
        tags=["Modèle"],
        responses={
            401: {"model": ErrorResponse, "description": "Jeton absent ou invalide"},
            503: ERRORS[503],
        },
    )
    def refresh_model(
        x_admin_token: str | None = Header(default=None),
        service: Service = Depends(get_service),
    ) -> ModelInfo:
        """Relit l'alias tout de suite, sans attendre le prochain rafraîchissement.

        N'agit que sur le worker qui reçoit l'appel ; les autres suivent au plus
        tard après `MODEL_REFRESH_SECONDS`.
        """
        expected = service.settings.admin_token
        if expected is None or not secrets.compare_digest(
            x_admin_token or "", expected.get_secret_value()
        ):
            raise ApiError(401, "unauthorized", "Jeton d'administration absent ou invalide.")
        try:
            service.models.refresh()
        except Exception as e:
            raise ApiError(503, "registry_unavailable", "Registre de modèles injoignable.") from e
        return served_model(service)

    @app.get("/health/live", response_model=Health, tags=["Santé"])
    def live() -> Health:
        """Le processus répond."""
        return Health(status="ok")

    @app.get(
        "/health/ready",
        response_model=Health,
        responses={503: {"model": Health, "description": "Pas prêt à servir"}},
        tags=["Santé"],
    )
    def ready(service: Service = Depends(get_service)) -> JSONResponse:
        """Prêt seulement si un modèle est chargé et que PostgreSQL répond."""
        health = Health(
            status="ok",
            model_loaded=service.models.current is not None,
            database=service.repository.ping(),
            registry_reachable=service.models.last_error is None,
            detail=service.models.last_error,
        )
        if not (health.model_loaded and health.database):
            health.status = "unavailable"
        return JSONResponse(
            status_code=200 if health.status == "ok" else 503,
            content=health.model_dump(mode="json"),
        )

    return app
