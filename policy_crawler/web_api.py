from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .site_selection import filter_candidates, rank_candidates
from .site_selection_storage import SiteSelectionRepository
from .storage import PolicyRepository
from .topic_monitor import TIMEZONE, TopicProfile, crawl_new_pages, search_candidates

load_dotenv()
logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOPICS_DIR = PROJECT_ROOT / "config" / "topics"


def _clean_terms(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _safe_requirement_name(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned[:60] or "未命名需求"


def _topic_key(explicit: str, requirement_name: str) -> str:
    """Prefer the template key, such as zhuchanye, over the Chinese display name."""
    value = explicit.strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", value):
        return value
    return _safe_requirement_name(requirement_name).lower()


class SearchRequest(BaseModel):
    topic_key: str = ""
    requirement_name: str = Field(min_length=1, max_length=100)
    queries: list[str]
    subject_terms: list[str]
    context_terms: list[str]
    exclude_terms: list[str] = Field(default_factory=list)
    minimum_relevance_score: Annotated[int, Field(ge=0, le=100)] = 45
    start_date: date
    end_date: date

    @field_validator("queries", "subject_terms", "context_terms")
    @classmethod
    def require_terms(cls, value: list[str]) -> list[str]:
        if not _clean_terms(value):
            raise ValueError("至少需要填写一个关键词")
        return value

    def to_profile(self) -> TopicProfile:
        return TopicProfile(
            key=_topic_key(self.topic_key, self.requirement_name),
            name=self.requirement_name.strip(),
            queries=_clean_terms(self.queries),
            subject_terms=_clean_terms(self.subject_terms),
            context_terms=_clean_terms(self.context_terms),
            exclude_terms=_clean_terms(self.exclude_terms),
            minimum_relevance_score=self.minimum_relevance_score,
        )


class Candidate(BaseModel):
    topic_key: str = ""
    topic: str = ""
    document_id: str = ""
    title: str
    source_url: str
    published_at: str = ""
    source_type: str = ""
    kind: str = "other"
    template: str = ""
    document_number: str = ""
    publisher: str = ""
    channel: str = ""
    snippet: str = ""
    matched_queries: list[str] = Field(default_factory=list)
    subject_hits: list[str] = Field(default_factory=list)
    context_hits: list[str] = Field(default_factory=list)
    relevance_score: int

    @field_validator("source_url")
    @classmethod
    def require_public_search_result(cls, value: str) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (
            host == "fjyc.gov.cn" or host.endswith(".fjyc.gov.cn")
        ):
            raise ValueError("只允许抓取永春县政府站点的候选页面")
        return value


class CrawlRequest(BaseModel):
    topic_key: str = ""
    requirement_name: str = Field(min_length=1, max_length=100)
    candidates: list[Candidate] = Field(min_length=1, max_length=200)
    concurrency: Annotated[int, Field(ge=1, le=5)] = 2
    queries: list[str] = Field(default_factory=list)
    subject_terms: list[str] = Field(default_factory=list)
    context_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    minimum_relevance_score: Annotated[int, Field(ge=0, le=100)] = 45
    start_date: date | None = None


class SiteSelectionWeights(BaseModel):
    cost: Annotated[float, Field(ge=0, le=100)] = 30
    market: Annotated[float, Field(ge=0, le=100)] = 30
    risk: Annotated[float, Field(ge=0, le=100)] = 25
    policy: Annotated[float, Field(ge=0, le=100)] = 15


class SiteSelectionRequest(BaseModel):
    resource_key: Literal["abandoned_cropland", "stable_natural_resource"]
    minimum_area_mu: Annotated[float, Field(gt=0, le=5000)] = 50
    weights: SiteSelectionWeights = Field(default_factory=SiteSelectionWeights)


INTERVAL_LIMITS = {"hours": (1, 168), "days": (1, 180), "weeks": (1, 26), "months": (1, 6)}
WEEKDAY_NAMES = "一二三四五六日"


class ScheduleRequest(BaseModel):
    topic_key: str = Field(min_length=1, max_length=80)
    kind: str
    interval_value: int | None = None
    interval_unit: str | None = None
    day_of_month: int | None = None
    weekday: int | None = None
    time: str | None = None

    def to_record(self) -> dict[str, Any]:
        kind = self.kind.strip()
        if kind not in {"interval", "daily", "monthly"}:
            raise ValueError("频率只能是固定间隔、每天或每月")
        clock = _clock(self.time) if kind != "interval" or self.interval_unit != "hours" else None
        if kind == "daily":
            if clock is None:
                raise ValueError("请填写每天的执行时刻")
            return {"schedule_kind": "daily", "hour": clock[0], "minute": clock[1]}
        if kind == "monthly":
            if clock is None or self.day_of_month is None:
                raise ValueError("请填写每月的日期和时刻")
            if not 1 <= self.day_of_month <= 28:
                raise ValueError("每月日期请选 1 到 28 日，这样每个月都会执行")
            return {
                "schedule_kind": "monthly",
                "day_of_month": self.day_of_month,
                "hour": clock[0],
                "minute": clock[1],
            }
        unit = self.interval_unit or ""
        if unit not in INTERVAL_LIMITS or self.interval_value is None:
            raise ValueError("请选择间隔单位和数值")
        low, high = INTERVAL_LIMITS[unit]
        if not low <= self.interval_value <= high:
            raise ValueError(f"这个间隔单位的范围是 {low} 到 {high}")
        record: dict[str, Any] = {
            "schedule_kind": "interval",
            "interval_value": self.interval_value,
            "interval_unit": unit,
        }
        if unit == "hours":
            return record
        if clock is None:
            raise ValueError("请填写执行时刻")
        record["hour"] = clock[0]
        record["minute"] = clock[1]
        if unit == "weeks":
            if self.weekday is None or not 0 <= self.weekday <= 6:
                raise ValueError("请选择星期")
            record["weekday"] = self.weekday
        return record


def _clock(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    matched = re.fullmatch(r"(\d{2}):(\d{2})", value.strip())
    if not matched:
        raise ValueError("时间格式应为 HH:MM")
    hour, minute = int(matched.group(1)), int(matched.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("时间格式应为 HH:MM")
    return hour, minute


def schedule_label(row: dict[str, Any]) -> str:
    kind = row.get("schedule_kind") or "daily"
    clock = ""
    if row.get("hour") is not None and row.get("minute") is not None:
        clock = f"{int(row['hour']):02d}:{int(row['minute']):02d}"
    if kind == "daily":
        return f"每天 {clock}"
    if kind == "monthly":
        return f"每月 {int(row['day_of_month'])} 日 {clock}"
    value = int(row["interval_value"])
    unit = row.get("interval_unit")
    if unit == "hours":
        return f"每 {value} 小时"
    if unit == "days":
        return f"每 {value} 天 {clock}"
    if unit == "weeks":
        return f"每 {value} 周的周{WEEKDAY_NAMES[int(row['weekday'])]} {clock}"
    return f"约每 {value} 个月 {clock}"


def _next_clock(now: datetime, hour: int, minute: int) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_weekday(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
    days_ahead = (weekday - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def schedule_trigger(row: dict[str, Any]):
    kind = row.get("schedule_kind") or "daily"
    if kind == "daily":
        return CronTrigger(hour=int(row["hour"]), minute=int(row["minute"]), timezone=TIMEZONE)
    if kind == "monthly":
        return CronTrigger(
            day=int(row["day_of_month"]), hour=int(row["hour"]), minute=int(row["minute"]), timezone=TIMEZONE
        )
    unit = row["interval_unit"]
    value = int(row["interval_value"])
    if unit == "hours":
        return IntervalTrigger(hours=value, timezone=TIMEZONE)
    now = datetime.now(TIMEZONE)
    hour, minute = int(row["hour"]), int(row["minute"])
    if unit == "days":
        return IntervalTrigger(days=value, start_date=_next_clock(now, hour, minute), timezone=TIMEZONE)
    if unit == "weeks":
        return IntervalTrigger(
            weeks=value,
            start_date=_next_weekday(now, int(row["weekday"]), hour, minute),
            timezone=TIMEZONE,
        )
    return IntervalTrigger(days=30 * value, start_date=_next_clock(now, hour, minute), timezone=TIMEZONE)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(TIMEZONE).isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _job_id(topic_key: str) -> str:
    return f"web-monitor-{topic_key}"


def _two_years_ago(today: date) -> date:
    try:
        return today.replace(year=today.year - 2)
    except ValueError:
        return today.replace(year=today.year - 2, day=28)


def _topic_file_profile(topic_key: str, name: str) -> dict[str, Any] | None:
    path = TOPICS_DIR / f"{topic_key}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    queries = [str(item) for item in raw.get("queries") or [] if str(item).strip()]
    subject_terms = [str(item) for item in raw.get("subject_terms") or [] if str(item).strip()]
    context_terms = [str(item) for item in raw.get("context_terms") or [] if str(item).strip()]
    if not queries or not subject_terms or not context_terms:
        return None
    return {
        "requirement_name": str(raw.get("name") or name),
        "queries": queries,
        "subject_terms": subject_terms,
        "context_terms": context_terms,
        "exclude_terms": [str(item) for item in raw.get("exclude_terms") or [] if str(item).strip()],
        "minimum_relevance_score": int(raw.get("minimum_relevance_score") or 45),
        "start_date": _two_years_ago(datetime.now(TIMEZONE).date()),
    }


def _stored_profile(row: dict[str, Any]) -> dict[str, Any]:
    start = row["start_date"]
    return {
        "requirement_name": row["requirement_name"],
        "queries": list(row["queries"] or []),
        "subject_terms": list(row["subject_terms"] or []),
        "context_terms": list(row["context_terms"] or []),
        "exclude_terms": list(row["exclude_terms"] or []),
        "minimum_relevance_score": int(row["minimum_relevance_score"]),
        "start_date": start if isinstance(start, date) else date.fromisoformat(str(start)),
    }


def resolve_profile(repository: PolicyRepository, topic_key: str, name: str) -> dict[str, Any] | None:
    stored = repository.get_project_profile(topic_key)
    if stored:
        return _stored_profile(stored)
    return _topic_file_profile(topic_key, name)


def _schedule_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row or not row["enabled"]:
        return None
    clock = None
    if row.get("hour") is not None and row.get("minute") is not None:
        clock = f"{int(row['hour']):02d}:{int(row['minute']):02d}"
    return {
        "enabled": True,
        "kind": row.get("schedule_kind") or "daily",
        "interval_value": row.get("interval_value"),
        "interval_unit": row.get("interval_unit"),
        "day_of_month": row.get("day_of_month"),
        "weekday": row.get("weekday"),
        "time": clock,
        "label": schedule_label(row),
        "last_run_at": _iso(row.get("last_run_at")),
        "last_status": row.get("last_status"),
        "last_message": row.get("last_message"),
    }


def _as_local_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(TIMEZONE)
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return date.fromisoformat(text[:10])
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(TIMEZONE)
    return parsed.date()


def scheduled_search_start(schedule: dict[str, Any], project: dict[str, Any], profile_start: date, today: date) -> date:
    """Search only from the previous run, so a schedule does not rescan the whole history."""
    start = (
        _as_local_date(schedule.get("last_run_at"))
        or _as_local_date(project.get("last_crawled_at"))
        or profile_start
    )
    return today if start > today else start


def execute_scheduled_crawl(topic_key: str) -> None:
    repository = PolicyRepository()
    try:
        schedule = repository.get_schedule(topic_key)
        if not schedule or not schedule["enabled"]:
            return
        projects = {item["topic_key"]: item for item in repository.list_crawled_projects()}
        project = projects.get(topic_key)
        if project is None:
            repository.record_schedule_run(topic_key, "failed", "项目已没有成功抓取的页面")
            return
        profile_data = resolve_profile(repository, topic_key, project["name"])
        if profile_data is None:
            repository.record_schedule_run(topic_key, "failed", "缺少检索条件，无法重复检索")
            return
        today = datetime.now(TIMEZONE).date()
        start = scheduled_search_start(schedule, project, profile_data["start_date"], today)
        profile = TopicProfile(
            key=topic_key,
            name=profile_data["requirement_name"],
            queries=tuple(profile_data["queries"]),
            subject_terms=tuple(profile_data["subject_terms"]),
            context_terms=tuple(profile_data["context_terms"]),
            exclude_terms=tuple(profile_data["exclude_terms"]),
            minimum_relevance_score=profile_data["minimum_relevance_score"],
        )
        candidates = search_candidates(profile, start, today)
        now = datetime.now(TIMEZONE).isoformat(timespec="seconds")
        run_id = repository.create_run(
            "scheduled_monitor",
            {
                "schedule": schedule_label(schedule),
                "period": {"start": start.isoformat(), "end": today.isoformat()},
            },
            requirement_name=profile.name,
            topic_key=topic_key,
        )
        try:
            repository.upsert_candidates(profile, candidates, now)
            to_crawl = repository.candidates_to_crawl(profile, candidates, False, None)
            updates = asyncio.run(crawl_new_pages(to_crawl, 2))
            for update in updates:
                repository.update_crawl(topic_key, update)
            success_count = sum(item["status"] == "crawled" for item in updates)
            message = f"检索 {start.isoformat()} 至 {today.isoformat()}，命中 {len(candidates)} 条，新抓取成功 {success_count} 条"
            repository.finish_run(
                run_id,
                {"candidate_count": len(candidates), "crawl_count": len(to_crawl), "success_count": success_count},
            )
            repository.record_schedule_run(topic_key, "completed", message)
        except Exception as exc:
            repository.finish_run(run_id, {"error": str(exc)}, status="failed")
            repository.record_schedule_run(topic_key, "failed", str(exc))
            logger.exception("定时爬取失败：%s", topic_key)
    except Exception:
        logger.exception("定时任务执行失败：%s", topic_key)
        try:
            repository.record_schedule_run(topic_key, "failed", "定时任务执行失败")
        except Exception:
            logger.exception("无法写入定时任务结果：%s", topic_key)
    finally:
        repository.close()


def sync_schedule_job(scheduler: BackgroundScheduler, topic_key: str, row: dict[str, Any] | None) -> None:
    job_id = _job_id(topic_key)
    existing = scheduler.get_job(job_id)
    if existing:
        scheduler.remove_job(job_id)
    if not row or not row.get("enabled"):
        return
    scheduler.add_job(
        execute_scheduled_crawl,
        trigger=schedule_trigger(row),
        args=[topic_key],
        id=job_id,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )


def load_schedule_jobs(scheduler: BackgroundScheduler) -> None:
    repository = PolicyRepository()
    try:
        for row in repository.list_schedules():
            if row["enabled"]:
                sync_schedule_job(scheduler, row["topic_key"], row)
    finally:
        repository.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.start()
    try:
        load_schedule_jobs(scheduler)
    except Exception:
        logger.exception("启动时未能载入定时任务")
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="政策主题采集 API", version="0.2.0", lifespan=lifespan)
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        repository = PolicyRepository()
        repository.close()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"PostgreSQL 不可用：{exc}") from exc
    return {"status": "ok", "database": "postgresql"}


@app.post("/site-selection/rank")
def site_selection_rank(request: SiteSelectionRequest) -> dict[str, object]:
    repository = SiteSelectionRepository()
    try:
        candidate_lands = repository.list_candidate_lands(request.resource_key)
        candidates = repository.list_candidates(request.resource_key)
        policy_record = repository.get_bamboo_policy_score()
    finally:
        repository.close()

    if policy_record is None:
        raise HTTPException(status_code=503, detail="数据库中没有可用的竹产业政策评分")
    policy = {
        "score": round(float(policy_record["score"]), 2),
        "status": "现有竹产业政策评分",
        "score_version": policy_record["score_version"],
        "run_id": policy_record["run_id"],
        "requirement_name": policy_record["requirement_name"],
        "completed_at": policy_record["completed_at"],
        "dimensions": policy_record["dimensions"],
    }
    eligible_lands = filter_candidates(candidate_lands, request.minimum_area_mu)
    eligible = filter_candidates(candidates, request.minimum_area_mu)
    ranked = rank_candidates(eligible, request.weights.model_dump(), policy["score"])
    labels = {
        "abandoned_cropland": ("疑似撂荒耕地", "DWD候选斑块 + ADS选址指标"),
        "stable_natural_resource": (
            "稳定裸地/草灌地（潜在资源）",
            "DWD稳定资源斑块 + ADS选址指标",
        ),
    }
    label, source = labels[request.resource_key]
    return {
        "resource_key": request.resource_key,
        "label": label,
        "source": source,
        "total_count": len(candidate_lands),
        "eligible_count": len(eligible_lands),
        "scored_count": len(candidates),
        "ranked_count": len(ranked),
        "policy": policy,
        "candidate_lands": eligible_lands,
        "candidates": ranked,
    }


@app.get("/topics")
def list_topics() -> list[dict[str, object]]:
    topics: list[dict[str, object]] = []
    for path in TOPICS_DIR.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        topics.append({"key": path.stem, **raw})
    topics.sort(key=lambda item: str(item.get("name") or item["key"]))
    return topics


@app.get("/policy-network")
def policy_network(topic_key: str = "zhuchanye") -> dict[str, object]:
    repository = PolicyRepository()
    try:
        result = repository.latest_policy_network(topic_key)
    finally:
        repository.close()
    if result is None:
        return {"runName": "数据库中没有政策画像数据", "measures": []}
    run = result["run"]
    measures = []
    for row in result["measures"]:
        measures.append({
            "id": str(row["measure_id"]),
            "toolId": int(row["policy_tool_type_id"]),
            "secondaryToolId": row["secondary_policy_tool_type_id"],
            "segments": row["industry_segments"],
            "text": row["measure_text"],
            "evidence": row["evidence_text"],
            "stage": row["implementation_stage"],
            "score": row["design_score_100"] or 0,
            "strengthScore": row["strength_score"] or 0,
            "specificityScore": row["specificity_score"] or 0,
            "scoreVersion": row["score_version"] or "",
            "scoreReasons": row["score_reasons"],
            "documentId": row["document_id"],
            "sectionId": row["section_id"],
            "sectionPath": row["section_path"],
            "sourceUrl": row["source_url"],
            "documentTitle": row["document_title"],
            "publishedAt": row["document_published_at"] or "",
            "sourceType": row["document_source_type"] or "政府网站",
            "publisher": row["document_publisher"] or "",
            "documentNumber": row["document_number"] or "",
            "region": row["region"],
            "topic": row["industry_topic"],
            "recordType": row["record_type"],
            "targets": row["policy_target"],
            "agencies": row["implementing_agencies"],
            "timeScope": row["time_scope"] or "",
            "startDate": str(row["start_date"] or ""),
            "endDate": str(row["end_date"] or ""),
            "quantities": row["quantities"],
            "dedupGroupId": row["dedup_group_id"] or "",
            "isPrimary": bool(row["is_primary"]),
            "duplicateOf": row["duplicate_of"] or "",
        })
    run_name = run["requirement_name"] or f"PostgreSQL 运行 {run['id']}"
    return {"runName": run_name, "measures": measures}


@app.post("/search")
async def search(request: SearchRequest) -> dict[str, object]:
    if request.start_date > request.end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    profile = request.to_profile()
    try:
        candidates = await run_in_threadpool(
            search_candidates, profile, request.start_date, request.end_date
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"检索失败：{exc}") from exc
    return {
        "profile": asdict(profile),
        "period": {"start": request.start_date, "end": request.end_date},
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


@app.post("/crawl")
async def crawl(request: CrawlRequest) -> dict[str, object]:
    candidates = [candidate.model_dump() for candidate in request.candidates]
    profile = TopicProfile(
        key=_topic_key(request.topic_key, request.requirement_name),
        name=request.requirement_name.strip(),
        queries=(),
        subject_terms=(),
        context_terms=(),
        exclude_terms=(),
        minimum_relevance_score=0,
    )
    repository = PolicyRepository()
    run_id = repository.create_run(
        "web_crawl",
        {"candidate_count": len(candidates), "concurrency": request.concurrency},
        requirement_name=request.requirement_name,
        topic_key=profile.key,
    )
    try:
        now = datetime.now(TIMEZONE).isoformat(timespec="seconds")
        if request.queries and request.subject_terms and request.context_terms and request.start_date:
            repository.save_project_profile(
                profile.key,
                request.requirement_name.strip(),
                list(_clean_terms(request.queries)),
                list(_clean_terms(request.subject_terms)),
                list(_clean_terms(request.context_terms)),
                list(_clean_terms(request.exclude_terms)),
                request.minimum_relevance_score,
                request.start_date.isoformat(),
            )
        repository.upsert_candidates(profile, candidates, now)
        updates = await crawl_new_pages(candidates, request.concurrency)
        for update in updates:
            repository.update_crawl(profile.key, update)
    except Exception as exc:
        repository.finish_run(run_id, {"error": str(exc)}, status="failed")
        repository.close()
        raise HTTPException(status_code=502, detail=f"抓取失败：{exc}") from exc

    public_updates = [
        {key: value for key, value in update.items() if key not in {"markdown_content", "html_content"}}
        for update in updates
    ]
    manifest = {
        "run_id": run_id,
        "requirement_name": request.requirement_name,
        "run_at": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "storage": f"postgresql://ops/crawl_runs/{run_id}",
        "requested_count": len(candidates),
        "success_count": sum(item["status"] == "crawled" for item in updates),
        "candidates": candidates,
        "updates": public_updates,
    }
    repository.finish_run(run_id, manifest)
    repository.close()
    return manifest


@app.get("/projects")
def list_projects() -> list[dict[str, object]]:
    repository = PolicyRepository()
    try:
        schedules = {row["topic_key"]: row for row in repository.list_schedules()}
        projects = []
        for project in repository.list_crawled_projects():
            profile = resolve_profile(repository, project["topic_key"], project["name"])
            projects.append({
                "topic_key": project["topic_key"],
                "name": project["name"],
                "crawled_count": int(project["crawled_count"]),
                "last_crawled_at": _iso(project["last_crawled_at"]),
                "has_profile": profile is not None,
                "schedule": _schedule_payload(schedules.get(project["topic_key"])),
            })
        return projects
    finally:
        repository.close()


@app.get("/schedules")
def list_schedule_tasks() -> list[dict[str, object]]:
    repository = PolicyRepository()
    try:
        return [
            {
                "topic_key": row["topic_key"],
                "name": row.get("name") or row["topic_key"],
                "schedule": _schedule_payload(row),
            }
            for row in repository.list_schedules()
            if row.get("enabled")
        ]
    finally:
        repository.close()


@app.put("/schedules")
def save_schedule(request: ScheduleRequest) -> dict[str, object]:
    try:
        record = request.to_record()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository = PolicyRepository()
    try:
        projects = {item["topic_key"]: item for item in repository.list_crawled_projects()}
        project = projects.get(request.topic_key)
        if project is None:
            raise HTTPException(status_code=404, detail="只能为已经成功抓取过的项目设置定时爬取")
        profile = resolve_profile(repository, request.topic_key, project["name"])
        if profile is None:
            raise HTTPException(
                status_code=422,
                detail="这个项目还没有保存检索条件。请先在政策采集页用同一需求再抓取一次。",
            )
        if repository.get_project_profile(request.topic_key) is None:
            repository.save_project_profile(
                request.topic_key,
                profile["requirement_name"],
                profile["queries"],
                profile["subject_terms"],
                profile["context_terms"],
                profile["exclude_terms"],
                profile["minimum_relevance_score"],
                profile["start_date"].isoformat(),
            )
        row = repository.save_schedule(request.topic_key, record, True)
    finally:
        repository.close()
    sync_schedule_job(app.state.scheduler, request.topic_key, row)
    return {
        "topic_key": request.topic_key,
        "name": row.get("name") or project["name"],
        "schedule": _schedule_payload(row),
    }


@app.delete("/schedules/{topic_key}")
def remove_schedule(topic_key: str) -> dict[str, bool]:
    repository = PolicyRepository()
    try:
        deleted = repository.delete_schedule(topic_key)
    finally:
        repository.close()
    if not deleted:
        raise HTTPException(status_code=404, detail="这个项目还没有定时任务")
    sync_schedule_job(app.state.scheduler, topic_key, None)
    return {"deleted": True}


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = os.getenv("UVICORN_RELOAD", "").lower() in {"1", "true", "yes"}
    uvicorn.run(
        "policy_crawler.web_api:app",
        host=host,
        port=port,
        reload=reload_enabled,
    )


if __name__ == "__main__":
    main()
