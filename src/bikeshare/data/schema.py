# Colonnes de hour.csv tel que publié par l'UCI
SOURCE_COLUMNS = (
    "instant",
    "dteday",
    "season",
    "yr",
    "mnth",
    "hr",
    "holiday",
    "weekday",
    "workingday",
    "weathersit",
    "temp",
    "atemp",
    "hum",
    "windspeed",
    "casual",
    "registered",
    "cnt",
)

# L'UCI normalise ces mesures entre 0 et 1 en divisant par ces valeurs (cf. Readme de l'archive)
TEMPERATURE_SCALE_C = 41.0
FEELS_LIKE_SCALE_C = 50.0
HUMIDITY_SCALE_PCT = 100.0
WINDSPEED_SCALE_KMH = 67.0

TIMESTAMP = "timestamp"
TARGET = "rentals"

# Ce que l'API reçoit et ce que le modèle voit
INPUT_COLUMNS = (
    TIMESTAMP,
    "holiday",
    "weather_code",
    "temperature_c",
    "feels_like_c",
    "humidity_pct",
    "windspeed_kmh",
)
DOMAIN_COLUMNS = (*INPUT_COLUMNS, TARGET)

# 1 dégagé, 2 brume, 3 pluie ou neige légère, 4 fortes intempéries
WEATHER_CODES = (1, 2, 3, 4)
