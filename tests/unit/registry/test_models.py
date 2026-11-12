import pytest
from mlflow.tracking import MlflowClient

from bikeshare.registry.models import PREVIOUS_ALIAS, ModelRegistry, PromotionRefusedError
from bikeshare.registry.policy import Decision, PromotionPolicy

NAME = "bikeshare-test"
POLICY = PromotionPolicy(policy_id="test", version="1", min_r2=0.5, min_gain_vs_baseline_pct=15)


@pytest.fixture
def registry(client: MlflowClient) -> ModelRegistry:
    return ModelRegistry(client, NAME)


def add_version(registry: ModelRegistry, status: str | None = "approved") -> str:
    client = registry.client
    if not client.search_registered_models(f"name='{NAME}'"):
        client.create_registered_model(NAME)
    version = str(client.create_model_version(NAME, source="file:///tmp/model").version)
    if status:
        registry.record_decision(version, Decision(status, False, ["test"]), POLICY)
    return version


def test_no_champion_before_first_promotion(registry: ModelRegistry) -> None:
    assert registry.champion() is None
    add_version(registry)
    assert registry.champion() is None


def test_promote_moves_the_alias(registry: ModelRegistry) -> None:
    v1 = add_version(registry)

    change = registry.promote(v1, actor="pipeline", reason="premier modèle")

    assert str(registry.champion().version) == v1
    assert change.from_version is None
    assert change.to_version == v1
    assert change.action == "promote"
    assert registry.champion().tags["promoted_by"] == "pipeline"


def test_promote_remembers_previous_champion(registry: ModelRegistry) -> None:
    v1, v2 = add_version(registry), add_version(registry)
    registry.promote(v1, actor="pipeline", reason="v1")

    change = registry.promote(v2, actor="pipeline", reason="v2")

    assert change.from_version == v1
    assert str(registry.champion().version) == v2
    assert str(registry.version_by_alias(PREVIOUS_ALIAS).version) == v1


@pytest.mark.parametrize("status", ["rejected", None])
def test_unapproved_version_cannot_be_promoted(registry: ModelRegistry, status: str | None) -> None:
    version = add_version(registry, status=status)

    with pytest.raises(PromotionRefusedError, match="non servable"):
        registry.promote(version, actor="hugo", reason="essai")

    assert registry.champion() is None


def test_rollback_restores_previous_champion(registry: ModelRegistry) -> None:
    v1, v2 = add_version(registry), add_version(registry)
    registry.promote(v1, actor="pipeline", reason="v1")
    registry.promote(v2, actor="pipeline", reason="v2")

    change = registry.rollback(actor="hugo", reason="régression en production")

    assert str(registry.champion().version) == v1
    assert change.action == "rollback"
    assert (change.from_version, change.to_version) == (v2, v1)
    # un second rollback annule le premier
    assert registry.rollback(actor="hugo", reason="fausse alerte").to_version == v2


def test_rollback_to_explicit_version(registry: ModelRegistry) -> None:
    v1, v2, v3 = (add_version(registry) for _ in range(3))
    for version in (v1, v2, v3):
        registry.promote(version, actor="pipeline", reason="auto")

    registry.rollback(actor="hugo", reason="retour à v1", to_version=v1)

    assert str(registry.champion().version) == v1


def test_rollback_refusals(registry: ModelRegistry) -> None:
    with pytest.raises(PromotionRefusedError, match="aucun champion"):
        registry.rollback(actor="hugo", reason="x")

    v1 = add_version(registry)
    registry.promote(v1, actor="pipeline", reason="v1")
    with pytest.raises(PromotionRefusedError, match="aucune version précédente"):
        registry.rollback(actor="hugo", reason="x")
    with pytest.raises(PromotionRefusedError, match="déjà le champion"):
        registry.rollback(actor="hugo", reason="x", to_version=v1)

    rejected = add_version(registry, status="rejected")
    with pytest.raises(PromotionRefusedError, match="non servable"):
        registry.rollback(actor="hugo", reason="x", to_version=rejected)
    assert str(registry.champion().version) == v1


def test_decision_is_stored_on_the_version(registry: ModelRegistry) -> None:
    version = add_version(registry, status=None)

    registry.record_decision(
        version, Decision("rejected", False, ["r2 trop bas", "segment"]), POLICY
    )

    tags = registry.client.get_model_version(NAME, version).tags
    assert tags["validation_status"] == "rejected"
    assert tags["validation_policy"] == "test@1"
    assert tags["validation_reasons"] == "r2 trop bas ; segment"
