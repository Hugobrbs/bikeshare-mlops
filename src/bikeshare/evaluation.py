import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error

from bikeshare.data.schema import INPUT_COLUMNS, TARGET
from bikeshare.features import build_features

RUSH_HOURS = (7, 8, 9, 17, 18, 19)


def regression_metrics(y_true: pd.Series, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(root_mean_squared_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
    }


def segment_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    features = build_features(frame)
    working = features["workingday"] == 1
    return {
        "rush_hours": working & features["hour"].isin(RUSH_HOURS),
        "night": features["hour"] < 6,
        "weekend_or_holiday": ~working,
    }


def evaluate(model: object, frame: pd.DataFrame) -> dict:
    y_true = frame[TARGET]
    y_pred = np.asarray(model.predict(frame[list(INPUT_COLUMNS)]), dtype=float)
    metrics = regression_metrics(y_true, y_pred)
    metrics["rows"] = len(frame)
    metrics["segments"] = {}
    for name, mask in segment_masks(frame).items():
        if mask.any():
            metrics["segments"][name] = {
                "mae": float(mean_absolute_error(y_true[mask], y_pred[mask.to_numpy()])),
                "rows": int(mask.sum()),
            }
    return metrics
