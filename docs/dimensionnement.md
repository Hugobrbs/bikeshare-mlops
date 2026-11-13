# Tests de charge et dimensionnement

## Objectif de service

Fixé avant de mesurer, pour `POST /predict` vu du client :

- p95 inférieur à 150 ms ;
- moins de 1 % de requêtes en échec.

Un échec est une réponse autre que 200, ou un 200 dont le corps est incohérent (Locust vérifie la présence de la prédiction et de la version du modèle).

La **capacité soutenable** d'une configuration est le plus grand débit observé sur un palier où toutes les répétitions respectent cet objectif. Ce n'est pas le débit maximal : au-delà, l'API répond encore, mais trop lentement.

## Protocole

| Elément | Valeur |
|---|---|
| Scénario | `OnlineUser` : prédictions unitaires, 500 observations valides générées avec une graine fixe |
| Temps de réflexion | 50 à 150 ms entre deux requêtes d'un même utilisateur (environ 10 req/s par utilisateur tant que l'API suit) |
| Paliers | 5, 10, 20, 40, 80 et 160 utilisateurs simultanés |
| Durée | 40 s par tir, après une chauffe de 10 s non mesurée |
| Répétitions | 2 par palier, chacune avec son identifiant de campagne (`X-Test-Campaign`) |
| Générateur | Locust 2.46.6 sur la machine hôte, 4 processus |
| Ressources | CPU et mémoire du conteneur relevés en continu avec `docker stats` |

Conditions gardées identiques pendant toute la campagne :

- même version du modèle servie (pipeline arrêté, donc pas de promotion en cours de tir) ;
- traçabilité PostgreSQL active : chaque requête et chaque prédiction sont écrites en base, c'est le service complet qui est mesuré ;
- pool de 5 connexions PostgreSQL par worker ;
- Grafana ouvert sur aucun dashboard.

Trois configurations du conteneur de l'API, appliquées avec les limites de ressources de Docker Compose :

| Configuration | CPU | Mémoire | Workers uvicorn |
|---|---|---|---|
| S | 1 | 768 Mo | 1 |
| M | 2 | 1280 Mo | 2 |
| L | 4 | 2304 Mo | 4 |

Machine : Apple M5 (10 coeurs, 24 Go), Docker Desktop limité à 10 CPU et 8 Go. Locust, PostgreSQL, MLflow, Prefect et l'API partagent les mêmes coeurs.

Reproduire :

```sh
make loadtest
uv run python loadtests/analyze.py loadtests/results/<date>
```

Les fichiers bruts de Locust sont dans `loadtests/results/<date>/raw/`, la synthèse dans `summary.csv` et `report.md`.

## Résultats

Campagne du 12 novembre 2026, 54 tirs. Médiane des deux répétitions de chaque palier. La colonne "Objectif" vaut "oui" seulement si les deux répétitions le respectent. Des paliers intermédiaires (15, 30 et 60 utilisateurs) ont été ajoutés après un premier passage pour resserrer autour du point de saturation.

