"""Scénarios de charge de l'API de prédiction.

    locust -f loadtests/locustfile.py OnlineUser --host http://localhost:8001

Chaque réponse est vérifiée : un 200 dont le corps est incohérent compte comme un échec.
"""

import os
import random
import uuid
from datetime import UTC, datetime, timedelta

from locust import HttpUser, between, task

CAMPAIGN = os.environ.get("CAMPAIGN_ID", "locust")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "20"))


def build_observations(count: int = 500, seed: int = 42) -> list[dict]:
    """Observations valides générées une fois pour toutes : mêmes entrées à chaque tir."""
    rng = random.Random(seed)
    start = datetime(2012, 6, 1)
    observations = []
    for _ in range(count):
        temperature = round(rng.uniform(2, 35), 1)
        observations.append(
            {
                "timestamp": (start + timedelta(hours=rng.randrange(24 * 90))).isoformat(),
                "holiday": rng.random() < 0.03,
                "weather_code": rng.choices([1, 2, 3], weights=[65, 27, 8])[0],
                "temperature_c": temperature,
                "feels_like_c": round(temperature + rng.uniform(-2, 4), 1),
                "humidity_pct": round(rng.uniform(25, 100), 1),
                "windspeed_kmh": round(rng.uniform(0, 45), 1),
            }
        )
    return observations


OBSERVATIONS = build_observations()


class ApiUser(HttpUser):
    abstract = True
    # temps de réflexion court : chaque utilisateur envoie environ 10 requêtes par seconde
    wait_time = between(0.05, 0.15)

    def on_start(self) -> None:
        self.client.headers["X-Test-Campaign"] = CAMPAIGN
        self.unrated: list[str] = []

    def predict_one(self) -> None:
        payload = {"observation": random.choice(OBSERVATIONS)}
        with self.client.post("/predict", json=payload, catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            body = response.json()
            prediction = body.get("prediction", {})
            if prediction.get("predicted_rentals", -1) < 0 or "version" not in body.get(
                "model", {}
            ):
                response.failure("réponse incohérente")
                return
            self.unrated.append(prediction["prediction_id"])

    def predict_batch(self) -> None:
        payload = {"observations": random.sample(OBSERVATIONS, BATCH_SIZE)}
        with self.client.post("/predict/batch", json=payload, catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            predictions = response.json().get("predictions", [])
            if [p["item_index"] for p in predictions] != list(range(BATCH_SIZE)):
                response.failure("lot incomplet ou dans le désordre")

    def send_feedback(self) -> None:
        if not self.unrated:
            return
        payload = {
            "feedback_id": str(uuid.uuid4()),
            # une prédiction ne reçoit qu'un feedback : on la retire de la liste
            "prediction_id": self.unrated.pop(),
            "observed_rentals": random.randrange(0, 600),
            "observed_at": datetime.now(UTC).isoformat(),
            # exclu des indicateurs de qualité du dashboard
            "label_source": "loadtest",
        }
        with self.client.post("/feedback", json=payload, catch_response=True) as response:
            if response.status_code != 201:
                response.failure(f"HTTP {response.status_code}")


class OnlineUser(ApiUser):
    """Prédictions unitaires uniquement."""

    @task
    def predict(self) -> None:
        self.predict_one()


class BatchUser(ApiUser):
    """Lots de BATCH_SIZE observations."""

    @task
    def batch(self) -> None:
        self.predict_batch()


class MixedUser(ApiUser):
    """Trafic réaliste : surtout de l'unitaire, quelques lots, quelques feedbacks."""

    @task(8)
    def predict(self) -> None:
        self.predict_one()

    @task(1)
    def batch(self) -> None:
        self.predict_batch()

    @task(1)
    def feedback(self) -> None:
        self.send_feedback()
