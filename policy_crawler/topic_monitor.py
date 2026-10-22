from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import httpx
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

SEARCH_API = "https://www.fjyc.gov.cn/ssp/search/api/search"
SITE_ID = "000000008ab5b860018ab75dc02e0065"
TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class TopicProfile:
    key: str
    name: str
    queries: tuple[str, ...]
    subject_terms: tuple[str, ...]
    context_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    minimum_relevance_score: int


def load_topic_profile(topic: str, topics_dir: Path = Path("config/topics")) -> TopicProfile:
    if not re.fullmatch(r"[a-z0-9_-]+", topic):
        raise ValueError("主题名称只能包含小写字母、数字、下划线和连字符")
    path = topics_dir / f"{topic}.json"
    if not path.is_file():
        available = ", ".join(sorted(item.stem for item in topics_dir.glob("*.json")))
        raise FileNotFoundError(f"找不到主题配置 {path}；可用主题：{available or '无'}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    required_lists = ("queries", "subject_terms", "context_terms", "exclude_terms")
    for field in required_lists:
        if not isinstance(raw.get(field), list):
            raise ValueError(f"{path} 的 {field} 必须是 JSON 数组")
    if not raw.get("name") or not raw["queries"] or not raw["subject_terms"]:
        raise ValueError(f"{path} 缺少 name、queries 或 subject_terms")

    return TopicProfile(
        key=topic,
        name=str(raw["name"]),
        queries=tuple(map(str, raw["queries"])),
        subject_terms=tuple(map(str, raw["subject_terms"])),
        context_terms=tuple(map(str, raw["context_terms"])),
        exclude_terms=tuple(map(str, raw["exclude_terms"])),
        minimum_relevance_score=int(raw.get("minimum_relevance_score", 45)),
    )


def _plain_text(value: str | None) -> str:
    without_tags = re.sub(r"<[^>]+>", "", html.unescape(value or ""))
    return re.sub(r"\s+", " ", without_tags).strip()


def _kind(row: dict[str, Any]) -> str:
    source_type = row.get("sourceType") or ""
    template = row.get("template") or row.get("ssp_type") or ""
    if source_type == "政策文件" or template == "SSP_DOCUMENT_ZC":
        return "policy"
    if source_type == "新闻动态" or template == "SSP_DOCUMENT":
        return "news"
    if source_type == "政务公开" or template == "SSP_ZWGK":
        return "public_info"
    return "other"


def _candidate_from_row(
    row: dict[str, Any], profile: TopicProfile, matched_queries: list[str]
) -> dict[str, Any] | None:
    title = _plain_text(row.get("doctitle") or row.get("_doctitle"))
    snippet = _plain_text(row.get("doccontent") or row.get("docabstract"))
    combined = f"{title} {snippet}"
    if any(term in combined for term in profile.exclude_terms):
        return None

    subject_hits = sorted({term for term in profile.subject_terms if term in combined})
    context_hits = sorted({term for term in profile.context_terms if term in combined})
    if not subject_hits or not context_hits:
        return None

    title_subject = any(term in title for term in profile.subject_terms)
    title_context = any(term in title for term in profile.context_terms)
    kind = _kind(row)
    kind_bonus = {"policy": 20, "public_info": 10, "news": 5, "other": 0}[kind]
    score = (
        (30 if title_subject else 15)
        + (15 if title_context else 10)
        + kind_bonus
        + min(len(set(matched_queries)) * 3, 12)
    )
    source_url = row.get("docpuburl") or row.get("chnldocurl")
    if not source_url:
        return None

    return {
        "topic_key": profile.key,
        "topic": profile.name,
        "document_id": str(row.get("docid") or row.get("ssp_id") or ""),
        "title": title,
        "source_url": source_url,
        "published_at": row.get("docreltime") or row.get("pubdate") or "",
        "source_type": row.get("sourceType") or "",
        "kind": kind,
        "template": row.get("template") or row.get("ssp_type") or "",
        "document_number": row.get("fileno") or "",
        "publisher": row.get("puborg") or "",
        "channel": row.get("chnlname") or "",
        "snippet": snippet,
        "matched_queries": sorted(set(matched_queries)),
        "subject_hits": subject_hits,
        "context_hits": context_hits,
        "relevance_score": score,
    }


def search_candidates(
    profile: TopicProfile,
    start_date: date,
    end_date: date,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    rows_by_url: dict[str, dict[str, Any]] = {}
    queries_by_url: dict[str, list[str]] = {}
    headers = {"User-Agent": "policy-crawler/0.1 (+public government search)"}

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        for query in profile.queries:
            page = 1
            while True:
                payload = {
                    "siteId": SITE_ID,
                    "mainSiteId": SITE_ID,
                    "keyWord": query,
                    "page": page,
                    "rows": page_size,
                    "sourceType": "SSP_ZHSS",
                    "sortFiled": "-docreltime",
                    "fullKey": "N",
                    "type": "0",
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                }
                response = client.post(SEARCH_API, data=payload)
                response.raise_for_status()
                body = response.json()
                if body.get("error"):
                    raise RuntimeError(f"永春县搜索接口返回错误：{body}")

                page_rows = body.get("datas") or []
                total = int((body.get("countDatas") or {}).get("recordcount") or 0)
                for row in page_rows:
                    url = row.get("docpuburl") or row.get("chnldocurl")
                    if not url:
                        continue
                    queries_by_url.setdefault(url, []).append(query)
                    previous = rows_by_url.get(url)
                    if previous is None or len(row.get("doccontent") or "") > len(previous.get("doccontent") or ""):
                        rows_by_url[url] = row

                if not page_rows or page * page_size >= total:
                    break
                page += 1
                time.sleep(0.3)
            time.sleep(0.3)

    candidates = []
    for url, row in rows_by_url.items():
        candidate = _candidate_from_row(row, profile, queries_by_url[url])
        if candidate is not None and candidate["relevance_score"] >= profile.minimum_relevance_score:
            candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda item: (item["relevance_score"], item["published_at"]),
        reverse=True,
    )


def _markdown_text(result: Any) -> str:
    markdown = result.markdown
    return str(getattr(markdown, "raw_markdown", markdown) or "")


def _normalized_url(value: str | None) -> str:
    """Return a stable URL key without query strings, fragments, or trailing slashes."""
    if not value:
        return ""
    parsed = urlparse(value)
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        )
    )


