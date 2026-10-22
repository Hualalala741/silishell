from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE_MIGRATION = ROOT / "sql" / "004_policy_warehouse_layers.sql"
WAREHOUSE_UPGRADES = (
    ROOT / "sql" / "013_policy_extraction_semantics.sql",
)

POLICY_DOCUMENTS = "ods.policy_documents"
POLICY_EXTRACTIONS = "ods.policy_extractions"
CRAWL_RUNS = "ops.crawl_runs"
PROJECT_PROFILES = "ops.project_profiles"
CRAWL_SCHEDULES = "ops.crawl_schedules"


def database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("未配置 DATABASE_URL；本项目要求使用 PostgreSQL")
    if not value.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL 必须是 PostgreSQL 连接地址")
    return value


def materialize_policy_outputs(
    connection: psycopg.Connection[Any],
    run_id: int,
    measures: Any,
    policy_scores: Any,
    portrait: Any,
) -> None:
    """Store one policy portrait run in DWD, DWS, and ADS."""
    run = connection.execute(
        "SELECT topic_key FROM ops.crawl_runs WHERE id = %s",
        (run_id,),
    ).fetchone()
    if not run or not run["topic_key"]:
        raise ValueError(f"运行 {run_id} 缺少 topic_key，无法建立措施来源关联")
    topic_key = str(run["topic_key"])

    if isinstance(measures, list):
        connection.execute(
            "DELETE FROM dwd.policy_measures WHERE run_id = %s",
            (run_id,),
        )
        rows = []
        for source_record_number, item in enumerate(measures, start=1):
            if not isinstance(item, dict) or not item.get("measure_id"):
                continue
            rows.append(
                (
                    run_id,
                    source_record_number,
                    topic_key,
                    item["measure_id"],
                    item.get("document_id") or "",
                    item.get("section_id") or "",
                    Jsonb(item.get("section_path") or []),
                    item.get("source_url") or "",
                    item.get("region") or "",
                    item.get("industry_topic") or "",
                    Jsonb(item.get("industry_segments") or []),
                    item.get("measure_text") or "",
                    item.get("evidence_text") or "",
                    item.get("record_type") or "unknown",
                    item.get("implementation_stage") or "unknown",
                    item.get("policy_tool_type_id") or 99,
                    item.get("secondary_policy_tool_type_id"),
                    Jsonb(item.get("policy_target") or []),
                    Jsonb(item.get("implementing_agencies") or []),
                    item.get("time_scope"),
                    item.get("start_date"),
                    item.get("end_date"),
                    Jsonb(item.get("quantities") or []),
                    item.get("strength_score"),
                    item.get("specificity_score"),
                    item.get("design_score"),
                    item.get("design_score_100"),
                    item.get("score_version"),
                    Jsonb(item.get("score_reasons") or {}),
                    item.get("dedup_group_id"),
                    item.get("is_primary"),
                    item.get("duplicate_of"),
                )
            )
        if rows:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO dwd.policy_measures (
                        run_id, source_record_number, topic_key, measure_id,
                        document_id, section_id,
                        section_path, source_url, region, industry_topic,
                        industry_segments, measure_text, evidence_text, record_type,
                        implementation_stage, policy_tool_type_id,
                        secondary_policy_tool_type_id, policy_target,
                        implementing_agencies, time_scope, start_date, end_date,
                        quantities, strength_score, specificity_score, design_score,
                        design_score_100, score_version, score_reasons, dedup_group_id,
                        is_primary, duplicate_of
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    rows,
                )

    if isinstance(policy_scores, dict):
        connection.execute(
            """
            INSERT INTO dws.county_policy_scores (
                run_id, score_version, aggregation_method,
                overall_unweighted_score_100, dimensions,
                raw_primary_measure_count, excluded_duplicate_count, warning,
                imported_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (run_id) DO UPDATE SET
                score_version = EXCLUDED.score_version,
                aggregation_method = EXCLUDED.aggregation_method,
                overall_unweighted_score_100 = EXCLUDED.overall_unweighted_score_100,
                dimensions = EXCLUDED.dimensions,
                raw_primary_measure_count = EXCLUDED.raw_primary_measure_count,
                excluded_duplicate_count = EXCLUDED.excluded_duplicate_count,
                warning = EXCLUDED.warning,
                imported_at = now()
            """,
            (
                run_id,
                policy_scores.get("score_version") or "unknown",
                policy_scores.get("aggregation_method") or "",
                policy_scores.get("overall_unweighted_score_100") or 0,
                Jsonb(policy_scores.get("dimensions") or []),
                policy_scores.get("raw_primary_measure_count") or 0,
                policy_scores.get("excluded_duplicate_count") or 0,
                policy_scores.get("warning"),
            ),
        )

    if isinstance(portrait, dict):
        connection.execute(
            """
            INSERT INTO ads.county_policy_portraits (
                run_id, region, industry_topic, generated_at,
                document_count, measure_count, coverage, policy_scores,
                quantitative_indicators, measures, note, imported_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (run_id) DO UPDATE SET
                region = EXCLUDED.region,
                industry_topic = EXCLUDED.industry_topic,
                generated_at = EXCLUDED.generated_at,
                document_count = EXCLUDED.document_count,
                measure_count = EXCLUDED.measure_count,
                coverage = EXCLUDED.coverage,
                policy_scores = EXCLUDED.policy_scores,
                quantitative_indicators = EXCLUDED.quantitative_indicators,
                measures = EXCLUDED.measures,
                note = EXCLUDED.note,
                imported_at = now()
            """,
            (
                run_id,
                portrait.get("region") or "",
                portrait.get("industry_topic") or "",
                str(portrait.get("generated_at") or ""),
                portrait.get("document_count") or 0,
                portrait.get("measure_count") or 0,
                Jsonb(portrait.get("coverage") or {}),
                Jsonb(portrait.get("policy_scores") or {}),
                Jsonb(portrait.get("quantitative_indicators") or []),
                Jsonb(portrait.get("measures") or []),
                portrait.get("note"),
            ),
        )


