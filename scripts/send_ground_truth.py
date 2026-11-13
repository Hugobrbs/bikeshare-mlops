import argparse
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd

from bikeshare.data.replay import month_of
from bikeshare.data.schema import INPUT_COLUMNS, TARGET
from bikeshare.data.standardize import to_domain


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="mois à rejouer, AAAA-MM")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--source", type=Path, default=Path("data/source/hour.csv"))
    parser.add_argument("--api", default="http://localhost:8001")
    args = parser.parse_args()

    source = pd.read_csv(args.source)
    hours = to_domain(source[month_of(source["dteday"]) == args.month])
    sample = hours.sample(min(args.count, len(hours)), random_state=0)

    errors = []
    with httpx.Client(base_url=args.api, headers={"X-Test-Campaign": "terrain"}) as client:
        for row in sample.to_dict("records"):
            observation = {name: row[name] for name in INPUT_COLUMNS}
            observation["timestamp"] = observation["timestamp"].isoformat()
            prediction = client.post("/predict", json={"observation": observation}).json()
            predicted = prediction["prediction"]["predicted_rentals"]
            feedback = {
                "feedback_id": str(uuid.uuid4()),
                "prediction_id": prediction["prediction"]["prediction_id"],
                "observed_rentals": int(row[TARGET]),
                "observed_at": datetime.now(UTC).isoformat(),
                "label_source": "uci-replay",
            }
            client.post("/feedback", json=feedback).raise_for_status()
            errors.append(abs(predicted - row[TARGET]))

    version = prediction["model"]["version"]
    print(
        f"{len(errors)} feedbacks envoyés, modèle v{version}, MAE {sum(errors) / len(errors):.1f}"
    )


if __name__ == "__main__":
    main()
