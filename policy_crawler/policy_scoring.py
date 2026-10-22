from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any


SCORING_VERSION = "measure-design-v0.1"
STANDARD_TOOL_IDS = tuple(range(1, 11))
DIRECTIONAL_PATTERN = re.compile(r"鼓励|引导|探索|倡导|力争")
MAJOR_PATTERN = re.compile(
    r"重大专项|专项资金|重点工程|示范工程|国家级.{0,12}(?:中心|基地|项目|平台)"
)


def score_and_deduplicate_measures(
    measures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scored = [score_measure(item) for item in measures]
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in scored:
        groups[_dedup_key(item)].append(item)

    enriched: list[dict[str, Any]] = []
    for rows in groups.values():
        primary = max(
            rows,
            key=lambda item: (
                item["specificity_score"],
                len(item.get("evidence_text") or ""),
                item["measure_id"],
            ),
        )
        group_id = "DG_" + hashlib.sha1(
            "|".join(sorted(item["measure_id"] for item in rows)).encode("utf-8")
        ).hexdigest()[:12]
        for item in rows:
            row = dict(item)
            row["dedup_group_id"] = group_id
            row["is_primary"] = item["measure_id"] == primary["measure_id"]
            row["duplicate_of"] = (
                None if row["is_primary"] else primary["measure_id"]
            )
            enriched.append(row)
    return enriched


def score_measure(measure: dict[str, Any]) -> dict[str, Any]:
    row = dict(measure)
    strength, strength_reasons = _strength_score(row)
    specificity, specificity_reasons = _specificity_score(row)
    row.update(
        {
            "strength_score": strength,
            "specificity_score": specificity,
            "design_score": strength * specificity,
            "design_score_100": round(strength * specificity * 4, 2),
            "score_version": SCORING_VERSION,
            "score_reasons": {
                "strength": strength_reasons,
                "specificity": specificity_reasons,
            },
        }
    )
    return row


def aggregate_policy_scores(
    measures: list[dict[str, Any]],
    policy_tool_types: dict[int, tuple[str, str]],
) -> dict[str, Any]:
    primary = [item for item in measures if item.get("is_primary", True)]
    dimensions: list[dict[str, Any]] = []
    for tool_id in STANDARD_TOOL_IDS:
        rows = [item for item in primary if item["policy_tool_type_id"] == tool_id]
        scores = sorted((item["design_score_100"] for item in rows), reverse=True)
        top_scores = scores[:3]
        dimensions.append(
            {
                "policy_tool_type_id": tool_id,
                "policy_tool_code": policy_tool_types[tool_id][0],
                "policy_tool_name": policy_tool_types[tool_id][1],
                "primary_measure_count": len(rows),
                "duplicate_measure_count": sum(
                    1
                    for item in measures
                    if item["policy_tool_type_id"] == tool_id
                    and not item.get("is_primary", True)
                ),
                "raw_design_score_sum": sum(item["design_score"] for item in rows),
                "average_design_score_100": _average(scores),
                "top3_design_score_100": _average(top_scores),
                "dimension_score_100": _average(top_scores),
                "top_measure_ids": [
                    item["measure_id"]
                    for item in sorted(
                        rows,
                        key=lambda value: (
                            value["design_score_100"],
                            value["specificity_score"],
                        ),
                        reverse=True,
                    )[:3]
                ],
            }
        )

    standard_scores = [item["dimension_score_100"] for item in dimensions]
    return {
        "score_version": SCORING_VERSION,
        "aggregation_method": "各政策工具取去重后设计分最高三条的平均值；县域诊断分对10类标准工具等权平均",
        "overall_unweighted_score_100": _average(standard_scores),
        "dimensions": dimensions,
        "raw_primary_measure_count": len(primary),
        "excluded_duplicate_count": len(measures) - len(primary),
        "warning": "等权总分仅用于MVP诊断，不代表特定企业的政策价值。",
    }


def _strength_score(measure: dict[str, Any]) -> tuple[int, list[str]]:
    text = measure.get("measure_text") or ""
    tool_id = measure["policy_tool_type_id"]
    dimensions = {item["dimension"] for item in measure.get("quantities", [])}
    has_time = bool(measure.get("time_scope") or measure.get("start_date") or measure.get("end_date"))
    has_target = bool(measure.get("policy_target"))
    has_agency = bool(measure.get("implementing_agencies"))
    reasons = ["存在明确政策动作"]
    score = 1 if DIRECTIONAL_PATTERN.search(text) and not dimensions else 2

    relevant_dimensions = {
        1: {"金额", "比例"},
        2: {"金额", "比例"},
        3: {"面积", "比例", "时间"},
        4: {"金额", "面积", "长度", "数量", "产能", "时间"},
        5: {"金额", "数量", "比例", "时间", "产能"},
        6: {"金额", "数量", "时间"},
        7: {"金额", "数量", "比例", "产值"},
        8: {"金额", "面积", "数量", "产能", "产值"},
        9: {"面积", "数量", "产能", "重量", "比例"},
        10: {"时间", "比例", "数量"},
    }.get(tool_id, set())
    quantified = bool(dimensions & relevant_dimensions)
    if quantified:
        score = max(score, 3)
        reasons.append("包含该政策工具的关键量化指标")
    if quantified and (has_time or has_target or has_agency):
        score = max(score, 4)
        reasons.append("量化指标同时具备时间、对象或责任主体")
    if quantified and has_time and has_target and has_agency and MAJOR_PATTERN.search(text):
        score = 5
        reasons.append("重大或专项措施同时具备完整实施要素")
    elif not quantified and MAJOR_PATTERN.search(text) and has_time and has_agency:
        score = max(score, 4)
        reasons.append("重大或专项措施明确时间和责任主体")
    return score, reasons


def _specificity_score(measure: dict[str, Any]) -> tuple[int, list[str]]:
    text = measure.get("measure_text") or ""
    has_quantity = bool(measure.get("quantities"))
    has_time = bool(measure.get("time_scope") or measure.get("start_date") or measure.get("end_date"))
    has_target = bool(measure.get("policy_target"))
    has_agency = bool(measure.get("implementing_agencies"))
    explicit_stage = measure.get("implementation_stage") in {
        "requested",
        "allocated",
        "disbursed",
        "completed",
        "verified",
    }
    reasons: list[str] = []

    if DIRECTIONAL_PATTERN.search(text) and not any(
        (has_quantity, has_time, has_target, has_agency)
    ):
        return 1, ["只有方向性表达，缺少结构化实施要素"]
    score = 2
    reasons.append("存在可识别的政策动作")
    if has_target or has_agency:
        score = 3
        reasons.append("明确作用对象或责任主体")
    if has_quantity or has_time or explicit_stage:
        score = 4
        reasons.append("包含数量、时间或明确实施结果")
    if has_quantity and has_time and has_target and has_agency:
        score = 5
        reasons.append("数量、时间、对象和责任主体齐全")
    return score, reasons


def _dedup_key(measure: dict[str, Any]) -> tuple[Any, ...]:
    quantity_signature = tuple(
        sorted(
            (
                item.get("dimension"),
                item.get("normalized_value"),
                item.get("normalized_unit"),
            )
            for item in measure.get("quantities", [])
        )
    )
    return (
        measure["document_id"],
        measure["policy_tool_type_id"],
        measure["implementation_stage"],
        _normalize_statement(measure.get("measure_text") or ""),
        quantity_signature,
        _normalize_statement(measure.get("time_scope") or ""),
    )


def _normalize_statement(value: str) -> str:
    value = re.sub(r"[\s，。；：、,.!！?？“”‘’（）()《》<>—-]+", "", value)
    return value.lower()


def _average(values: list[float | int]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0
