from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Crawl4AI creates a cache/database directory during import. Keeping it inside
# the project makes local runs and containers self-contained.
load_dotenv()
runtime_base = Path(os.getenv("CRAWL4_AI_BASE_DIRECTORY", ".runtime")).resolve()
os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(runtime_base))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(runtime_base / "ms-playwright"))

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy


FIELD_HINTS = {
    "标题": "政策文件的完整标题",
    "发文机关": "发布或联合发布该文件的机构；多个机构用顿号分隔",
    "文号": "政策文号，例如国发〔2025〕1号；没有则为 null",
    "发布日期": "网页或文件明确标注的发布日期，统一为 YYYY-MM-DD",
    "生效日期": "政策明确规定的施行或生效日期，统一为 YYYY-MM-DD；没有则为 null",
    "有效状态": "有效、失效、废止、修订中或未知",
    "适用地区": "政策明确适用的行政区域",
    "政策主题": "用 2 到 5 个简短关键词概括，以顿号分隔",
    "摘要": "忠于原文的 100 至 200 字中文摘要，不加入推测",
}


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL 必须是完整的 http:// 或 https:// 地址")


def make_schema(fields: list[str]) -> dict[str, Any]:
    properties = {
        field: {
            "type": ["string", "null"],
            "description": FIELD_HINTS.get(field, f"从政策正文提取的“{field}”；原文没有则为 null"),
        }
        for field in fields
    }
    return {
        "title": "PolicyExtraction",
        "type": "object",
        "properties": properties,
        "required": fields,
        "additionalProperties": False,
    }


def _instruction(fields: list[str], requirements: str) -> str:
    return f"""
你是政府政策文件信息抽取助手。请只根据页面中的政策正文抽取一个政策文件。

规则：
1. 输出字段只能是：{', '.join(fields)}。
2. 不得猜测。原文没有明确说明的字段输出 null。
3. 忽略导航栏、推荐阅读、版权信息、搜索框和附件列表中的无关文字。
4. 如果页面是政策解读或新闻稿，而不是政策原文，仍按页面本身抽取，并在摘要中说明其性质。
5. 日期尽量统一成 YYYY-MM-DD；无法确定完整日期时保留原文。

用户补充需求：{requirements or '无'}
""".strip()


def _normalize_extracted(raw: str | None, fields: list[str]) -> dict[str, Any]:
    if not raw:
        raise RuntimeError("LLM 没有返回结构化内容")
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        candidates = [parsed]
    elif isinstance(parsed, list):
        candidates = [item for item in parsed if isinstance(item, dict)]
    else:
        candidates = []
    if not candidates:
        raise RuntimeError("LLM 返回内容中没有对象")

    # Chunked extraction may return more than one object. Prefer the object with
    # the most populated requested fields instead of silently merging guesses.
    best = max(candidates, key=lambda item: sum(item.get(field) not in (None, "") for field in fields))
    return {field: best.get(field) for field in fields}


def _build_crawl_components(
    fields: list[str],
    requirements: str,
    provider: str,
    api_key: str | None,
    base_url: str | None = None,
) -> tuple[BrowserConfig, CrawlerRunConfig, LLMExtractionStrategy]:
    llm_config = LLMConfig(provider=provider, api_token=api_key or None, base_url=base_url)
    strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=make_schema(fields),
        extraction_type="schema",
        instruction=_instruction(fields, requirements),
        chunk_token_threshold=3000,
        overlap_rate=0.1,
        apply_chunking=True,
        input_format="markdown",
        extra_args={"temperature": 0},
    )
    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        check_robots_txt=True,
        excluded_tags=["nav", "footer", "form", "aside"],
        exclude_external_links=True,
        word_count_threshold=10,
        page_timeout=60_000,
        extraction_strategy=strategy,
    )
    return browser_config, run_config, strategy


def _record_from_result(result: Any, url: str, fields: list[str]) -> dict[str, Any]:
    if not result.success:
        raise RuntimeError(result.error_message or "网页抓取失败")
    record = _normalize_extracted(result.extracted_content, fields)
    timezone = ZoneInfo(os.getenv("TIMEZONE", "Asia/Shanghai"))
    return {
        **record,
        "source_url": url,
        "crawled_at": datetime.now(timezone).isoformat(timespec="seconds"),
    }


async def crawl_and_extract(
    url: str,
    fields: list[str],
    requirements: str,
    provider: str,
    api_key: str | None,
    base_url: str | None = None,
) -> dict[str, Any]:
    validate_url(url)
    browser_config, run_config, strategy = _build_crawl_components(
        fields, requirements, provider, api_key, base_url
    )

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
        return _record_from_result(result, url, fields)
    finally:
        strategy.show_usage()


async def crawl_many_and_extract(
    urls: list[str],
    fields: list[str],
    requirements: str,
    provider: str,
    api_key: str | None,
    base_url: str | None = None,
    concurrency: int = 2,
) -> list[dict[str, Any]]:
    for url in urls:
        validate_url(url)

    browser_config, run_config, strategy = _build_crawl_components(
        fields, requirements, provider, api_key, base_url
    )
    # arun_many reads these values when it builds its adaptive dispatcher.
    run_config.semaphore_count = concurrency
    run_config.mean_delay = 0.8
    run_config.max_range = 0.8

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            crawl_results = await crawler.arun_many(urls=urls, config=run_config)

        rows: list[dict[str, Any]] = []
        for requested_url, result in zip(urls, crawl_results, strict=True):
            source_url = str(getattr(result, "url", "") or requested_url)
            empty_fields = {field: None for field in fields}
            try:
                record = _record_from_result(result, source_url, fields)
                rows.append({"status": "success", "error": None, **record})
            except Exception as exc:
                rows.append(
                    {
                        "status": "failed",
                        "error": str(exc),
                        **empty_fields,
                        "source_url": source_url,
                        "crawled_at": datetime.now(
                            ZoneInfo(os.getenv("TIMEZONE", "Asia/Shanghai"))
                        ).isoformat(timespec="seconds"),
                    }
                )
        return rows
    finally:
        strategy.show_usage()


def _safe_stem(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.hostname}-{parsed.path.strip('/').split('/')[-1] or 'index'}"
    return re.sub(r"[^0-9A-Za-z._-]+", "-", raw).strip("-")[:80] or "policy"


def save_record(record: dict[str, Any], output_dir: Path, formats: set[str]) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{timestamp}-{_safe_stem(str(record['source_url']))}"
    paths: list[Path] = []

    if "json" in formats:
        path = output_dir / f"{stem}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(path)

    if "csv" in formats:
        path = output_dir / f"{stem}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(record))
            writer.writeheader()
            writer.writerow(record)
        paths.append(path)

    return paths


def save_batch(records: list[dict[str, Any]], output_dir: Path, formats: set[str]) -> list[Path]:
    if not records:
        raise ValueError("没有可保存的批量结果")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{timestamp}-policy-batch"
    paths: list[Path] = []

    if "json" in formats:
        path = output_dir / f"{stem}.json"
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(path)

    if "csv" in formats:
        path = output_dir / f"{stem}.csv"
        fieldnames = list(dict.fromkeys(key for record in records for key in record))
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
        paths.append(path)

    return paths
