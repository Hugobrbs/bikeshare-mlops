from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NaiveDatetime, model_validator

MAX_BATCH_SIZE = 100

OBSERVATION_EXAMPLE = {
    "timestamp": "2012-06-15T08:00:00",
    "holiday": False,
    "weather_code": 1,
    "temperature_c": 22.5,
    "feels_like_c": 25.0,
    "humidity_pct": 60,
    "windspeed_kmh": 12.0,
}


class Observation(BaseModel):
    """Conditions connues pour l'heure à prévoir."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": OBSERVATION_EXAMPLE})

    timestamp: NaiveDatetime = Field(description="Heure locale de la station, sans fuseau horaire.")
    holiday: bool = Field(default=False, description="Jour férié.")
    weather_code: int = Field(
        ge=1,
        le=4,
        description="1 dégagé, 2 brume, 3 pluie ou neige légère, 4 fortes intempéries.",
    )
    temperature_c: float = Field(ge=-40, le=55, description="Température en °C.")
    feels_like_c: float = Field(ge=-60, le=70, description="Température ressentie en °C.")
    humidity_pct: float = Field(ge=0, le=100, description="Humidité relative en %.")
    windspeed_kmh: float = Field(ge=0, le=200, description="Vitesse du vent en km/h.")


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation: Observation


class BatchPredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[Observation] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


class ModelIdentity(BaseModel):
    """Version exacte qui a produit la prédiction (l'alias seul ne suffit pas, il peut bouger)."""

    name: str
    version: int
    run_id: str
    alias: str


class Prediction(BaseModel):
    prediction_id: UUID
    item_index: int
    predicted_rentals: float = Field(description="Nombre de locations prévu sur l'heure.")


class PredictResponse(BaseModel):
    request_id: UUID
    model: ModelIdentity
    prediction: Prediction


class BatchPredictResponse(BaseModel):
    request_id: UUID
    model: ModelIdentity
    predictions: list[Prediction]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "feedback_id": "7b7a5f0e-6d0f-4a43-9a53-0d4f5d1c2a10",
                "prediction_id": "0c3e7f8a-2b1d-4e5f-8a9b-1c2d3e4f5a6b",
                "observed_rentals": 412,
                "observed_at": "2012-06-15T09:05:00Z",
                "label_source": "comptage-stations",
            }
        },
    )

    feedback_id: UUID = Field(
        description="Généré par le client et réutilisé en cas de nouvel essai."
    )
    prediction_id: UUID
    useful: bool | None = Field(default=None, description="Avis subjectif sur la prédiction.")
    observed_rentals: int | None = Field(
        default=None, ge=0, le=100_000, description="Nombre de locations réellement constaté."
    )
    observed_at: AwareDatetime | None = None
    label_source: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[^\x00]+$"
    )

    @model_validator(mode="after")
    def check_consistency(self) -> "FeedbackRequest":
        if self.useful is None and self.observed_rentals is None:
            raise ValueError("fournir useful, observed_rentals ou les deux")
        observation = (self.observed_rentals, self.observed_at, self.label_source)
        if any(v is not None for v in observation) and any(v is None for v in observation):
            raise ValueError("observed_rentals, observed_at et label_source vont ensemble")
        return self


class FeedbackResponse(BaseModel):
    request_id: UUID
    feedback_id: UUID
    status: Literal["created", "already_recorded"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    request_id: UUID
    error: ErrorDetail


class ModelInfo(ModelIdentity):
    loaded_at: datetime
    feature_version: str


class Health(BaseModel):
    status: Literal["ok", "unavailable"]
    model_loaded: bool = False
    database: bool = False
    registry_reachable: bool = False
    detail: str | None = None
