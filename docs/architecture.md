# Architecture

## Vue d'ensemble

Huit conteneurs démarrés par `docker compose up`, dont un d'initialisation qui s'arrête une fois la base prête :

| Conteneur | Image | Rôle |
|---|---|---|
| `postgres` | postgres 18.6 | base `bikeshare` (schémas `tracing` et `ops`) et base `prefect` |
| `db-init` | image de l'API | crée les rôles et les tables puis s'arrête |
| `mlflow` | mlflow 3.17.2 | suivi des runs, registre de modèles, stockage des artefacts |
| `prefect` | prefect 3.8.8 | serveur d'orchestration : états, logs, planning, automations |
| `pipeline` | image du pipeline | exécute le flow d'entraînement (direct au démarrage, puis planifié) |
| `alert-sink` | image de l'API | reçoit les alertes de Prefect, les stocke, les relaie si un webhook est configuré |
| `api` | image de l'API | prédictions et feedbacks |
| `grafana` | grafana 13.2.3 | dashboards sur PostgreSQL |

Deux images sont construites depuis le même `Dockerfile` :

- `api` : uniquement ce qu'il faut pour servir (FastAPI, scikit-learn, client MLflow, psycopg) ;
- `pipeline` : la même base, plus Prefect, Great Expectations et matplotlib.

Les deux installent les dépendances de `uv.lock` sans les résoudre à nouveau, et tournent avec un utilisateur non root.

## Du lot de données à la prédiction

```
lot 2011-07.csv
   |  validation (Great Expectations) : accepté ou quarantaine
   v
data/curated/2011-07.csv + manifest.json (sha256 du lot)
   |  fenêtre de 12 mois, test = 7 derniers jours
   v
run MLflow "train-2011-07"  (tags : flow_run_id, training_batches, data_fingerprint, git_commit)
   |  politique de promotion
   v
version N du modèle "bikeshare-demand"  (tags : validation_status, feature_version)
   |  alias champion
   v
API : chaque prédiction enregistrée avec le model_id de la version exacte
```

On peut donc remonter d'une prédiction en base jusqu'aux lots qui ont entraîné son modèle :

`tracing.predictions.model_id` -> `tracing.model_versions` (nom, version, `mlflow_run_id`) -> run MLflow (tags `training_batches`, `flow_run_id`, `data_fingerprint`) -> flow run Prefect et `ops.pipeline_runs`.

Le tag `training_batches` liste tous les lots de la fenêtre d'entraînement. `batch_ids` ne contient que les lots arrivés pendant ce run.

## Qui trace quoi

| Outil | Ce qu'on y trouve |
|---|---|
| Prefect | état de chaque exécution et de chaque étape, tentatives, durées, logs |
| MLflow | données utilisées, paramètres, métriques, artefacts, versions et alias des modèles |
| PostgreSQL `ops` | une ligne par exécution du pipeline, par lot contrôlé, par changement de champion, par alerte |
| PostgreSQL `tracing` | une ligne par requête HTTP, par prédiction, par feedback, et les heartbeats des workers |
| Grafana | lecture seule sur les deux schémas |

Le schéma `ops` duplique volontairement un peu d'information déjà présente dans Prefect et MLflow : il donne à Grafana des tables simples et stables, sans dépendre du schéma interne de ces outils. S'il est indisponible, le pipeline journalise un avertissement et continue.

## Base de données

Le schéma est dans `src/bikeshare/db/schema.sql`, appliqué par `bikeshare db init` (rejouable).

### Schéma `tracing` (API)

| Table | Une ligne = | Clés |
|---|---|---|
| `model_versions` | une version exacte chargée depuis MLflow | PK `model_id`, unique `(tracking_uri, registered_name, version)` |
| `api_requests` | un appel HTTP sur `/predict`, `/predict/batch` ou `/feedback`, erreurs comprises | PK `request_id` |
| `predictions` | une observation prédite | PK `prediction_id`, FK `request_id`, FK `model_id`, unique `(request_id, item_index)` |
| `feedbacks` | le retour sur une prédiction | PK `feedback_id`, FK `request_id` unique, FK `prediction_id` unique |
| `worker_heartbeats` | un échantillon CPU/mémoire d'un worker | PK `(instance_id, recorded_at)` |

Cardinalités : une requête de lot valide de N observations produit 1 ligne dans `api_requests` et N lignes dans `predictions`. Une requête en erreur a sa ligne dans `api_requests` (avec `error_code`) et aucune prédiction. Une prédiction a zéro ou un feedback.

Transactions de l'API :

