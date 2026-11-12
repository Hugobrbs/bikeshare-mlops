import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure
from sklearn.inspection import permutation_importance
from sklearn.pipeline import Pipeline

from bikeshare.data.schema import INPUT_COLUMNS, TARGET, TIMESTAMP


def input_importance(model: Pipeline, test: pd.DataFrame, seed: int) -> dict[str, float]:
    """Hausse de MAE quand on mélange une colonne d'entrée : plus c'est haut, plus elle compte."""
    inputs = test[list(INPUT_COLUMNS)]
    result = permutation_importance(
        model,
        inputs,
        test[TARGET],
        scoring="neg_mean_absolute_error",
        n_repeats=3,
        random_state=seed,
    )
    scores = dict(zip(inputs.columns, result.importances_mean, strict=True))
    return {name: float(scores[name]) for name in sorted(scores, key=scores.get, reverse=True)}


def importance_figure(importance: dict[str, float]) -> Figure:
    figure, axis = plt.subplots(figsize=(7, 3.5))
    names = list(importance)[::-1]
    axis.barh(names, [importance[n] for n in names])
    axis.set_xlabel("hausse de la MAE si la variable est mélangée")
    figure.tight_layout()
    return figure


def forecast_figure(model: Pipeline, test: pd.DataFrame) -> Figure:
    figure, axis = plt.subplots(figsize=(10, 3.5))
    axis.plot(test[TIMESTAMP], test[TARGET], label="réel")
    axis.plot(test[TIMESTAMP], model.predict(test[list(INPUT_COLUMNS)]), label="prédit")
    axis.set_ylabel("locations par heure")
    axis.legend()
    figure.autofmt_xdate()
    figure.tight_layout()
    return figure
