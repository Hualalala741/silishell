from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx

from .policy_scoring import aggregate_policy_scores, score_and_deduplicate_measures
from .topic_monitor import TopicProfile


DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_LLM_MODEL = "qwen3.5-plus-2026-04-20"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_REGION = "福建省泉州市永春县"

INDUSTRY_SEGMENTS = (
    "竹种与竹林资源培育",
    "竹林经营与生态保护",
    "竹材采伐、运输与收储",
    "竹材初加工",
    "笋竹精深加工",
    "竹基材料与制品制造",
    "流通、市场与应用",
    "竹文旅与产业融合",
)

POLICY_TOOL_TYPES = {
    1: ("FISCAL_SUPPORT", "财政支持"),
    2: ("FINANCIAL_SUPPORT", "金融支持"),
    3: ("LAND_FOREST_SUPPORT", "土地与林地保障"),
    4: ("INFRASTRUCTURE", "基础设施建设"),
    5: ("TECH_STANDARD", "技术创新与标准"),
    6: ("TALENT", "人才培养"),
    7: ("MARKET_APPLICATION", "市场推广与应用示范"),
    8: ("ENTERPRISE_ORGANIZATION", "企业、园区与产业组织"),
    9: ("RESOURCE_ECOLOGY", "资源培育与生态管护"),
    10: ("ADMIN_SERVICE", "行政审批与政务服务"),
    99: ("OTHER", "其他"),
}

RECORD_TYPES = {"target", "policy_action", "implementation", "result"}
IMPLEMENTATION_STAGES = {
    "planned",
    "requested",
    "allocated",
    "disbursed",
    "ongoing",
    "completed",
    "verified",
    "not_applicable",
    "unknown",
}

STAGE_EVIDENCE_PATTERNS = {
    "requested": re.compile(r"争取|申请|申报|拟申报"),
    "allocated": re.compile(
        r"(?:资金|经费|预算|补助).{0,30}(?:下达|安排|分解|核定|批复)|"
        r"(?:下达|安排|分解|核定|批复).{0,30}(?:资金|经费|预算|补助|金额)"
    ),
    "disbursed": re.compile(
        r"(?:已|已经|实际)(?:完成)?(?:拨付|发放|支付)|"
        r"(?:拨付|发放|支付)(?:工作)?(?:已经)?完成|资金到账"
    ),
    "verified": re.compile(r"验收合格|经核验|通过验收|审核确认"),
}
QUANTITY_DIMENSIONS = {
    "金额",
    "面积",
    "长度",
    "重量",
    "产能",
    "数量",
    "比例",
    "增长率",
    "产值",
    "时间",
    "其他",
}

UNIT_NORMALIZATION = {
    "金额": {
        "元": (1.0, "元"),
        "万元": (10_000.0, "元"),
        "亿元": (100_000_000.0, "元"),
    },
    "产值": {
        "元": (1.0, "元"),
        "万元": (10_000.0, "元"),
        "亿元": (100_000_000.0, "元"),
    },
    "面积": {
        "平方米": (0.0015, "亩"),
        "亩": (1.0, "亩"),
        "万亩": (10_000.0, "亩"),
        "公顷": (15.0, "亩"),
        "万公顷": (150_000.0, "亩"),
    },
    "长度": {
        "米": (0.001, "公里"),
        "公里": (1.0, "公里"),
        "千米": (1.0, "公里"),
    },
    "重量": {
        "吨": (1.0, "吨"),
        "万吨": (10_000.0, "吨"),
        "千克": (0.001, "吨"),
        "公斤": (0.001, "吨"),
    },
    "产能": {
        "吨": (1.0, "吨"),
        "万吨": (10_000.0, "吨"),
        "千克": (0.001, "吨"),
        "公斤": (0.001, "吨"),
    },
}

END_MARKERS = (
    "扫一扫在手机上查看当前页面",
    "相关解读",
    "泉州县（市、区）",
    "## 通知",
)


