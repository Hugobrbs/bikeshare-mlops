# Données

## Source

[Bike Sharing Dataset](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset), UCI Machine Learning Repository.
Locations horaires du service Capital Bikeshare (Washington D.C.) en 2011 et 2012, croisées avec la météo : 17 379 lignes dans `hour.csv`.

Licence : [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Citation demandée par les auteurs :

> Fanaee-T, H. et Gama, J. (2013). Event labeling combining ensemble detectors and background knowledge. Progress in Artificial Intelligence. doi:10.1007/s13748-013-0040-3

Le dataset n'est pas versionné dans ce dépôt. `bikeshare data download` le télécharge et vérifie le sha256 de `hour.csv`, fixé dans `config/pipeline.yaml`. Si l'URL principale ne répond pas, l'ancienne URL de l'UCI sert de secours.

## Rejeu par lots

Le dataset est historique et figé. Pour simuler l'arrivée de nouvelles données, il est rejoué mois par mois : `bikeshare data publish` découpe le mois suivant dans `hour.csv` et le dépose dans `data/incoming/`. Le pipeline ne lit que ce dossier, jamais le fichier complet. Les données sont donc réelles, seule leur date d'arrivée est simulée.

`bikeshare data publish --corrupt <défaut>` dépose un lot volontairement abîmé (heures manquantes, valeurs négatives, colonne absente, capteur figé, doublons) pour tester les contrôles qualité.

## Dossiers

| Dossier | Contenu |
|---|---|
| `source/` | `hour.csv` téléchargé et son manifest (origine, date, sha256) |
| `incoming/` | lots déposés, en attente de validation |
| `curated/` | lots acceptés, convertis en unités physiques, et `manifest.json` |
| `quarantine/` | lots rejetés avec leur rapport de validation |

## Colonnes

| Colonne | Origine UCI | Description |
|---|---|---|
| `timestamp` | `dteday` + `hr` | heure de l'observation |
| `holiday` | `holiday` | jour férié |
| `weather_code` | `weathersit` | 1 dégagé, 2 brume, 3 pluie ou neige légère, 4 fortes intempéries |
| `temperature_c` | `temp` x 41 | température en °C |
| `feels_like_c` | `atemp` x 50 | température ressentie en °C |
| `humidity_pct` | `hum` x 100 | humidité relative en % |
| `windspeed_kmh` | `windspeed` x 67 | vitesse du vent en km/h |
| `rentals` | `cnt` | nombre de locations sur l'heure (cible) |

Colonnes écartées : `casual` et `registered` (leur somme vaut `cnt`, ce serait une fuite de la cible) et les colonnes calendaires (`season`, `mnth`, `weekday`, `workingday`), recalculées à partir de la date.