class PolicyRepository:
    """PostgreSQL is the canonical store for crawler state and page content."""

    def __init__(self, url: str | None = None):
        self.connection = psycopg.connect(url or database_url(), row_factory=dict_row)
        self._ensure_schema()

    def _ensure_idle(self) -> None:
        """Commit a read-only transaction so the next write is not rolled back on close."""
        if self.connection.info.transaction_status.name != "IDLE":
            self.connection.commit()

    def _ensure_schema(self) -> None:
        with self.connection.transaction():
            self.connection.execute(WAREHOUSE_MIGRATION.read_text(encoding="utf-8"))
            for migration in WAREHOUSE_UPGRADES:
                self.connection.execute(migration.read_text(encoding="utf-8"))

    def upsert_candidates(
        self, profile: Any, candidates: list[dict[str, Any]], seen_at: str
    ) -> list[dict[str, Any]]:
        new_candidates: list[dict[str, Any]] = []
        with self.connection.transaction():
            for candidate in candidates:
                exists = self.connection.execute(
                    f"SELECT 1 FROM {POLICY_DOCUMENTS} WHERE topic_key = %s AND source_url = %s",
                    (profile.key, candidate["source_url"]),
                ).fetchone()
                if not exists:
                    new_candidates.append(candidate)
                self.connection.execute(
                    f"""
                    INSERT INTO {POLICY_DOCUMENTS} (
                        topic_key, source_url, topic, document_id, title, published_at, source_type,
                        kind, template, document_number, publisher, channel, snippet,
                        first_seen_at, last_seen_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (topic_key, source_url) DO UPDATE SET
                        topic = EXCLUDED.topic,
                        document_id = EXCLUDED.document_id,
                        title = EXCLUDED.title,
                        published_at = EXCLUDED.published_at,
                        source_type = EXCLUDED.source_type,
                        kind = EXCLUDED.kind,
                        template = EXCLUDED.template,
                        document_number = EXCLUDED.document_number,
                        publisher = EXCLUDED.publisher,
                        channel = EXCLUDED.channel,
                        snippet = EXCLUDED.snippet,
                        last_seen_at = EXCLUDED.last_seen_at
                    """,
                    (
                        profile.key, candidate["source_url"], candidate["topic"],
                        candidate.get("document_id"), candidate["title"],
                        candidate.get("published_at"), candidate.get("source_type"),
                        candidate.get("kind"), candidate.get("template"),
                        candidate.get("document_number"), candidate.get("publisher"),
                        candidate.get("channel"), candidate.get("snippet"),
                        seen_at, seen_at,
                    ),
                )
        return new_candidates

    def candidates_to_crawl(
        self,
        profile: Any,
        candidates: list[dict[str, Any]],
        refresh_existing: bool,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        if refresh_existing:
            selected = candidates
        else:
            rows = self.connection.execute(
                f"SELECT source_url, crawl_status FROM {POLICY_DOCUMENTS} WHERE topic_key = %s",
                (profile.key,),
            ).fetchall()
            statuses = {row["source_url"]: row["crawl_status"] for row in rows}
            selected = [
                item for item in candidates if statuses.get(item["source_url"]) != "crawled"
            ]
        return selected[:limit] if limit is not None else selected

    def update_crawl(self, topic_key: str, update: dict[str, Any]) -> None:
        with self.connection.transaction():
            self.connection.execute(
                f"""
                UPDATE {POLICY_DOCUMENTS} SET
                    crawl_status = %s,
                    crawled_at = %s,
                    resolved_url = %s,
                    content_hash = %s,
                    markdown_content = %s,
                    html_content = %s,
                    last_error = %s
                WHERE topic_key = %s AND source_url = %s
                """,
                (
                    update["status"], update.get("crawled_at"), update.get("resolved_url"),
                    update.get("content_hash"), update.get("markdown_content"),
                    update.get("html_content"), update.get("error"), topic_key,
                    update["source_url"],
                ),
            )

    def create_run(
        self,
        run_type: str,
        request: dict[str, Any],
        requirement_name: str | None = None,
        topic_key: str | None = None,
    ) -> int:
        self._ensure_idle()
        with self.connection.transaction():
            row = self.connection.execute(
                f"""
                INSERT INTO {CRAWL_RUNS}
                    (run_type, requirement_name, topic_key, request)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (run_type, requirement_name, topic_key, Jsonb(request)),
            ).fetchone()
        return int(row["id"])

    def finish_run(self, run_id: int, result: dict[str, Any], status: str = "completed") -> None:
        self._ensure_idle()
        with self.connection.transaction():
            self.connection.execute(
                f"""
                UPDATE {CRAWL_RUNS}
                SET completed_at = now(), status = %s, result = %s
                WHERE id = %s
                """,
                (status, Jsonb(result), run_id),
            )

    def store_extractions(
        self,
        records: Iterable[dict[str, Any]],
        requested_fields: Iterable[str] = (),
        requirements: str = "",
        model_provider: str | None = None,
    ) -> int:
        count = 0
        fields = list(requested_fields)
        with self.connection.transaction():
            for record in records:
                self.connection.execute(
                    f"""
                    INSERT INTO {POLICY_EXTRACTIONS} (
                        source_url, crawled_at, status, data, requested_fields,
                        requirements, model_provider, error_text
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.get("source_url", ""), record.get("crawled_at"),
                        record.get("status", "success"), Jsonb(record), Jsonb(fields),
                        requirements or None, model_provider, record.get("error"),
                    ),
                )
                count += 1
        return count

    def export_topic_documents(self, topic_key: str, destination: Path) -> int:
        """Materialize a temporary portrait input directory from canonical DB rows."""
        rows = self.connection.execute(
            f"""
            SELECT document_id, title, source_url, published_at, source_type, publisher,
                   document_number, markdown_content
            FROM {POLICY_DOCUMENTS}
            WHERE topic_key = %s AND crawl_status = 'crawled'
              AND markdown_content IS NOT NULL
            ORDER BY published_at, source_url
            """,
            (topic_key,),
        ).fetchall()
        if not rows:
            raise ValueError(f"PostgreSQL 中没有主题 {topic_key!r} 的已抓取正文")
        destination.mkdir(parents=True, exist_ok=True)
        candidates: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            document_id = str(row["document_id"] or index)
            stem = "".join(char if char.isalnum() or char in "_-" else "-" for char in document_id)
            (destination / f"{stem}.md").write_text(row["markdown_content"], encoding="utf-8")
            candidates.append({key: value for key, value in row.items() if key != "markdown_content"})
        (destination / "manifest.json").write_text(
            json.dumps({"source": "postgresql", "topic_key": topic_key, "candidates": candidates}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return len(rows)

    def store_policy_portrait(self, run_id: int, output_dir: Path) -> dict[str, int]:
        """Load the canonical outputs of one portrait run into warehouse layers."""
        measures = json.loads((output_dir / "measures.json").read_text(encoding="utf-8"))
        policy_scores = json.loads(
            (output_dir / "policy_scores.json").read_text(encoding="utf-8")
        )
        portrait = json.loads((output_dir / "portrait.json").read_text(encoding="utf-8"))
        self._ensure_idle()
        with self.connection.transaction():
            region = str(portrait.get("region") or "")
            industry_topic = str(portrait.get("industry_topic") or "")
            previous_rows = self.connection.execute(
                """
                SELECT DISTINCT run_id
                FROM (
                    SELECT run_id FROM dwd.policy_measures
                    WHERE region = %s AND industry_topic = %s
                    UNION
                    SELECT run_id FROM ads.county_policy_portraits
                    WHERE region = %s AND industry_topic = %s
                ) existing
                WHERE run_id <> %s
                """,
                (region, industry_topic, region, industry_topic, run_id),
            ).fetchall()
            previous_run_ids = [int(row["run_id"]) for row in previous_rows]
            if previous_run_ids:
                self.connection.execute(
                    """
                    UPDATE ops.crawl_runs
                    SET status = 'superseded',
                        result = COALESCE(result, '{}'::jsonb)
                            || jsonb_build_object('superseded_by_run_id', %s)
                    WHERE id = ANY(%s)
                    """,
                    (run_id, previous_run_ids),
                )
                self.connection.execute(
                    "DELETE FROM dws.county_policy_scores WHERE run_id = ANY(%s)",
                    (previous_run_ids,),
                )
                self.connection.execute(
                    "DELETE FROM ads.county_policy_portraits WHERE run_id = ANY(%s)",
                    (previous_run_ids,),
                )
                self.connection.execute(
                    "DELETE FROM dwd.policy_measures WHERE run_id = ANY(%s)",
                    (previous_run_ids,),
                )
            materialize_policy_outputs(
                self.connection, run_id, measures, policy_scores, portrait
            )
        return {
            "measures": len(measures) if isinstance(measures, list) else 0,
            "scores": 1 if isinstance(policy_scores, dict) else 0,
            "portraits": 1 if isinstance(portrait, dict) else 0,
        }

    def list_crawled_projects(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            f"""
            SELECT
                topic_key,
                COALESCE(MAX(NULLIF(topic, '')), topic_key) AS name,
                COUNT(*) FILTER (WHERE crawl_status = 'crawled') AS crawled_count,
                MAX(crawled_at) AS last_crawled_at
            FROM {POLICY_DOCUMENTS}
            GROUP BY topic_key
            HAVING COUNT(*) FILTER (WHERE crawl_status = 'crawled') > 0
            ORDER BY MAX(crawled_at) DESC NULLS LAST, topic_key
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_policy_network(self, topic_key: str) -> dict[str, Any] | None:
        run = self.connection.execute(
            """
            SELECT run.id, run.requirement_name, run.completed_at
            FROM ops.crawl_runs run
            WHERE run.status = 'completed'
              AND EXISTS (
                  SELECT 1 FROM dwd.policy_measures measure
                  WHERE measure.run_id = run.id AND measure.topic_key = %s
              )
            ORDER BY run.completed_at DESC NULLS LAST, run.id DESC
            LIMIT 1
            """,
            (topic_key,),
        ).fetchone()
        if not run:
            return None
        rows = self.connection.execute(
            """
            SELECT *
            FROM dwd.policy_measure_details
            WHERE run_id = %s
            ORDER BY source_record_number, measure_id
            """,
            (run["id"],),
        ).fetchall()
        return {"run": dict(run), "measures": [dict(row) for row in rows]}

    def get_project_profile(self, topic_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT * FROM {PROJECT_PROFILES} WHERE topic_key = %s",
            (topic_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_project_profile(
        self,
        topic_key: str,
        requirement_name: str,
        queries: list[str],
        subject_terms: list[str],
        context_terms: list[str],
        exclude_terms: list[str],
        minimum_relevance_score: int,
        start_date: str,
    ) -> None:
        self._ensure_idle()
        with self.connection.transaction():
            self.connection.execute(
                f"""
                INSERT INTO {PROJECT_PROFILES} (
                    topic_key, requirement_name, queries, subject_terms, context_terms,
                    exclude_terms, minimum_relevance_score, start_date, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (topic_key) DO UPDATE SET
                    requirement_name = EXCLUDED.requirement_name,
                    queries = EXCLUDED.queries,
                    subject_terms = EXCLUDED.subject_terms,
                    context_terms = EXCLUDED.context_terms,
                    exclude_terms = EXCLUDED.exclude_terms,
                    minimum_relevance_score = EXCLUDED.minimum_relevance_score,
                    start_date = EXCLUDED.start_date,
                    updated_at = now()
                """,
                (
                    topic_key, requirement_name, Jsonb(queries), Jsonb(subject_terms),
                    Jsonb(context_terms), Jsonb(exclude_terms), minimum_relevance_score,
                    start_date,
                ),
            )

    def list_schedules(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            f"""
            SELECT schedule.*, COALESCE(project.name, schedule.topic_key) AS name
            FROM {CRAWL_SCHEDULES} AS schedule
            LEFT JOIN (
                SELECT topic_key, COALESCE(MAX(NULLIF(topic, '')), topic_key) AS name
                FROM {POLICY_DOCUMENTS}
                GROUP BY topic_key
            ) AS project ON project.topic_key = schedule.topic_key
            ORDER BY schedule.updated_at DESC, schedule.topic_key
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_schedule(self, topic_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT * FROM {CRAWL_SCHEDULES} WHERE topic_key = %s",
            (topic_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_schedule(self, topic_key: str, schedule: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
        self._ensure_idle()
        with self.connection.transaction():
            row = self.connection.execute(
                f"""
                INSERT INTO {CRAWL_SCHEDULES} (
                    topic_key, enabled, schedule_kind, interval_value, interval_unit,
                    day_of_month, weekday, hour, minute, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (topic_key) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    schedule_kind = EXCLUDED.schedule_kind,
                    interval_value = EXCLUDED.interval_value,
                    interval_unit = EXCLUDED.interval_unit,
                    day_of_month = EXCLUDED.day_of_month,
                    weekday = EXCLUDED.weekday,
                    hour = EXCLUDED.hour,
                    minute = EXCLUDED.minute,
                    updated_at = now()
                RETURNING *
                """,
                (
                    topic_key, enabled, schedule["schedule_kind"], schedule.get("interval_value"),
                    schedule.get("interval_unit"), schedule.get("day_of_month"), schedule.get("weekday"),
                    schedule.get("hour"), schedule.get("minute"),
                ),
            ).fetchone()
        saved = dict(row)
        named = self.connection.execute(
            f"""
            SELECT COALESCE(MAX(NULLIF(topic, '')), %s) AS name
            FROM {POLICY_DOCUMENTS}
            WHERE topic_key = %s
            """,
            (topic_key, topic_key),
        ).fetchone()
        saved["name"] = named["name"] if named and named["name"] else topic_key
        return saved

    def delete_schedule(self, topic_key: str) -> bool:
        self._ensure_idle()
        with self.connection.transaction():
            row = self.connection.execute(
                f"DELETE FROM {CRAWL_SCHEDULES} WHERE topic_key = %s RETURNING topic_key",
                (topic_key,),
            ).fetchone()
        return row is not None

    def record_schedule_run(self, topic_key: str, status: str, message: str) -> None:
        self._ensure_idle()
        with self.connection.transaction():
            self.connection.execute(
                f"""
                UPDATE {CRAWL_SCHEDULES}
                SET last_run_at = now(), last_status = %s, last_message = %s, updated_at = now()
                WHERE topic_key = %s
                """,
                (status, message, topic_key),
            )

    def close(self) -> None:
        self.connection.close()


def migrate_legacy_data(
    repository: PolicyRepository,
    project_root: Path,
    sqlite_path: Path,
) -> dict[str, int]:
    """Copy legacy SQLite documents and materialize portrait output directories."""
    counts = {"topic_documents": 0, "portrait_runs": 0}
    if sqlite_path.is_file():
        source = sqlite3.connect(sqlite_path)
        source.row_factory = sqlite3.Row
        try:
            tables = {
                row[0]
                for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            table = "topic_documents" if "topic_documents" in tables else "documents"
            topic_default = "zhuchanye"
            if table in tables:
                rows = source.execute(f"SELECT * FROM {table}").fetchall()
                with repository.connection.transaction():
                    for row in rows:
                        item = dict(row)
                        topic_key = item.get("topic_key") or topic_default
                        markdown_content = None
                        html_content = None
                        for field, target in (
                            ("markdown_path", "markdown"), ("html_path", "html")
                        ):
                            value = item.get(field)
                            if value and Path(value).is_file():
                                content = Path(value).read_text(encoding="utf-8", errors="replace")
                                if target == "markdown":
                                    markdown_content = content
                                else:
                                    html_content = content
                        repository.connection.execute(
                            f"""
                            INSERT INTO {POLICY_DOCUMENTS} (
                                topic_key, source_url, topic, document_id, title, published_at,
                                source_type, kind, template, document_number, publisher, channel,
                                snippet, first_seen_at, last_seen_at, crawl_status, crawled_at, content_hash,
                                markdown_content, html_content, last_error
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s
                            )
                            ON CONFLICT (topic_key, source_url) DO UPDATE SET
                                markdown_content = COALESCE(EXCLUDED.markdown_content, {POLICY_DOCUMENTS}.markdown_content),
                                html_content = COALESCE(EXCLUDED.html_content, {POLICY_DOCUMENTS}.html_content),
                                crawl_status = EXCLUDED.crawl_status,
                                content_hash = EXCLUDED.content_hash,
                                last_error = EXCLUDED.last_error
                            """,
                            (
                                topic_key, item["source_url"], item.get("topic") or topic_key,
                                item.get("document_id"), item.get("title") or item["source_url"],
                                item.get("published_at"), item.get("source_type"), item.get("kind"),
                                item.get("template"), item.get("document_number"), item.get("publisher"),
                                item.get("channel"), item.get("snippet"),
                                item.get("first_seen_at"), item.get("last_seen_at"),
                                item.get("crawl_status") or "pending",
                                item.get("crawled_at"), item.get("content_hash"), markdown_content,
                                html_content, item.get("last_error"),
                            ),
                        )
                        counts["topic_documents"] += 1
        finally:
            source.close()

    output_root = project_root / "output"
    if output_root.is_dir():
        for run_dir in sorted(output_root.glob("portrait-*")):
            required = [
                run_dir / "measures.json",
                run_dir / "policy_scores.json",
                run_dir / "portrait.json",
            ]
            if not run_dir.is_dir() or not all(path.is_file() for path in required):
                continue
            relative = str(run_dir.resolve().relative_to(project_root.resolve()))
            existing = repository.connection.execute(
                """
                SELECT id FROM ops.crawl_runs
                WHERE run_type = 'policy_portrait'
                  AND result->>'output_dir' = %s
                LIMIT 1
                """,
                (relative,),
            ).fetchone()
            if existing:
                run_id = int(existing["id"])
            else:
                run_id = repository.create_run(
                    "policy_portrait",
                    {"migration_source": "legacy output directory"},
                )
            repository.store_policy_portrait(run_id, run_dir)
            repository.finish_run(
                run_id,
                {"output_dir": relative, "files_retained": True},
            )
            counts["portrait_runs"] += 1
    return counts
