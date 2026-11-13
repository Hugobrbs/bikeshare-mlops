from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class PromotionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    version: str
    min_r2: float
    min_gain_vs_baseline_pct: float = Field(ge=0)
    min_gain_vs_champion_pct: float = Field(default=0, ge=0)
    min_segment_rows: int = Field(default=20, ge=1)
    reload_tolerance: float = Field(default=1e-6, ge=0)

    @property
    def label(self) -> str:
        return f"{self.policy_id}@{self.version}"


@dataclass
class Evidence:
    """Tout ce qui est mesuré sur le même jeu de test avant de décider."""

    candidate: dict
    baseline: dict
    champion: dict | None = None
    reload_delta: float | None = None


@dataclass
class Decision:
    status: Literal["approved", "rejected"]
    promote: bool
    reasons: list[str] = field(default_factory=list)
    gain_vs_baseline_pct: float | None = None
    gain_vs_champion_pct: float | None = None


def load_policy(path: Path) -> PromotionPolicy:
    with path.open(encoding="utf-8") as f:
        return PromotionPolicy.model_validate(yaml.safe_load(f))


def gain_pct(reference_mae: float, candidate_mae: float) -> float:
    return 100 * (reference_mae - candidate_mae) / reference_mae


def decide(evidence: Evidence, policy: PromotionPolicy) -> Decision:
    candidate, baseline = evidence.candidate, evidence.baseline
    reasons = []

    if candidate["r2"] < policy.min_r2:
        reasons.append(f"r2 {candidate['r2']:.2f} < {policy.min_r2}")

    baseline_gain = gain_pct(baseline["mae"], candidate["mae"])
    if baseline_gain < policy.min_gain_vs_baseline_pct:
        reasons.append(
            f"gain sur la baseline {baseline_gain:.1f}% < {policy.min_gain_vs_baseline_pct}%"
        )

    for name, segment in candidate["segments"].items():
        reference = baseline["segments"].get(name)
        if segment["rows"] < policy.min_segment_rows or reference is None:
            continue
        if segment["mae"] > reference["mae"]:
            reasons.append(f"segment {name} moins bon que la baseline")

    # une preuve manquante ne vaut pas acceptation
    if evidence.reload_delta is None:
        reasons.append("rechargement depuis le registre non vérifié")
    elif evidence.reload_delta > policy.reload_tolerance:
        reasons.append(f"le modèle rechargé prédit différemment (écart {evidence.reload_delta:g})")

    if reasons:
        return Decision("rejected", False, reasons, gain_vs_baseline_pct=baseline_gain)

    if evidence.champion is None:
        return Decision(
            "approved", True, ["aucun champion en place"], gain_vs_baseline_pct=baseline_gain
        )

    champion_gain = gain_pct(evidence.champion["mae"], candidate["mae"])
    promote = champion_gain >= policy.min_gain_vs_champion_pct
    reason = (
        f"gain de {champion_gain:.1f}% sur le champion"
        if promote
        else f"pas mieux que le champion ({champion_gain:.1f}%), champion conservé"
    )
    return Decision("approved", promote, [reason], baseline_gain, champion_gain)
