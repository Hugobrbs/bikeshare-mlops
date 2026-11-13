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

TEMPERATURE_SCALE_C = 41.0
FEELS_LIKE_SCALE_C = 50.0
HUMIDITY_SCALE_PCT = 100.0
WINDSPEED_SCALE_KMH = 67.0

TIMESTAMP = "timestamp"
TARGET = "rentals"

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
