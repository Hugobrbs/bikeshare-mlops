# Scénario de démonstration

Toutes les commandes se lancent depuis la racine du dépôt, stack démarrée (`make up`). Pour une démonstration, raccourcir la cadence du pipeline dans `.env` (`PIPELINE_SCHEDULE_SECONDS=300`) puis relancer `make up`. Le fichier `.env` est créé par le premier `make up` à partir de `.env.example`. Pour que le tag `git_commit` des runs MLflow désigne le code exécuté plutôt que `dev`, y renseigner aussi `APP_REVISION` avec le résultat de `git rev-parse --short HEAD`.

Le dataset contient 24 mois. Le premier run en ingère 6, chaque run suivant un seul. Une fois tous rejoués, le pipeline n'a plus rien à ingérer et ses exécutions se terminent avec le résultat `no_new_data` : à 300 secondes, les exécutions planifiées y arrivent en une heure et demie, plus tôt si on ajoute des `make train`. Pour repartir d'une stack vide avant une démonstration :

```sh
make reset
make up
```

Onglets à ouvrir avant de commencer :

- Prefect : http://localhost:4200
- MLflow : http://localhost:7281
- Swagger : http://localhost:8001/docs
- Grafana : http://localhost:3000 (admin / `bikeshare-local-admin`), dashboards "Bikeshare - Pipeline" et "Bikeshare - API"

Dans MLflow, fermer le panneau "MLflow Assistant" qui s'ouvre à droite. Dans Grafana, replier le menu latéral : ouvert, il tronque les titres des panneaux et les colonnes des tableaux.

Sur une stack vide, les sept sections se jouent en une dizaine de minutes de commandes.

## 1. Fonctionnement nominal

### Le pipeline

```sh
make train
```

Dans Prefect, page "Runs", le flow run le plus récent montre sept tâches dans l'ordre (onglet "Task Runs") : acquisition, validation, preparation, entrainement, evaluation, publication, decision. Dans l'onglet "Logs", la tâche `validation` donne le lot traité et le nombre de contrôles, `decision` le verdict de la politique.

La page "Deployments" montre le déploiement `replay-mensuel` et sa planification : les exécutions suivantes partent sans intervention.

Dans MLflow, expérience `bikeshare-demand`, le run `train-AAAA-MM` contient :

- les paramètres (graine, fenêtre, hyperparamètres) et les tags (`flow_run_id`, `training_batches`, `data_fingerprint`, `git_commit`) ;
- les jeux d'entraînement et de test avec leur empreinte ;
- les métriques du candidat, de la baseline et du champion sur le même jeu de test ;
- les rapports de validation des lots, l'importance des variables et le graphique prévu/réel.

Dans "Models", la version créée porte les tags `validation_status` et `validation_reasons`, et l'alias `champion` si elle a été promue.

```sh
make registry-status
```

### L'API

```sh
curl -s localhost:8001/predict -H 'content-type: application/json' -d '{
  "observation": {"timestamp": "2012-06-15T08:00:00", "weather_code": 1,
    "temperature_c": 22.5, "feels_like_c": 25, "humidity_pct": 60, "windspeed_kmh": 12}
}'
```

