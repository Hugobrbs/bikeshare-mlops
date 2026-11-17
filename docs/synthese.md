# Synthèse des choix et résultats OPS

## Le sujet

Prévoir le nombre de locations de vélos en libre-service pour une heure donnée, à partir de la date et de la météo (dataset UCI Bike Sharing, 17 379 heures sur 2011-2012). Le modèle est un gradient boosting scikit-learn avec une loss de Poisson : rapide à entraîner (environ 1 seconde), suffisant pour illustrer le cycle. Les scores ne sont pas un objectif, ils servent à faire vivre les décisions de promotion.

Le dataset est historique. Il est rejoué mois par mois pour simuler un flux : chaque exécution du pipeline reçoit le mois suivant, comme un export périodique.

## Choix techniques

| Besoin | Choix | Pourquoi |
|---|---|---|
| Dépendances | uv, `uv.lock`, extras et groupes | installation identique partout (`uv sync --locked`), image de l'API sans les dépendances du pipeline |
| Configuration | variables d'environnement (pydantic-settings) + `config/pipeline.yaml` validé au chargement | l'environnement décrit où ça tourne, le YAML décrit le traitement. Une clé inconnue ou une valeur hors bornes arrête le programme avant tout calcul |
| Ingestion | téléchargement vérifié par sha256, URL de secours, rejeu par lots mensuels | relancer ne retélécharge rien, un fichier tronqué n'est jamais accepté |
| Qualité des données | Great Expectations, 40 expectations par lot, plus deux règles métier | contrôles lisibles et rapport par lot. Seuils calibrés sur les 24 mois réels (voir plus bas) |
| Orchestration | Prefect 3.8.8, `flow.serve` avec planning à intervalle | Python natif, pas de work pool à gérer pour un seul exécutant |
| Suivi et registre | MLflow 3.17.2, alias `champion` | versions immuables, alias déplaçable : promotion et retour arrière sans redéployer |
| Sérialisation du modèle | format skops avec liste de types autorisés | pas d'exécution de code arbitraire au chargement, contrairement à pickle |
| API | FastAPI, handlers synchrones, pool psycopg par worker | le calcul scikit-learn et les écritures SQL sont bloquants : ils tournent dans le pool de threads au lieu de bloquer la boucle asynchrone |
| Persistance | PostgreSQL, trois comptes à droits minimaux | l'API ne peut qu'ajouter des traces, Grafana ne peut que lire |
| Alertes | automation côté serveur Prefect vers un webhook | un retry réussi ne déclenche rien, et l'alerte ne dépend pas du processus du flow |
| Supervision | Grafana sur PostgreSQL, dashboards provisionnés | pas de Prometheus à opérer pour ce périmètre, dashboards versionnés avec le code |
| Charge | Locust, script de campagne | mêmes scénarios à chaque tir, résultats bruts conservés |

### Ce que les essais ont fait changer

Les quatre premiers points sont des écarts par rapport aux versions vues en cours.

- **MLflow 3.17.2 au lieu de 3.16.1** : `pip-audit` remonte trois CVE sur la 3.16.1.
- **Prefect 3.8.8 au lieu de 3.8.7** : l'image serveur 3.8.7 embarque SQLAlchemy 2.1, avec lequel son planificateur plante à l'insertion des exécutions planifiées (`Can't evaluate bulk DML statement`). La 3.8.8 revient à SQLAlchemy 2.0.
- **Serveur Prefect sur PostgreSQL au lieu de SQLite** : sur SQLite, une automation créée après le démarrage du serveur n'est pas prise en compte avant un redémarrage. Constaté en test : flow en échec, événement `prefect.flow-run.Failed` émis, aucune alerte. Sur PostgreSQL l'alerte part une seconde après l'échec.
- **FastAPI limité à moins de 0.155** : au-delà, le serveur Prefect (utilisé par les tests d'intégration) ne démarre plus.
- **Automation Prefect recréée plutôt que mise à jour** : après un `update`, l'automation ne se déclenche plus (événement d'échec émis, aucune action). Au démarrage le pipeline la laisse en place si elle n'a pas changé, sinon la supprime et la recrée.
- **`OMP_NUM_THREADS=1` dans le conteneur de l'API** : sans ce réglage, le débit à 2 CPU est divisé par plus de deux (mesuré, voir le dimensionnement).

Ces points ont été trouvés en faisant tourner la chaîne pour de vrai, pas en lisant la documentation.

## Contrôles qualité des données

Chaque lot passe 42 contrôles avant d'être accepté (40 expectations Great Expectations et 2 règles métier), sur les trois familles vues en cours :

| Famille | Contrôles | Défaut de démonstration attrapé |
|---|---|---|
| Volume | nombre de lignes entre 90 % et 100 % des heures du mois, tous les jours présents (un manquant toléré) | `missing-hours` |
| Qualité technique | colonnes attendues, pas de valeur manquante, bornes (`cnt` positif, mesures entre 0 et 1), codes météo connus, pas de doublon (date, heure), dates du bon mois | `negative-rentals`, `dropped-column`, `duplicated-rows` |
| Distribution | les 24 heures présentes, température non constante, moyenne des locations plausible | `frozen-sensor` |
| Métier (hors GX) | le mois suit le dernier mois accepté, `cnt = casual + registered` | |