@dataclass(frozen=True, slots=True)
class DocumentSource:
    document_id: str
    title: str
    source_url: str
    published_at: str
    source_type: str
    publisher: str
    document_number: str
    markdown_path: Path
    body: str
    remapped: bool


@dataclass(frozen=True, slots=True)
class Section:
    section_id: str
    document_id: str
    section_path: tuple[str, ...]
    text: str


class DashScopeClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 120.0):
        if not api_key:
            raise ValueError("缺少 DASHSCOPE_API_KEY 或 LLM_API_KEY")
        self.client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self.client.close()

    def embeddings(
        self,
        texts: list[str],
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int = 1024,
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in _batched(texts, 10):
            response = self.client.post(
                "embeddings",
                json={
                    "model": _model_name(model),
                    "input": batch,
                    "dimensions": dimensions,
                    "encoding_format": "float",
                },
            )
            response.raise_for_status()
            payload = response.json()
            rows = sorted(payload["data"], key=lambda item: item["index"])
            vectors.extend(row["embedding"] for row in rows)
        return vectors

    def extract_measures(self, prompt: str, model: str = DEFAULT_LLM_MODEL) -> dict[str, Any]:
        response = self.client.post(
            "chat/completions",
            json={
                "model": _model_name(model),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是严谨的中国政府政策文本信息抽取助手。"
                            "只能依据用户提供的原文抽取，不得补充常识、推测或改写证据。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return _parse_json_object(content)


def _model_name(value: str) -> str:
    return value.split("/", 1)[1] if value.startswith("dashscope/") else value


def _batched(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _normalize_title(value: str) -> str:
    value = re.sub(r"[*_`#]", "", value)
    value = re.sub(r"\s+", "", value)
    return value.strip("：:。")


def _body_title_line_index(markdown: str, title: str) -> int | None:
    target = _normalize_title(title)
    # Skip the synthetic H1 written by the crawler, but allow compact pages
    # whose real article title appears before line 10.
    for index, line in enumerate(markdown.splitlines()[1:], start=1):
        if _normalize_title(line) == target:
            return index
    return None


def _strip_markdown_noise(value: str) -> str:
    value = re.sub(r"!\[[^]]*]\([^)]*\)", "", value)
    value = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", value)
    value = re.sub(r"[_`]+", "", value)
    value = value.replace("", "").replace("", "").replace("", "").replace("", "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def extract_article_body(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    start = _body_title_line_index(markdown, title)
    if start is None:
        raise ValueError(f"正文中找不到标题：{title}")
    body_lines = lines[start + 1 :]
    while body_lines and (
        not body_lines[0].strip()
        or body_lines[0].strip().startswith("时间：")
        or re.fullmatch(r"[* _-]+", body_lines[0].strip())
    ):
        body_lines.pop(0)

    end = len(body_lines)
    for index, line in enumerate(body_lines):
        if any(marker in line for marker in END_MARKERS):
            end = index
            break
    return _strip_markdown_noise("\n".join(body_lines[:end]))


def load_document_sources(pages_dir: Path) -> tuple[list[DocumentSource], list[str]]:
    manifest_path = pages_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    markdown_files = sorted(pages_dir.glob("*.md"))
    markdown_by_path = {path: path.read_text(encoding="utf-8") for path in markdown_files}
    used_paths: set[Path] = set()
    warnings: list[str] = []
    documents: list[DocumentSource] = []

    for candidate in manifest.get("candidates", []):
        title = str(candidate["title"])
        expected_path = pages_dir / f"{candidate['document_id']}.md"
        matches = [
            path
            for path, markdown in markdown_by_path.items()
            if path not in used_paths and _body_title_line_index(markdown, title) is not None
        ]
        if not matches:
            raise ValueError(f"没有找到与 manifest 标题匹配的正文：{title}")
        source_path = expected_path if expected_path in matches else matches[0]
        used_paths.add(source_path)
        remapped = source_path != expected_path
        if remapped:
            warnings.append(
                f"文档 {candidate['document_id']} 的正文来自 {source_path.name}，"
                f"而不是预期的 {expected_path.name}"
            )
        markdown = markdown_by_path[source_path]
        documents.append(
            DocumentSource(
                document_id=str(candidate["document_id"]),
                title=title,
                source_url=str(candidate.get("source_url") or ""),
                published_at=str(candidate.get("published_at") or ""),
                source_type=str(candidate.get("source_type") or ""),
                publisher=str(candidate.get("publisher") or ""),
                document_number=str(candidate.get("document_number") or ""),
                markdown_path=source_path,
                body=extract_article_body(markdown, title),
                remapped=remapped,
            )
        )
    return documents, warnings


def _heading(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    markdown_match = re.fullmatch(r"(#{1,6})\s+(.+)", stripped)
    if markdown_match:
        return len(markdown_match.group(1)), _strip_markdown_noise(markdown_match.group(2))
    bold_match = re.fullmatch(r"\*\*(.+)\*\*", stripped)
    if bold_match:
        text = _strip_markdown_noise(bold_match.group(1))
        if len(text) <= 80:
            level = 1 if re.match(r"^[一二三四五六七八九十]+、", text) else 2
            return level, text
    return None


def _sentence_chunks(text: str, max_chars: int = 1100) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    sentences = [part.strip() for part in re.split(r"(?<=[。！？；])", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars))
        elif current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    return chunks


def split_sections(document: DocumentSource) -> list[Section]:
    headings: list[str] = [document.title]
    paragraphs: list[tuple[tuple[str, ...], str]] = []
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            paragraphs.append((tuple(headings), text))
        buffer.clear()

    for line in document.body.splitlines():
        detected = _heading(line)
        if detected:
            flush()
            level, title = detected
            headings[:] = headings[:level]
            headings.append(title)
            continue
        if not line.strip():
            flush()
        else:
            buffer.append(line.strip())
    flush()

    sections: list[Section] = []
    index = 1
    for path, paragraph in paragraphs:
        for chunk in _sentence_chunks(paragraph):
            if len(chunk) < 20:
                continue
            sections.append(
                Section(
                    section_id=f"{document.document_id}_S{index:03d}",
                    document_id=document.document_id,
                    section_path=path,
                    text=chunk,
                )
            )
            index += 1
    return sections


def retrieval_queries(profile: TopicProfile) -> list[str]:
    base_terms = "、".join(dict.fromkeys((*profile.subject_terms, *profile.context_terms)))
    return [
        f"{profile.name}政策措施，涉及{base_terms}",
        "竹种选育、丰产竹林、竹林提质、竹林经营管护和生态保护",
        "竹材采伐、竹山流转、生产道路、机耕路、竹材运输、仓储和收储",
        "竹山分解点、竹材切段剖分拉丝、笋竹初加工和精深加工",
        "竹纤维、竹基复合材料、竹制品、竹家具、竹炭、生物降解材料和以竹代塑",
        "竹产业财政补助、金融支持、林地审批、基础设施、技术标准、人才培训",
        "竹产品市场推广、应用示范、招商引资、产业园区、产业协会和品牌建设",
        "竹编非遗、竹文化、竹艺展示、竹博园、研学旅游和产业融合",
    ]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def select_relevant_sections(
    sections: list[Section],
    section_vectors: list[list[float]],
    query_vectors: list[list[float]],
    profile: TopicProfile,
    threshold: float,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    subject_terms = tuple(profile.subject_terms)
    for section, vector in zip(sections, section_vectors, strict=True):
        score = max(cosine_similarity(vector, query) for query in query_vectors)
        lexical_hits = sorted({term for term in subject_terms if term in section.text})
        if lexical_hits or score >= threshold:
            selected.append(
                {
                    "section_id": section.section_id,
                    "document_id": section.document_id,
                    "section_path": list(section.section_path),
                    "text": section.text,
                    "similarity_score": round(score, 6),
                    "lexical_hits": lexical_hits,
                }
            )
    return selected


def extraction_prompt(document: DocumentSource, sections: list[dict[str, Any]]) -> str:
    tools = ", ".join(f"{key}={value[1]}" for key, value in POLICY_TOOL_TYPES.items())
    payload = [
        {
            "section_id": item["section_id"],
            "section_path": item["section_path"],
            "text": item["text"],
        }
        for item in sections
    ]
    return f"""
请从下面与“竹产业”相关的政策原文中抽取原子化 measure，输出严格 JSON 对象：
{{"measures": [{{...}}]}}。

文档：{document.title}
发布日期：{document.published_at}
发布机构：{document.publisher or '原文未明确'}

每条 measure 必须包含：
- section_id
- industry_segments: 数组，只能选：{json.dumps(INDUSTRY_SEGMENTS, ensure_ascii=False)}
- measure_text: 一个主要动作、一种实施状态的简洁陈述
- evidence_text: 必须逐字复制自对应 section 的 text，不得改写
- record_type: target / policy_action / implementation / result
- implementation_stage: planned / requested / allocated / disbursed / ongoing / completed / verified / not_applicable / unknown
- policy_tool_type_id: 一个整数，映射为 {tools}
- secondary_policy_tool_type_id: 整数或 null
- policy_target: 字符串数组
- implementing_agencies: 字符串数组
- time_scope: 原文时间表达或 null
- start_date: YYYY-MM-DD 或 null；原文不明确不得猜测
- end_date: YYYY-MM-DD 或 null；原文不明确不得猜测
- quantities: 数组，每项包含 metric、raw_text、value、unit、dimension、normalized_value、normalized_unit

抽取规则：
1. 只抽取与竹产业直接相关的内容；仅泛泛提到林业、其他产业时不要抽取。
2. 同一句中若有多个独立动作，拆成多条 measure。
3. 同一句中若同时有计划值和完成值，按状态拆成多条 measure。
4. “争取资金”是 requested，不是 disbursed；“下达、安排、分解资金”是 allocated；只有明确“已拨付、已发放、已支付”才是 disbursed。
5. “正在推进”是 ongoing；“已完成、已建成、成功引进、已流转”是 completed；“验收合格、经核验”是 verified。
6. 长期审批规则或适用条件使用 not_applicable。无法确定时使用 unknown，禁止猜测。
7. quantities 不保存实施状态。dimension 只能选：{json.dumps(sorted(QUANTITY_DIMENSIONS), ensure_ascii=False)}。
8. 没有 measure 时输出 {{"measures": []}}。

原文 sections：
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


def _parse_json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    return parsed


def validate_and_complete_measures(
    raw: dict[str, Any],
    document: DocumentSource,
    selected_sections: list[dict[str, Any]],
    topic: str,
    region: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    section_by_id = {item["section_id"]: item for item in selected_sections}
    warnings: list[str] = []
    valid: list[dict[str, Any]] = []
    candidates = raw.get("measures")
    if not isinstance(candidates, list):
        return [], [f"{document.document_id}: LLM 输出缺少 measures 数组"]

    for position, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            warnings.append(f"{document.document_id}: 第 {position} 条 measure 不是对象")
            continue
        section = section_by_id.get(str(item.get("section_id") or ""))
        evidence = str(item.get("evidence_text") or "").strip()
        if section is None:
            warnings.append(f"{document.document_id}: 第 {position} 条引用未知 section")
            continue
        if not evidence or evidence not in section["text"]:
            matching_sections = [
                candidate
                for candidate in selected_sections
                if evidence and evidence in candidate["text"]
            ]
            if len(matching_sections) == 1:
                section = matching_sections[0]
                warnings.append(
                    f"{document.document_id}: 第 {position} 条 section_id 已纠正为 "
                    f"{section['section_id']}"
                )
            else:
                warnings.append(
                    f"{document.document_id}: 第 {position} 条 evidence_text 不是原文逐字片段"
                )
                continue
        evidence = _expand_evidence_to_sentence(section["text"], evidence)
        record_type = str(item.get("record_type") or "unknown")
        stage = str(item.get("implementation_stage") or "unknown")
        if record_type not in RECORD_TYPES:
            inferred_record_type = {
                "planned": "target",
                "requested": "policy_action",
                "allocated": "implementation",
                "disbursed": "result",
                "ongoing": "implementation",
                "completed": "result",
                "verified": "result",
            }.get(record_type)
            if inferred_record_type is None:
                warnings.append(f"{document.document_id}: 非法 record_type={record_type}，已跳过")
                continue
            warnings.append(
                f"{document.document_id}: record_type={record_type} 已纠正为 {inferred_record_type}"
            )
            record_type = inferred_record_type
        if stage not in IMPLEMENTATION_STAGES:
            warnings.append(f"{document.document_id}: 非法 implementation_stage={stage}，已改为 unknown")
            stage = "unknown"
        stage, stage_warning = _guard_strong_stage(stage, evidence)
        if stage_warning:
            warnings.append(f"{document.document_id}: 第 {position} 条 {stage_warning}")
        tool_id = int(item.get("policy_tool_type_id") or 99)
        if tool_id not in POLICY_TOOL_TYPES:
            tool_id = 99
        secondary = item.get("secondary_policy_tool_type_id")
        if secondary is not None:
            secondary = int(secondary)
            if secondary not in POLICY_TOOL_TYPES or secondary == tool_id:
                secondary = None
        segments = [
            value for value in item.get("industry_segments", []) if value in INDUSTRY_SEGMENTS
        ]
        if not segments:
            warnings.append(f"{document.document_id}: 第 {position} 条缺少合法 industry_segments")
            continue
        raw_quantities = item.get("quantities")
        quantities = _validate_quantities(raw_quantities, evidence)
        if isinstance(raw_quantities, list) and len(quantities) < len(raw_quantities):
            warnings.append(
                f"{document.document_id}: 第 {position} 条有数量因 raw_text 不在 evidence_text 中被移除"
            )
        measure_text = str(item.get("measure_text") or evidence).strip()
        fingerprint = hashlib.sha1(
            (
                f"{document.document_id}|{section['section_id']}|{evidence}|"
                f"{measure_text}|{stage}|{tool_id}"
            ).encode("utf-8")
        ).hexdigest()[:12]
        valid.append(
            {
                "measure_id": f"M_{fingerprint}",
                "document_id": document.document_id,
                "section_id": section["section_id"],
                "section_path": section["section_path"],
                "source_url": document.source_url,
                "region": region,
                "industry_topic": topic,
                "industry_segments": segments,
                "measure_text": measure_text,
                "evidence_text": evidence,
                "record_type": record_type,
                "implementation_stage": stage,
                "policy_tool_type_id": tool_id,
                "secondary_policy_tool_type_id": secondary,
                "policy_target": _string_list(item.get("policy_target")),
                "implementing_agencies": _string_list(item.get("implementing_agencies")),
                "time_scope": item.get("time_scope") or None,
                "start_date": item.get("start_date") or None,
                "end_date": item.get("end_date") or None,
                "quantities": quantities,
            }
        )
    return valid, warnings


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _guard_strong_stage(stage: str, evidence: str) -> tuple[str, str | None]:
    pattern = STAGE_EVIDENCE_PATTERNS.get(stage)
    if pattern is None or pattern.search(evidence):
        return stage, None
    if re.search(r"正在|稳步推进|加快推进|持续推进|已启动|目前.{0,10}(?:推进|开展|建设)", evidence):
        fallback = "ongoing"
    elif re.search(r"将|拟|计划|负责|应当|参照", evidence):
        fallback = "planned"
    else:
        fallback = "unknown"
    return fallback, f"{stage} 缺少明确原文信号，已降级为 {fallback}"


def _expand_evidence_to_sentence(text: str, evidence: str) -> str:
    start = text.find(evidence)
    if start < 0:
        return evidence
    left = max(text.rfind(mark, 0, start) for mark in ("。", "！", "？", "\n")) + 1
    evidence_end = start + len(evidence)
    right_candidates = [
        position + 1
        for mark in ("。", "！", "？", "\n")
        if (position := text.find(mark, evidence_end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    sentence = text[left:right].strip()
    return sentence if sentence else evidence


def _validate_quantities(value: Any, evidence: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or not item.get("raw_text"):
            continue
        raw_text = str(item["raw_text"]).strip()
        if not raw_text or raw_text not in evidence:
            continue
        dimension = str(item.get("dimension") or "其他")
        if dimension not in QUANTITY_DIMENSIONS:
            dimension = "其他"
        unit = str(item.get("unit") or "").strip() or None
        numeric_value = _numeric_value(item.get("value"))
        normalized_value, normalized_unit = _normalize_quantity(
            numeric_value,
            unit,
            dimension,
            item.get("normalized_value"),
            item.get("normalized_unit"),
        )
        rows.append(
            {
                "metric": str(item.get("metric") or "未命名指标"),
                "raw_text": raw_text,
                "value": numeric_value if numeric_value is not None else item.get("value"),
                "unit": unit,
                "dimension": dimension,
                "normalized_value": normalized_value,
                "normalized_unit": normalized_unit,
            }
        )
    return rows


def _numeric_value(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _normalize_quantity(
    value: float | int | None,
    unit: str | None,
    dimension: str,
    model_value: Any,
    model_unit: Any,
) -> tuple[float | int | Any, str | Any]:
    conversion = UNIT_NORMALIZATION.get(dimension, {}).get(unit or "")
    if value is not None and conversion is not None:
        multiplier, target_unit = conversion
        result = round(float(value) * multiplier, 6)
        normalized: float | int = int(result) if result.is_integer() else result
        return normalized, target_unit
    return model_value, model_unit


def build_portrait(
    measures: list[dict[str, Any]], documents: list[DocumentSource], generated_at: str
) -> dict[str, Any]:
    measures = score_and_deduplicate_measures(measures)
    by_tool = Counter(POLICY_TOOL_TYPES[item["policy_tool_type_id"]][1] for item in measures)
    by_segment = Counter(segment for item in measures for segment in item["industry_segments"])
    by_stage = Counter(item["implementation_stage"] for item in measures)
    by_record = Counter(item["record_type"] for item in measures)
    quantitative = [
        {
            "measure_id": item["measure_id"],
            "document_id": item["document_id"],
            "implementation_stage": item["implementation_stage"],
            **quantity,
        }
        for item in measures
        for quantity in item["quantities"]
    ]
    return {
        "region": DEFAULT_REGION,
        "industry_topic": "竹产业",
        "generated_at": generated_at,
        "document_count": len(documents),
        "measure_count": len(measures),
        "coverage": {
            "policy_tools": dict(by_tool.most_common()),
            "industry_segments": dict(by_segment.most_common()),
            "implementation_stages": dict(by_stage.most_common()),
            "record_types": dict(by_record.most_common()),
        },
        "policy_scores": aggregate_policy_scores(measures, POLICY_TOOL_TYPES),
        "quantitative_indicators": quantitative,
        "measures": measures,
        "note": "数量指标逐条列示，未跨文档求和，以避免规划、进展和结果重复计算。",
    }


def portrait_markdown(portrait: dict[str, Any]) -> str:
    coverage = portrait["coverage"]
    scores = portrait["policy_scores"]
    lines = [
        "# 永春县竹产业政策画像",
        "",
        f"- 生成时间：{portrait['generated_at']}",
        f"- 文档数：{portrait['document_count']}",
        f"- 原子 measure 数：{portrait['measure_count']}",
        "",
        "## 政策工具分布",
        "",
    ]
    lines.extend(f"- {name}：{count}" for name, count in coverage["policy_tools"].items())
    lines.extend(["", "## 产业链环节分布", ""])
    lines.extend(f"- {name}：{count}" for name, count in coverage["industry_segments"].items())
    lines.extend(["", "## 实施阶段分布", ""])
    lines.extend(f"- `{name}`：{count}" for name, count in coverage["implementation_stages"].items())
    lines.extend(
        [
            "",
            "## 政策设计分（MVP）",
            "",
            f"- 等权诊断总分：**{scores['overall_unweighted_score_100']} / 100**",
            f"- 聚合口径：{scores['aggregation_method']}",
            f"- 聚合纳入 measure：{scores['raw_primary_measure_count']}；排除高置信重复：{scores['excluded_duplicate_count']}",
            "",
            "| 政策工具 | 维度分 | Measure数 | 原始设计分总和 | 平均设计分 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for item in scores["dimensions"]:
        lines.append(
            f"| {item['policy_tool_name']} | {item['dimension_score_100']} | "
            f"{item['primary_measure_count']} | {item['raw_design_score_sum']} | "
            f"{item['average_design_score_100']} |"
        )
    lines.extend(
        [
            "",
            "> 该总分是等权的政策供给诊断分，不代表任何一家企业的实际可获得价值。",
        ]
    )
    lines.extend(
        [
            "",
            "## Measures",
            "",
            "| 状态 | 政策工具 | 力度 | 明确性 | 设计分 | 原子政策陈述 | 数量 | 来源文档 |",
            "|---|---|---:|---:|---:|---|---|---|",
        ]
    )
    for item in portrait["measures"]:
        quantities = "；".join(value["raw_text"] for value in item["quantities"]) or "—"
        tool_name = POLICY_TOOL_TYPES[item["policy_tool_type_id"]][1]
        text = item["measure_text"].replace("|", "｜").replace("\n", " ")
        lines.append(
            f"| `{item['implementation_stage']}` | {tool_name} | "
            f"{item['strength_score']} | {item['specificity_score']} | "
            f"{item['design_score_100']} | {text} | "
            f"{quantities} | {item['document_id']} |"
        )
    lines.extend(["", "> 数量指标未跨文档求和，避免把规划目标、执行进展和验收结果重复累计。", ""])
    return "\n".join(lines)


def run_portrait(
    pages_dir: Path,
    profile: TopicProfile,
    output_dir: Path,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    llm_model: str = DEFAULT_LLM_MODEL,
    embedding_dimensions: int = 1024,
    similarity_threshold: float = 0.42,
) -> dict[str, Any]:
    documents, source_warnings = load_document_sources(pages_dir)
    sections = [section for document in documents for section in split_sections(document)]
    client = DashScopeClient(api_key=api_key, base_url=base_url)
    try:
        queries = retrieval_queries(profile)
        query_vectors = client.embeddings(queries, embedding_model, embedding_dimensions)
        section_vectors = client.embeddings(
            [section.text for section in sections], embedding_model, embedding_dimensions
        )
        selected = select_relevant_sections(
            sections,
            section_vectors,
            query_vectors,
            profile,
            similarity_threshold,
        )

        selected_by_document: dict[str, list[dict[str, Any]]] = {}
        for item in selected:
            selected_by_document.setdefault(item["document_id"], []).append(item)

        measures: list[dict[str, Any]] = []
        raw_extractions: list[dict[str, Any]] = []
        extraction_warnings: list[str] = []
        for document in documents:
            relevant = selected_by_document.get(document.document_id, [])
            if not relevant:
                continue
            raw = client.extract_measures(extraction_prompt(document, relevant), llm_model)
            raw_extractions.append({"document_id": document.document_id, "response": raw})
            document_measures, warnings = validate_and_complete_measures(
                raw,
                document,
                relevant,
                profile.name,
                DEFAULT_REGION,
            )
            measures.extend(document_measures)
            extraction_warnings.extend(warnings)
    finally:
        client.close()

    now = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Shanghai"))).isoformat(
        timespec="seconds"
    )
    portrait = build_portrait(measures, documents, now)
    run_dir = output_dir / f"portrait-{pages_dir.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    document_rows = [
        {
            "document_id": item.document_id,
            "title": item.title,
            "source_url": item.source_url,
            "published_at": item.published_at,
            "source_type": item.source_type,
            "publisher": item.publisher,
            "document_number": item.document_number,
            "markdown_path": str(item.markdown_path),
            "remapped": item.remapped,
        }
        for item in documents
    ]
    _write_json(run_dir / "documents.json", document_rows)
    _write_json(
        run_dir / "sections.json",
        [
            {
                "section_id": item.section_id,
                "document_id": item.document_id,
                "section_path": list(item.section_path),
                "text": item.text,
            }
            for item in sections
        ],
    )
    _write_json(run_dir / "selected_sections.json", selected)
    _write_json(run_dir / "raw_extractions.json", raw_extractions)
    measures = portrait["measures"]
    _write_json(run_dir / "measures.json", measures)
    _write_json(run_dir / "portrait.json", portrait)
    _write_json(run_dir / "policy_scores.json", portrait["policy_scores"])
    _write_json(
        run_dir / "warnings.json",
        {"source_warnings": source_warnings, "extraction_warnings": extraction_warnings},
    )
    (run_dir / "portrait.md").write_text(portrait_markdown(portrait), encoding="utf-8")
    return {
        "output_dir": str(run_dir.resolve()),
        "document_count": len(documents),
        "section_count": len(sections),
        "selected_section_count": len(selected),
        "measure_count": len(measures),
        "source_warning_count": len(source_warnings),
        "extraction_warning_count": len(extraction_warnings),
    }


def rebuild_portrait_from_raw(
    pages_dir: Path,
    profile: TopicProfile,
    run_dir: Path,
) -> dict[str, Any]:
    documents, source_warnings = load_document_sources(pages_dir)
    sections = json.loads((run_dir / "sections.json").read_text(encoding="utf-8"))
    selected = json.loads((run_dir / "selected_sections.json").read_text(encoding="utf-8"))
    raw_rows = json.loads((run_dir / "raw_extractions.json").read_text(encoding="utf-8"))
    selected_by_document: dict[str, list[dict[str, Any]]] = {}
    for item in selected:
        selected_by_document.setdefault(item["document_id"], []).append(item)
    raw_by_document = {item["document_id"]: item["response"] for item in raw_rows}

    measures: list[dict[str, Any]] = []
    extraction_warnings: list[str] = []
    for document in documents:
        raw = raw_by_document.get(document.document_id)
        relevant = selected_by_document.get(document.document_id, [])
        if raw is None or not relevant:
            continue
        document_measures, warnings = validate_and_complete_measures(
            raw,
            document,
            relevant,
            profile.name,
            DEFAULT_REGION,
        )
        measures.extend(document_measures)
        extraction_warnings.extend(warnings)

    now = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Shanghai"))).isoformat(
        timespec="seconds"
    )
    portrait = build_portrait(measures, documents, now)
    measures = portrait["measures"]
    _write_json(run_dir / "measures.json", measures)
    _write_json(run_dir / "portrait.json", portrait)
    _write_json(run_dir / "policy_scores.json", portrait["policy_scores"])
    _write_json(
        run_dir / "warnings.json",
        {"source_warnings": source_warnings, "extraction_warnings": extraction_warnings},
    )
    (run_dir / "portrait.md").write_text(portrait_markdown(portrait), encoding="utf-8")
    return {
        "output_dir": str(run_dir.resolve()),
        "document_count": len(documents),
        "section_count": len(sections),
        "selected_section_count": len(selected),
        "measure_count": len(measures),
        "source_warning_count": len(source_warnings),
        "extraction_warning_count": len(extraction_warnings),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