| Config | CPU | Workers | Utilisateurs | req/s | p50 ms | p95 ms | p99 ms | Echecs % | CPU moyen % | Mémoire max Mo | Objectif |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S | 1.0 | 1 | 5 | 45 | 7 | 23 | 43 | 0.00 | 24 | 200 | oui |
| S | 1.0 | 1 | 10 | 88 | 7 | 34 | 76 | 0.00 | 38 | 196 | oui |
| S | 1.0 | 1 | 15 | 124 | 10 | 70 | 140 | 0.00 | 58 | 203 | oui |
| S | 1.0 | 1 | 20 | 134 | 37 | 160 | 255 | 0.00 | 84 | 202 | non |
| S | 1.0 | 1 | 40 | 116 | 220 | 435 | 785 | 0.00 | 92 | 212 | non |
| S | 1.0 | 1 | 80 | 123 | 510 | 815 | 1080 | 0.00 | 93 | 212 | non |
| S | 1.0 | 1 | 160 | 131 | 1100 | 1500 | 1600 | 0.00 | 93 | 219 | non |
| M | 2.0 | 2 | 5 | 46 | 7 | 14 | 24 | 0.00 | 23 | 470 | oui |
| M | 2.0 | 2 | 10 | 92 | 6 | 16 | 26 | 0.00 | 34 | 464 | oui |
| M | 2.0 | 2 | 20 | 176 | 6 | 38 | 107 | 0.00 | 73 | 472 | oui |
| M | 2.0 | 2 | 30 | 234 | 13 | 98 | 180 | 0.00 | 132 | 467 | oui |
| M | 2.0 | 2 | 40 | 201 | 96 | 200 | 265 | 0.00 | 186 | 469 | non |
| M | 2.0 | 2 | 80 | 186 | 320 | 450 | 545 | 0.00 | 189 | 478 | non |
| M | 2.0 | 2 | 160 | 180 | 765 | 1150 | 1300 | 0.00 | 190 | 485 | non |
| M0 | 2.0 | 2 | 5 | 45 | 7 | 14 | 28 | 0.00 | 33 | 470 | oui |
| M0 | 2.0 | 2 | 10 | 91 | 6 | 20 | 62 | 0.00 | 60 | 466 | oui |
| M0 | 2.0 | 2 | 20 | 87 | 125 | 270 | 365 | 0.00 | 187 | 479 | non |
| M0 | 2.0 | 2 | 40 | 75 | 420 | 605 | 760 | 0.00 | 189 | 476 | non |
| M0 | 2.0 | 2 | 80 | 71 | 960 | 1400 | 1650 | 0.00 | 189 | 484 | non |
| M0 | 2.0 | 2 | 160 | 80 | 1800 | 2600 | 2850 | 0.00 | 193 | 494 | non |
| L | 4.0 | 4 | 5 | 46 | 7 | 14 | 30 | 0.00 | 24 | 840 | oui |
| L | 4.0 | 4 | 10 | 92 | 6 | 12 | 38 | 0.00 | 39 | 836 | oui |
| L | 4.0 | 4 | 20 | 184 | 5 | 22 | 50 | 0.00 | 66 | 844 | oui |
| L | 4.0 | 4 | 40 | 324 | 8 | 99 | 200 | 0.00 | 182 | 846 | oui |
| L | 4.0 | 4 | 60 | 368 | 34 | 190 | 280 | 0.00 | 282 | 850 | non |
| L | 4.0 | 4 | 80 | 367 | 118 | 250 | 330 | 0.00 | 372 | 848 | non |
| L | 4.0 | 4 | 160 | 323 | 380 | 575 | 695 | 0.00 | 378 | 862 | non |

| Config | Capacité soutenable (req/s) | Palier | p95 ms | req/s par CPU |
|---|---|---|---|---|
| S | 124 | 15 utilisateurs | 70 | 124 |
| M | 234 | 30 utilisateurs | 98 | 117 |
| M0 | 91 | 10 utilisateurs | 20 | 46 |
| L | 324 | 40 utilisateurs | 99 | 81 |

`M0` est la configuration M sans `OMP_NUM_THREADS=1` (voir plus bas). CPU moyen : 100 % = un coeur.

## Lecture des résultats

**Chaque configuration a un point de saturation net.** Pour S, le débit suit la charge jusqu'à 124 req/s (p95 de 70 ms, CPU à 58 %). Au palier suivant, 134 req/s, le CPU monte à 84 % et le p95 à 160 ms : l'objectif est perdu. Au-delà le débit plafonne autour de 120 à 130 req/s et seule la latence augmente (p95 de 1,5 s à 160 utilisateurs).

**En surcharge l'API ralentit mais ne tombe pas.** Aucun échec sur les 54 tirs, même à dix fois la capacité. Les requêtes attendent dans le pool de threads. La mémoire ne bouge pas (219 Mo au maximum pour S).

**Le débit par CPU baisse quand la configuration grossit.** 124 req/s par CPU pour S, 117 pour M, 81 pour L. Pour L la mesure est une borne basse : avec 4 coeurs pour l'API, 4 processus Locust et PostgreSQL, les 10 coeurs de la machine sont occupés et le générateur de charge gêne ce qu'il mesure.