- prédiction : la ligne de requête et toutes les prédictions sont écrites dans la même transaction, et la réponse 200 n'est envoyée qu'après le commit. Si l'écriture échoue, rien n'est gardé et l'API répond 503 ;
- feedback : l'insertion utilise `ON CONFLICT DO NOTHING`. Les contraintes d'unicité arbitrent les envois simultanés : un seul crée la ligne (201), les rejeux identiques répondent 200, un contenu différent répond 409. Chaque tentative garde sa propre ligne dans `api_requests` ;
- erreurs : tracées dans une transaction à part par le gestionnaire d'erreurs. Si PostgreSQL est arrêté, il ne reste que le log de l'API, avec le `request_id` rendu au client.

Les durées sont mesurées avec une horloge monotone. `processing_duration_ms` va du début du traitement jusqu'avant l'écriture en base ; `inference_duration_ms` ne couvre que `model.predict`. La latence vue par le client (réseau et commit compris) est mesurée par Locust.

### Schéma `ops` (pipeline)

| Table | Une ligne = |
|---|---|
| `pipeline_runs` | une exécution du flow : statut, résultat, lots, run MLflow, version produite, métriques, durée des étapes |
| `data_batches` | un lot contrôlé : accepté ou non, contrôles en échec |
| `model_promotions` | un déplacement de l'alias `champion` (promotion ou retour arrière), avec auteur et raison |
| `alerts` | une alerte reçue |

### Comptes

| Compte | Droits |
|---|---|
| `api_writer` | `SELECT`, `INSERT` sur `tracing`. Pas d'`UPDATE` ni de `DELETE` : l'historique ne peut pas être réécrit |
| `pipeline_writer` | `SELECT`, `INSERT`, `UPDATE` sur `ops` |
| `grafana_reader` | lecture seule sur les deux schémas, requêtes limitées à 10 secondes |

Le superutilisateur ne sert qu'à `db-init`.

## Quel modèle l'API sert-elle ?

L'API ne connaît ni chemin de fichier ni numéro de version : elle suit l'alias `champion` du registre.

- Au démarrage, chaque worker résout l'alias, vérifie que la version a été entraînée avec la même version des features (`feature_version`), charge le modèle et enregistre son identité dans `tracing.model_versions`.
- Toutes les 30 secondes (`MODEL_REFRESH_SECONDS`), il relit l'alias. Si la version a changé, le nouveau modèle est chargé puis remplace l'ancien d'un coup ; l'ancien sert jusqu'à ce moment.
- Si MLflow ne répond pas, le modèle en mémoire continue de servir.
- `POST /admin/model/refresh` force la relecture sur le worker qui reçoit l'appel.

Avec plusieurs workers, chacun suit l'alias de son côté : pendant au plus 30 secondes après une promotion, deux versions peuvent répondre. Ce n'est pas un problème de traçabilité, chaque prédiction enregistre la version qui l'a produite.

Le modèle est sérialisé au format skops plutôt que pickle : au chargement, seuls les types déclarés dans `registry/tracking.py` sont acceptés, il n'y a pas d'exécution de code arbitraire.

## Que se passe-t-il quand ça casse ?

| Panne | Comportement |
|---|---|
| Source de données injoignable | 3 nouvelles tentatives espacées, URL de secours, puis échec et alerte. Une erreur définitive (404, mauvaise empreinte) n'est pas retentée |
| Lot invalide | quarantaine avec rapport, exécution en échec, alerte, pas d'entraînement. Le champion reste servi. Le lot est relivré au cycle suivant, avant les lots suivants déjà en attente. Un fichier illisible ou mal nommé est traité de la même façon |
| Modèle moins bon que la baseline ou que le champion | version marquée `rejected` ou non promue. Ce n'est pas une panne : l'exécution est `Completed`, sans alerte |
| MLflow arrêté | le flow échoue en quelques secondes (les tentatives du client MLflow sont bornées) avec alerte. Le lot déjà validé reste accepté et servira à l'entraînement suivant. L'API continue avec le modèle en mémoire ; un worker qui redémarre pendant la panne reste non prêt jusqu'au retour de MLflow |
| PostgreSQL arrêté | l'API répond 503 en une seconde au plus (jamais de prédiction sans trace), `/health/ready` passe à 503, `/health/live` reste à 200 et l'erreur est journalisée avec son `request_id`. Reprise automatique quelques secondes après le retour de la base |
| Conteneur du pipeline tué en plein run | Prefect ne s'en aperçoit pas seul : le run reste `Running`. Au redémarrage, le conteneur passe ces runs en `Crashed`, ce qui déclenche l'alerte |
| Worker de l'API tué | Docker (ou uvicorn s'il y a plusieurs workers) le relance, il recharge le champion |
