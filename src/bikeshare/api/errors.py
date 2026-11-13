class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def model_unavailable() -> ApiError:
    return ApiError(503, "model_unavailable", "Aucun modèle n'est chargé pour le moment.")


def storage_unavailable() -> ApiError:
    return ApiError(503, "storage_unavailable", "Le stockage des traces est indisponible.")


def prediction_not_found() -> ApiError:
    return ApiError(404, "prediction_not_found", "Aucune prédiction avec cet identifiant.")


def feedback_conflict(message: str) -> ApiError:
    return ApiError(409, "feedback_conflict", message)
