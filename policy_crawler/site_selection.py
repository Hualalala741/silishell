from __future__ import annotations

import re
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


def _search_normalize(value: str) -> str:
    value = re.sub(r"[\s，。、“”‘’（）()·/]+", "", str(value)).lower()
    return re.sub(r"(?:产业|行业)$", "", value)


def search_industry_profile(
    query: str, rows: Sequence[dict[str, Any]], limit: int = 1
) -> list[dict[str, Any]]:
    needle = _search_normalize(query)
    if not needle:
        return []
    ranked = []
    for source in rows:
        row = dict(source)
        names = [row.get("industry", ""), *(row.get("industry_aliases") or [])]
        score = 0.0
        for name in names:
            candidate = _search_normalize(str(name))
            if not candidate:
                continue
            if needle == candidate:
                score = max(score, 1.0)
            elif candidate in needle:
                score = max(score, 0.98)
            elif needle in candidate:
                score = max(score, 0.93)
            else:
                overlap = len(set(needle) & set(candidate))
                score = max(score, 0.8 * 2 * overlap / (len(set(needle)) + len(set(candidate))))
        descriptive_text = " ".join(
            [
                str(row.get("development_goal", "")),
                *map(str, row.get("development_directions") or []),
                *map(str, row.get("planned_projects") or []),
            ]
        )
        if needle in _search_normalize(descriptive_text):
            score = max(score, 0.75)
        if score >= 0.65:
            row["match_score"] = round(score, 3)
            ranked.append(row)
    return sorted(ranked, key=lambda row: row["match_score"], reverse=True)[:limit]


def compare_plan_mentions(query: str, profiles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    fourteenth_rows = [row for row in profiles if row.get("plan_period") == "2021-2025"]
    fifteenth_rows = [row for row in profiles if row.get("plan_period") == "2026-2030"]
    fourteenth_matches = search_industry_profile(query, fourteenth_rows)
    fifteenth_matches = search_industry_profile(query, fifteenth_rows)
    fourteenth = fourteenth_matches[0] if fourteenth_matches else None
    fifteenth = fifteenth_matches[0] if fifteenth_matches else None
    if fourteenth and fifteenth:
        status, level = "十四五、十五五均提及", 2
    elif fifteenth:
        status, level = "十五五新增", 3
    elif fourteenth:
        status, level = "十四五提及，十五五未提及", 1
    else:
        status, level = "未提及", 0
    return {
        "fourteenth": fourteenth,
        "fifteenth": fifteenth,
        "status": status,
        "level": level,
        "score": round(level / 3 * 100, 1),
    }