Les seuils viennent des données réelles : le mois le moins complet (janvier 2011) contient 92,5 % de ses heures, d'où le seuil à 90 %. Octobre 2012 (ouragan Sandy, 95,2 %) passe aussi.

Un lot invalide est déplacé en quarantaine avec son rapport, l'exécution s'arrête et aucun modèle n'est entraîné. Il n'y a pas de nouvelle tentative sur cette étape : rejouer un lot invalide ne le rend pas valide.

## Politique de promotion

Le candidat, la baseline (moyenne par heure et type de jour) et le champion en place sont évalués sur le même jeu de test : les 7 derniers jours du lot le plus récent, qu'aucun des trois n'a vus à l'entraînement.

Une version est `approved` si (`config/promotion-policy.yaml`) :

- R2 d'au moins 0,5 ;
- MAE meilleure que la baseline d'au moins 15 % ;
- aucun segment (heures de pointe, nuit, week-end) moins bon que la baseline ;
- le modèle rechargé depuis le registre prédit exactement comme celui en mémoire.

Une preuve manquante vaut refus. Une version approuvée ne devient `champion` que si elle fait au moins aussi bien que le champion en place. Sinon elle reste dans le registre, approuvée mais non servie.

Le verdict et ses raisons sont posés en tags sur la version (`validation_status`, `validation_reasons`), et chaque déplacement de l'alias est enregistré dans `ops.model_promotions`.

## Preuves d'intégration

Tout ce qui suit a été observé sur la stack Docker complète, démarrée à vide.

| Scénario | Résultat observé |
|---|---|
| Premier démarrage | 6 lots acceptés, modèle v1 (MAE 21,8 contre 93,8 pour la baseline) promu, API prête quelques secondes après sans redémarrage |
| Clone neuf dans un dossier vide | stack démarrée sur d'autres ports à côté de la première : API prête en moins d'une minute (images déjà construites), même modèle v1 et même MAE de 21,8 |
| Exécution planifiée | lot 2011-07 accepté, v2 (MAE 18,6) promue car meilleure que la v1 sur le même test (21,4) |
| Lot tronqué, valeurs négatives ou capteur figé | exécution `Failed` à l'étape de validation, lot en quarantaine, aucun run MLflow, aucune nouvelle version, alerte en base une à deux secondes après, champion inchangé |
| Reprise après lot invalide | au cycle suivant le lot est relivré propre et accepté, sans intervention |
| Retour arrière | `bikeshare registry rollback` : alias de la v2 vers la v1, l'API sert la v1 5 secondes plus tard. La version quittée est marquée `rolled_back` et ne peut plus être servie |
| Feedback rejoué | 201 puis 200, une seule ligne. 8 envois simultanés identiques : un seul créé |
| Entrée invalide | 422 avec `request_id`, tracée en base avec son code d'erreur, aucune prédiction enregistrée |
| MLflow arrêté | l'API continue de prédire (`registry_reachable: false`). Un run lancé pendant la panne échoue en 5 secondes, avec alerte |
| PostgreSQL arrêté | `/health/ready` et `/predict` répondent 503 (tout de suite pour la première requête, puis après une seconde d'attente d'une connexion), `/health/live` reste à 200, erreur journalisée avec son `request_id`. API de nouveau prête 6 secondes après le redémarrage de la base |
| Conteneur du pipeline tué en plein run | au redémarrage, le run resté `Running` passe en `Crashed`, l'alerte arrive dans la seconde et le run MLflow resté ouvert est clos (`KILLED`) |
| Valeurs réelles renvoyées en feedback | 150 heures d'octobre 2011 prédites par la v3 puis comparées au réel : MAE observée de 33,9, affichée dans Grafana |

Les mêmes scénarios sont couverts par les tests automatisés :

- tests unitaires : logique métier, API avec une base et un modèle simulés ;
- tests d'intégration : vrai PostgreSQL (transactions, droits des comptes, feedbacks concurrents), vrai moteur Prefect et vrai MLflow sur le flow complet (premier entraînement, mois suivant, lot invalide puis reprise, source épuisée).

La CI lance les deux niveaux sur chaque PR, avec l'audit des dépendances et la construction des images.

## Ce que le rejeu des 24 mois a montré

Le dataset a été rejoué en entier sur la stack, un mois toutes les deux minutes. 12 versions du modèle ont été entraînées : 10 promues, 2 rejetées par la politique.

| Lot | Version | MAE candidat | MAE baseline | MAE champion en place | Décision |
|---|---|---|---|---|---|
| 2011-01 à 2011-06 | v1 | 21,8 | 93,8 | aucun | promue |
| 2011-07 | v2 | 18,6 | 51,8 | 21,4 | promue |
| 2012-04 | v4 | 23,7 | 68,6 | 91,2 | promue |
| 2012-10 | v10 | 42,5 | 86,2 | 102,3 | promue |
| 2012-11 | v11 | 42,4 | 62,6 | 57,1 | rejetée : heures de pointe moins bonnes que la baseline |
| 2012-12 | v12 | 61,5 | 162,7 | 31,6 | rejetée : R2 de -0,51 |

