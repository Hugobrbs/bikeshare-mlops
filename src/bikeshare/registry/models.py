from dataclasses import dataclass
from datetime import UTC, datetime

from mlflow.entities.model_registry import ModelVersion
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from bikeshare.registry.policy import Decision, PromotionPolicy

PREVIOUS_ALIAS = "previous-champion"


class PromotionRefusedError(RuntimeError):
    pass


@dataclass(frozen=True)
class AliasChange:
    action: str
    model_name: str
    alias: str
    from_version: str | None
    to_version: str
    actor: str
    reason: str
    at: str


class ModelRegistry:
    """Le modèle servi est celui que désigne l'alias. Promouvoir ou revenir en
    arrière = déplacer l'alias, sans toucher aux versions."""

    def __init__(self, client: MlflowClient, name: str, alias: str = "champion") -> None:
        self.client = client
        self.name = name
        self.alias = alias

    def version_by_alias(self, alias: str) -> ModelVersion | None:
        try:
            return self.client.get_model_version_by_alias(self.name, alias)
        except MlflowException as e:
            # modèle pas encore enregistré, ou alias jamais posé
            if e.error_code in ("RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"):
                return None
            raise

    def champion(self) -> ModelVersion | None:
        return self.version_by_alias(self.alias)

    def record_decision(self, version: str, decision: Decision, policy: PromotionPolicy) -> None:
        tags = {
            "validation_status": decision.status,
            "validation_policy": policy.label,
            "validation_reasons": " ; ".join(decision.reasons),
        }
        for key, value in tags.items():
            self.client.set_model_version_tag(self.name, version, key, value)

    def promote(self, version: str, *, actor: str, reason: str) -> AliasChange:
        self._require_approved(version)
        return self._move(version, "promote", actor, reason)

    def rollback(self, *, actor: str, reason: str, to_version: str | None = None) -> AliasChange:
        current = self.champion()
        if current is None:
            raise PromotionRefusedError("aucun champion en place, rien à annuler")
        if to_version is None:
            previous = self.version_by_alias(PREVIOUS_ALIAS)
            if previous is None:
                raise PromotionRefusedError("aucune version précédente connue")
            to_version = previous.version
        # selon le backend MLflow renvoie le numéro de version en int ou en str
        to_version = str(to_version)
        if to_version == str(current.version):
            raise PromotionRefusedError(f"la version {to_version} est déjà le champion")
        self._require_approved(to_version)
        return self._move(to_version, "rollback", actor, reason)

    def _require_approved(self, version: str) -> None:
        status = self.client.get_model_version(self.name, version).tags.get("validation_status")
        if status != "approved":
            raise PromotionRefusedError(
                f"version {version} non servable (validation_status={status!r})"
            )

    def _move(self, version: str, action: str, actor: str, reason: str) -> AliasChange:
        version = str(version)
        current = self.champion()
        current_version = str(current.version) if current else None
        if current_version and current_version != version:
            self.client.set_registered_model_alias(self.name, PREVIOUS_ALIAS, current_version)
        self.client.set_registered_model_alias(self.name, self.alias, version)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        for key, value in {
            "promoted_at": now,
            "promoted_by": actor,
            "promotion_reason": reason,
        }.items():
            self.client.set_model_version_tag(self.name, version, key, value)
        return AliasChange(
            action=action,
            model_name=self.name,
            alias=self.alias,
            from_version=current_version,
            to_version=version,
            actor=actor,
            reason=reason,
            at=now,
        )
