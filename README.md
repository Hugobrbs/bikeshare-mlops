# bikeshare-mlops

Chaîne MLOps complète pour prévoir, heure par heure, le nombre de locations d'un
service de vélos en libre-service (jeu de données
[UCI Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset)).

Projet final du module **5MLDE - Déploiement de modèles** (SUPINFO).

> Dépôt en cours de construction : la documentation complète (architecture,
> installation, commandes de reproduction) arrive avec les briques du projet.

## Périmètre visé

| Brique | Outil |
|---|---|
| Ingestion et contrôles qualité bloquants | Python, Great Expectations |
| Orchestration, reprises et alertes | Prefect |
| Suivi des expériences et registre de modèles | MLflow |
| API de prédiction et collecte de feedbacks | FastAPI, PostgreSQL |
| Tests de charge et dimensionnement | Locust |
| Supervision | Grafana |

## Licence

Code sous licence [MIT](LICENSE). Le jeu de données n'est pas redistribué dans ce
dépôt : il est téléchargé depuis l'UCI Machine Learning Repository (licence
CC BY 4.0, Fanaee-T & Gama, 2013).