Trois choses à en retenir :

- **Le réentraînement compte.** En avril 2012 le champion en place, entraîné sur des données s'arrêtant à l'été 2011, fait 91,2 de MAE sur la semaine de test. Le modèle réentraîné fait 23,7.
- **La politique protège le service.** Les deux rejets tombent sur les semaines de test de fin novembre et de Noël, où la demande ne ressemble pas au reste de l'année. Sur la semaine de Noël le candidat fait 61,5 de MAE alors que le champion v10 fait 31,6 : sans la politique, on aurait remplacé un bon modèle par un moins bon.
- **Un rejet n'est pas une panne.** Ces deux exécutions sont `Completed` dans Prefect, sans alerte. Le verdict et sa raison sont sur la version dans MLflow et dans le dashboard du pipeline.

Les versions absentes du tableau (v3 et v5 à v9) ont toutes été promues. Entre 2011-09 et 2012-03, les lots ont été acceptés sans entraînement : c'est la période où MLflow avait été arrêté volontairement. Ils ont servi à l'entraînement suivant, comme prévu.

## Tests de charge et dimensionnement

Voir [dimensionnement.md](dimensionnement.md) pour le protocole et toutes les mesures.

Trois configurations du conteneur de l'API, de 5 à 160 utilisateurs simultanés, deux répétitions par palier, traçabilité PostgreSQL active. Objectif fixé avant les mesures : p95 inférieur à 150 ms et moins de 1 % d'échecs.

| Configuration | Capacité soutenable | p95 à ce débit | Débit par CPU | Mémoire max |
|---|---|---|---|---|
| S : 1 CPU, 1 worker | 124 req/s | 70 ms | 124 req/s | 219 Mo |
| M : 2 CPU, 2 workers | 234 req/s | 98 ms | 117 req/s | 489 Mo |
| L : 4 CPU, 4 workers | 324 req/s | 99 ms | 81 req/s | 868 Mo |

- Aucun échec sur les 54 tirs : en surcharge l'API ralentit mais ne tombe pas.
- La mémoire dépend du nombre de workers (environ 215 Mo chacun), pas de la charge.
- Sans `OMP_NUM_THREADS=1`, la configuration M ne tient que 91 req/s au lieu de 234.
- Les totaux de Locust recoupent les lignes de `tracing.api_requests` (3 476 des deux côtés sur une campagne).

**Recommandation** : des pods de 1 vCPU et 512 Mo avec un worker, comptés à 85 req/s chacun (70 % de la capacité mesurée), plus un pod de réserve. Soit 3 pods pour un pic de 150 req/s. Mise à l'échelle automatique à 60 % de CPU, entre 2 et 8 pods.

## Limites connues

- **Un seul exécutant pour le pipeline.** `flow.serve` tourne dans un conteneur. S'il est tué en plein run, Prefect garde le run à l'état `Running`. Le run n'est passé en `Crashed` (et l'alerte envoyée) qu'au redémarrage du conteneur : tant qu'il reste arrêté, rien ne le signale.
- **Pas de reprise des traces perdues.** Si PostgreSQL est arrêté, l'API refuse de prédire (503) plutôt que de prédire sans trace. Les erreurs de cette période ne sont que dans ses logs : le dashboard a un trou, Locust reste la référence externe.
- **Plusieurs workers, plusieurs caches.** Après une promotion, chaque worker bascule de son côté, au plus tard après `MODEL_REFRESH_SECONDS`. Deux versions peuvent répondre pendant ce délai. Chaque prédiction enregistre la sienne.
- **Suivi `ops` au mieux.** Si la base ne répond pas pendant un entraînement, la ligne de suivi est perdue (Prefect et MLflow gardent l'information).
- **MLflow et artefacts sur disque local.** Backend SQLite et artefacts dans un volume : suffisant sur un poste, à remplacer par PostgreSQL et un stockage objet pour plusieurs machines.
- **Pas d'authentification sur l'API.** Seule la route d'administration demande un jeton. La stack n'écoute que sur `127.0.0.1`.
- **Feedbacks non exploités automatiquement.** Ils alimentent le dashboard (MAE observée, couverture) mais ne déclenchent ni réentraînement ni retour arrière.
- **Dérive des données non mesurée automatiquement.** Le dashboard montre la distribution des prédictions et d'une variable d'entrée, sans test statistique ni alerte.
- **Mesures de charge sur un poste unique.** Locust, Docker, PostgreSQL et l'API se partagent les mêmes coeurs : les chiffres valent pour comparer des configurations, pas comme promesse de production.
- **Images volumineuses** (1,4 Go pour l'API), à cause du paquet `mlflow` complet. `mlflow-skinny` les réduirait.
