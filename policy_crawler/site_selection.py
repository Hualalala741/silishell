from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


SCORE_COLUMNS = {
    "cost": "cost_score",
    "market": "market_score",
    "risk": "risk_score",
    "policy": "policy_score",
}


def _explicit_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def _explicit_false(value: Any) -> bool:
    return value is False or str(value).strip().lower() in {"false", "0", "no"}


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    clean = {key: max(0.0, float(weights.get(key, 0.0))) for key in SCORE_COLUMNS}
    total = sum(clean.values())
    if total == 0:
        return {key: 1 / len(clean) for key in clean}
    return {key: value / total for key, value in clean.items()}


def filter_candidates(
    candidates: Sequence[dict[str, Any]],
    minimum_area_mu: float,
    require_irrigation: bool = False,
) -> list[dict[str, Any]]:
    """Apply the original MVP hard filters without treating unknown as pass or fail."""
    eligible = []
    for candidate in candidates:
        if float(candidate["area_mu"]) < minimum_area_mu:
            continue
        if _explicit_true(candidate.get("protected_overlap")):
            continue
        if _explicit_false(candidate.get("climate_pass")):
            continue
        if _explicit_false(candidate.get("soil_pass")):
            continue
        if require_irrigation and _explicit_false(candidate.get("irrigation_available")):
            continue
        eligible.append(dict(candidate))
    return eligible


def rank_candidates(
    candidates: Sequence[dict[str, Any]],
    weights: Mapping[str, float],
    policy_score: float,
) -> list[dict[str, Any]]:
    """Run the original four-dimension weighted ranking in FastAPI."""
    normalized = normalize_weights(weights)
    ranked = []
    for candidate in candidates:
        row = dict(candidate)
        row["policy_score"] = policy_score
        total = 0.0
        for dimension, column in SCORE_COLUMNS.items():
            raw = row.get(column)
            value = 0.0 if raw is None else min(100.0, max(0.0, float(raw)))
            row[column] = value
            total += value * normalized[dimension]
        row["totalScore"] = round(total, 1)
        ranked.append(row)
    ranked.sort(key=lambda row: (row["totalScore"], float(row["area_mu"])), reverse=True)
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return ranked