**La mémoire est proportionnelle au nombre de workers** : environ 215 Mo par worker (219 Mo pour S, 489 pour M, 868 pour L), quel que soit le palier. Chaque worker charge son propre modèle.

**L'inférence domine le temps de réponse.** Sur la campagne `S-u10-r1`, côté serveur : p50 de 4,1 ms et p95 de 28,6 ms, dont 26,4 ms dans `model.predict`. Locust mesure un p95 de 37 ms côté client : le réseau local et l'écriture en base ajoutent moins de 10 ms.

**Les threads OpenMP de scikit-learn coûtent plus de la moitié du débit si on les oublie.** Premier passage de M sans réglage (`M0`) : 91 req/s tenus au lieu de 234, avec le CPU à 187 % dès 20 utilisateurs. Par défaut scikit-learn lance autant de threads que de CPU pour chaque prédiction. Pour une seule ligne c'est inutile, et les threads des différents workers se gênent. `OMP_NUM_THREADS=1` est maintenant fixé dans `docker-compose.yml` ; le parallélisme vient des workers.

**Les traces en base recoupent Locust.** Campagne `S-u10-r1` : 3 476 requêtes comptées par Locust, 3 476 lignes dans `tracing.api_requests`. Campagne `L-u40-r1` : 12 515 des deux côtés. Sur un palier saturé (`L-u80-r1`) la base en compte 41 de plus (14 161 contre 14 120) : ce sont les requêtes encore en cours quand Locust s'arrête de compter.

## Recommandation

**Un pod = 1 vCPU, 512 Mo, 1 worker uvicorn, `OMP_NUM_THREADS=1`, 5 connexions PostgreSQL.**

- 1 vCPU et 1 worker : c'est la configuration qui rend le plus par CPU (124 req/s contre 117 et 81).
- 512 Mo : 219 Mo observés au maximum. La marge couvre le chargement d'un nouveau modèle à côté de l'ancien pendant un changement de champion.
- Capacité retenue : **85 req/s par pod**, soit 70 % des 124 req/s mesurés. A ce débit le CPU est autour de 40 % et le p95 sous 40 ms.

Nombre de pods = pic attendu / 85, arrondi au supérieur, plus un pod de réserve :

| Pic attendu | Pods |
|---|---|
| 50 req/s | 2 |
| 150 req/s | 3 |
| 300 req/s | 5 |
| 500 req/s | 7 |

Mise à l'échelle automatique :

- ajouter un pod quand le CPU moyen dépasse 60 % : à 58 % l'objectif est tenu, à 84 % il est perdu ;
- minimum 2 pods, pour survivre à la perte d'un pod ;
- maximum 8 pods : un plafond évite qu'une attaque ou un client en boucle fasse grimper la facture, et 8 x 5 = 40 connexions restent loin des 100 de PostgreSQL par défaut.

Sans orchestrateur, sur une seule machine avec Docker Compose, la configuration M tient 234 req/s :

```sh
API_WORKERS=2 API_CPUS=2.0 API_MEMORY=1280M docker compose up -d api
```

Ce qui n'a pas été mesuré : le comportement d'un autoscaler sous une salve brutale (il n'y en a pas en local), et la charge côté PostgreSQL, qui deviendra le facteur limitant avant l'API si on multiplie les pods.

## Limites

- Tout tourne sur un seul poste : le générateur de charge et la base prennent du CPU à l'API. Les chiffres servent à comparer des configurations entre elles, pas à promettre une capacité en production.
- Les tirs sont courts (40 s) et répétés deux fois : assez pour situer la saturation, pas pour mesurer une dérive sur la durée (fuite mémoire, remplissage de la base).
- Seul le scénario unitaire est dimensionné. Les scénarios `BatchUser` et `MixedUser` existent pour l'exploration.
- La latence mesurée par Locust inclut le réseau local et l'écriture en base. Le dashboard Grafana affiche la durée de traitement côté serveur, plus courte : les deux ne se comparent pas directement.