def _article_index(value: str | None) -> str:
    """Extract the stable article index from an fjyc.gov.cn detail-page URL."""
    if not value:
        return ""
    filename = Path(urlparse(value).path).name
    match = re.search(r"(?:_|^)(\d+)\.s?html?$", filename, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _pair_candidates_with_results(
    candidates: list[dict[str, Any]], results: list[Any]
) -> list[tuple[dict[str, Any], Any]]:
    """Match concurrent crawl results by article index/URL, never by return order."""
    by_index: dict[str, dict[str, Any]] = {}
    by_url: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        candidate_url = str(candidate["source_url"])
        index = str(candidate.get("document_id") or _article_index(candidate_url)).strip()
        url_key = _normalized_url(candidate_url)
        if index:
            if index in by_index:
                raise RuntimeError(f"候选文章 index 重复：{index}")
            by_index[index] = candidate
        if url_key:
            if url_key in by_url:
                raise RuntimeError(f"候选 URL 重复：{candidate_url}")
            by_url[url_key] = candidate

    matched: dict[str, Any] = {}
    for result in results:
        result_urls = [
            str(getattr(result, "url", "") or ""),
            str(getattr(result, "redirected_url", "") or ""),
        ]
        indexes = [_article_index(url) for url in result_urls]
        candidate = next((by_index[index] for index in indexes if index in by_index), None)
        if candidate is None:
            candidate = next(
                (by_url[key] for key in map(_normalized_url, result_urls) if key in by_url),
                None,
            )
        if candidate is None:
            actual = next((url for url in result_urls if url), "未知 URL")
            raise RuntimeError(f"抓取结果无法匹配候选文章：{actual}")

        candidate_url = str(candidate["source_url"])
        if candidate_url in matched:
            raise RuntimeError(f"同一候选文章收到多个抓取结果：{candidate_url}")
        matched[candidate_url] = result

    missing = [
        str(candidate["source_url"])
        for candidate in candidates
        if str(candidate["source_url"]) not in matched
    ]
    if missing:
        raise RuntimeError(f"以下候选文章缺少抓取结果：{', '.join(missing)}")
    return [(candidate, matched[str(candidate["source_url"])]) for candidate in candidates]


async def crawl_new_pages(
    candidates: list[dict[str, Any]], concurrency: int
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        check_robots_txt=True,
        excluded_tags=["nav", "footer", "form", "aside"],
        exclude_external_links=True,
        word_count_threshold=10,
        page_timeout=60_000,
        semaphore_count=concurrency,
        mean_delay=0.8,
        max_range=0.8,
    )
    urls = [candidate["source_url"] for candidate in candidates]
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun_many(urls=urls, config=run_config)

    updates: list[dict[str, Any]] = []
    for candidate, result in _pair_candidates_with_results(candidates, list(results)):
        crawled_at = datetime.now(TIMEZONE).isoformat(timespec="seconds")
        resolved_url = str(
            getattr(result, "redirected_url", "") or getattr(result, "url", "") or ""
        )
        if not result.success:
            updates.append(
                {
                    "source_url": candidate["source_url"], "status": "failed",
                    "resolved_url": resolved_url,
                    "crawled_at": crawled_at,
                    "error": result.error_message or "网页抓取失败",
                }
            )
            continue

        markdown = _markdown_text(result)
        raw_html = str(result.html or "")
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        header = (
            f"# {candidate['title']}\n\n"
            f"- URL: {candidate['source_url']}\n"
            f"- 发布时间: {candidate['published_at']}\n"
            f"- 类型: {candidate['source_type']}\n"
            f"- 发布机构: {candidate['publisher']}\n"
            f"- 文号: {candidate['document_number']}\n"
            f"- 抓取时间: {crawled_at}\n\n---\n\n"
        )
        updates.append(
            {
                "source_url": candidate["source_url"], "status": "crawled",
                "resolved_url": resolved_url,
                "crawled_at": crawled_at, "content_hash": content_hash,
                "markdown_content": header + markdown,
                "html_content": raw_html, "error": None,
            }
        )
    return updates