La réponse contient la version exacte du modèle et, dans `prediction`, un `prediction_id`. On envoie la valeur réellement observée pour cette prédiction (remplacer l'identifiant) :

```sh
curl -s -i localhost:8001/feedback -H 'content-type: application/json' -d '{
  "feedback_id": "7b7a5f0e-6d0f-4a43-9a53-0d4f5d1c2a10",
  "prediction_id": "<prediction_id>",
  "observed_rentals": 412,
  "observed_at": "2012-06-15T09:05:00Z",
  "label_source": "comptage-stations"
}'
```

Première fois : `201`. La même commande rejouée : `200` avec `already_recorded`, sans doublon. Avec une autre valeur : `409`. Si le scénario a déjà été joué sur cette stack, changer le `feedback_id`, sinon la première réponse est aussi un `409`.

Entrée invalide (`weather_code` à 9) : `422` avec un `request_id`, et la requête apparaît quand même dans le panneau "Erreurs par endpoint" de Grafana.

Tout est en base :

```sh
docker compose exec postgres psql -U postgres -d bikeshare -c \
  "select endpoint, http_status, item_count, round(processing_duration_ms::numeric, 1) as ms, error_code
   from tracing.api_requests order by started_at desc limit 5"
```

## 2. Incident : un lot de données invalide

```sh
make incident-data
```

La commande dépose un lot tronqué (la moitié des heures du mois) puis lance le pipeline.

Ce qu'on observe :

1. Prefect : le flow run passe en `Failed`, la tâche `validation` est en échec, les tâches suivantes ne sont pas lancées. Pas de nouvelle tentative : rejouer un lot invalide ne le rendrait pas valide.
2. Le lot est en quarantaine avec son rapport : `docker compose exec pipeline bikeshare data status`.
3. MLflow : aucun nouveau run, aucune nouvelle version.
4. Grafana, dashboard "Bikeshare - Pipeline" : exécution en échec avec le résultat `invalid_data`, lot rejeté avec les contrôles en échec (`expect_table_row_count_to_be_between`), et une ligne dans le tableau "Alertes". L'alerte est envoyée par une automation du serveur Prefect (`bikeshare-flow-failure`, page "Automations"), pas par le flow.
5. L'API répond toujours, avec le même champion : `curl -s localhost:8001/model`.

Reprise : au cycle suivant (ou `make train`), le fournisseur relivre le même mois, propre. Le pipeline l'accepte et repart sans intervention.

D'autres défauts sont disponibles, chacun attrapé par un contrôle différent. La commande dépose le lot, `make train` lance ensuite le pipeline :

```sh
docker compose exec pipeline bikeshare data publish --corrupt negative-rentals
docker compose exec pipeline bikeshare data publish --corrupt frozen-sensor
docker compose exec pipeline bikeshare data publish --corrupt dropped-column
docker compose exec pipeline bikeshare data publish --corrupt duplicated-rows
```

## 3. Retour arrière

```sh
make registry-status
make rollback
curl -s localhost:8001/model
```

L'alias `champion` revient sur la version précédente. L'API sert l'ancienne version au plus tard 30 secondes après (`MODEL_REFRESH_SECONDS`), sans redémarrage. Pour ne pas attendre :

```sh
curl -s -X POST localhost:8001/admin/model/refresh -H 'X-Admin-Token: bikeshare-local-admin-token'
```

Le changement est tracé dans le tableau "Changements de champion" de Grafana et apparaît comme annotation sur les graphiques de l'API.

La version quittée est marquée `rolled_back` : relancer `make rollback` par erreur est refusé, elle ne peut plus revenir en service. Pour reculer de plusieurs versions d'un coup : `docker compose exec pipeline bikeshare registry rollback --to-version N --reason "..."`.

Une promotion manuelle de cette version est refusée elle aussi (remplacer par le numéro de la version quittée, visible avec `make registry-status`) :

```sh
docker compose exec pipeline bikeshare registry promote <version quittée> --reason essai
```

La commande répond `refusé : version N non servable (validation_status='rolled_back')`. Le refus est le même pour une version rejetée par la politique (`rejected`). En suivant ce scénario depuis une stack vide, le premier candidat rejeté est celui du mois 2011-12 : il n'y en a pas encore à cette étape.

## 4. Incident : MLflow indisponible

```sh
docker compose stop mlflow
curl -s localhost:8001/health/ready
curl -s localhost:8001/predict -H 'content-type: application/json' -d '{"observation": {"timestamp": "2012-06-15T08:00:00", "weather_code": 1, "temperature_c": 22.5, "feels_like_c": 25, "humidity_pct": 60, "windspeed_kmh": 12}}'
```

L'API reste prête et continue de prédire avec le modèle déjà en mémoire. `/health/ready` passe à `registry_reachable: false` à la relecture suivante de l'alias, au plus tard une trentaine de secondes après l'arrêt. Si on lance le pipeline pendant la panne (`make train`), le flow échoue en quelques secondes et une alerte arrive. Le lot validé juste avant reste accepté : il servira à l'entraînement suivant.

```sh
docker compose start mlflow
```

## 5. Incident : PostgreSQL indisponible

```sh
docker compose stop postgres
curl -s -i localhost:8001/health/ready
curl -s -i localhost:8001/predict -H 'content-type: application/json' -d '{"observation": {"timestamp": "2012-06-15T08:00:00", "weather_code": 1, "temperature_c": 22.5, "feels_like_c": 25, "humidity_pct": 60, "windspeed_kmh": 12}}'
docker compose logs api --since 1m
```

L'API n'est plus prête (`503`) et refuse de répondre un faux succès : une prédiction n'est rendue que si sa trace est écrite. L'erreur porte un `request_id` qu'on retrouve dans les logs de l'API, seul endroit où elle peut être tracée pendant la panne. Prefect utilise la même instance PostgreSQL : son interface est indisponible pendant ce test.

```sh
docker compose start postgres
```

L'API redevient prête toute seule.

## 6. Incident : le pipeline est tué en plein run

```sh
make incident-crash
```

La commande lance un run, attend le début de l'entraînement, tue le conteneur du pipeline puis le redémarre. Une exécution dure quelques secondes : tué à la main, le conteneur s'arrête le plus souvent avant que le run ait démarré, et le run s'exécute alors normalement au redémarrage.

Tant que le conteneur est arrêté, Prefect garde le run à l'état `Running`. Au redémarrage, le conteneur repère ces runs orphelins et les passe en `Crashed` : l'alerte part, l'exécution apparaît en échec (`crashed`) dans Grafana et le run MLflow resté ouvert est clos avec le statut `KILLED`. Le lot validé avant l'arrêt reste accepté : il servira à l'entraînement suivant.

## 7. Charge et supervision

```sh
make loadtest-ui
```

Ouvrir http://localhost:8089, lancer 20 utilisateurs, puis regarder le dashboard "Bikeshare - API" : requêtes par seconde, percentiles de durée, CPU et mémoire par worker. Le filtre "Campagne" isole le tir. La commande reste au premier plan : lancer la suite dans un second terminal.

Pour alimenter les panneaux de qualité observée avec de vraies valeurs, on rejoue des heures réelles du dataset : prédiction, puis feedback avec le nombre de locations réellement constaté.

```sh
uv run bikeshare data download
uv run python scripts/send_ground_truth.py --month 2011-10 --count 200
```

La première commande télécharge le dataset sur la machine hôte (celui du pipeline est dans un volume Docker).

Le panneau "MAE observée" affiche alors l'écart moyen entre prévu et réel pour la version servie. Le panneau "Avis favorables" reste vide : ce scénario n'envoie que des valeurs observées, pas d'avis.
