from pathlib import Path

import pytest

from bikeshare.registry.policy import Evidence, PromotionPolicy, decide, load_policy

REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY = PromotionPolicy(
    policy_id="test", version="1", min_r2=0.5, min_gain_vs_baseline_pct=15, min_segment_rows=20
)


def report(mae: float, r2: float = 0.9, rush: float | None = None, rows: int = 40) -> dict:
    return {
        "mae": mae,
        "r2": r2,
        "segments": {"rush_hours": {"mae": rush if rush is not None else mae, "rows": rows}},
    }


def evidence(**overrides: object) -> Evidence:
    defaults = {"candidate": report(20), "baseline": report(60), "reload_delta": 0.0}
    return Evidence(**{**defaults, **overrides})


def test_repository_policy_is_valid() -> None:
    policy = load_policy(REPO_ROOT / "config" / "promotion-policy.yaml")

    assert policy.label == "bikeshare-promotion@1.0"


def test_first_model_is_promoted() -> None:
    decision = decide(evidence(), POLICY)

    assert decision.status == "approved"
    assert decision.promote
    assert decision.gain_vs_baseline_pct == pytest.approx(66.67, abs=0.01)


def test_better_than_champion_is_promoted() -> None:
    decision = decide(evidence(champion=report(25)), POLICY)

    assert decision.promote
    assert decision.gain_vs_champion_pct == pytest.approx(20)


def test_worse_than_champion_is_approved_but_not_promoted() -> None:
    decision = decide(evidence(champion=report(18)), POLICY)

    assert decision.status == "approved"
    assert not decision.promote
    assert "champion conservé" in decision.reasons[0]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"candidate": report(20, r2=0.1)}, "r2"),
        ({"candidate": report(55)}, "gain sur la baseline"),
        ({"candidate": report(20, rush=90)}, "segment rush_hours"),
        ({"reload_delta": None}, "non vérifié"),
        ({"reload_delta": 0.5}, "prédit différemment"),
    ],
    ids=["r2", "baseline", "segment", "reload-absent", "reload-different"],
)
def test_rejections(overrides: dict, reason: str) -> None:
    decision = decide(evidence(**overrides), POLICY)

    assert decision.status == "rejected"
    assert not decision.promote
    assert any(reason in r for r in decision.reasons)


def test_rejected_candidate_is_never_promoted_even_if_better_than_champion() -> None:
    decision = decide(evidence(candidate=report(20, r2=0.1), champion=report(80)), POLICY)

    assert decision.status == "rejected"
    assert not decision.promote


def test_small_segment_is_ignored() -> None:
    decision = decide(evidence(candidate=report(20, rush=90, rows=5)), POLICY)

    assert decision.status == "approved"


def test_all_failed_checks_are_reported() -> None:
    decision = decide(evidence(candidate=report(58, r2=0.2), reload_delta=None), POLICY)

    assert len(decision.reasons) == 3
