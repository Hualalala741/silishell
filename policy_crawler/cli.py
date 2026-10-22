from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from .pipeline import (
    FIELD_HINTS,
    crawl_and_extract,
    crawl_many_and_extract,
    save_batch,
    save_record,
)
from .policy_portrait import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LLM_MODEL,
    rebuild_portrait_from_raw,
    run_portrait,
)
from .storage import PolicyRepository, migrate_legacy_data
from .topic_monitor import load_topic_profile, run_monitor


DEFAULT_FIELDS = ",".join(FIELD_HINTS)
DEFAULT_PROVIDER = "dashscope/qwen3.5-plus-2026-04-20"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def parse_fields(raw: str) -> list[str]:
    fields = list(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not fields:
        raise argparse.ArgumentTypeError("至少需要一个字段")
    return fields


def llm_api_key() -> str | None:
    return os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY")


def llm_base_url(provider: str) -> str | None:
    configured = os.getenv("LLM_BASE_URL")
    if configured:
        return configured
    if provider.startswith("dashscope/"):
        return DASHSCOPE_BASE_URL
    return None


def read_urls(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    urls = list(
        dict.fromkeys(
            line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )
    )
    if not urls:
        raise ValueError(f"URL 文件为空：{path}")
    return urls


def execute(args: argparse.Namespace) -> None:
    formats = {"json", "csv"} if args.format == "both" else ({args.format} if args.format != "none" else set())
    url_file = getattr(args, "url_file", None)
    if url_file:
        urls = read_urls(url_file)
        print(f"开始批量处理 {len(urls)} 个 URL，并发数：{args.concurrency}")
        records = asyncio.run(
            crawl_many_and_extract(
                urls=urls,
                fields=args.fields,
                requirements=args.requirements,
                provider=args.provider,
                api_key=llm_api_key(),
                base_url=llm_base_url(args.provider),
                concurrency=args.concurrency,
            )
        )
        repository = PolicyRepository()
        try:
            stored = repository.store_extractions(
                records,
                requested_fields=args.fields,
                requirements=args.requirements,
                model_provider=args.provider,
            )
        finally:
            repository.close()
        paths = save_batch(records, args.output, formats) if formats else []
        succeeded = sum(record["status"] == "success" for record in records)
        print(f"批量完成：成功 {succeeded}，失败 {len(records) - succeeded}")
    else:
        record = asyncio.run(
            crawl_and_extract(
                url=args.url,
                fields=args.fields,
                requirements=args.requirements,
                provider=args.provider,
                api_key=llm_api_key(),
                base_url=llm_base_url(args.provider),
            )
        )
        repository = PolicyRepository()
        try:
            stored = repository.store_extractions(
                [record],
                requested_fields=args.fields,
                requirements=args.requirements,
                model_provider=args.provider,
            )
        finally:
            repository.close()
        paths = save_record(record, args.output, formats) if formats else []
    print(f"已写入 PostgreSQL：{stored} 条")
    for path in paths:
        print(f"已保存：{path.resolve()}")


def execute_monitor(args: argparse.Namespace) -> None:
    profile = load_topic_profile(args.topic, args.topics_dir)
    report = run_monitor(
        profile=profile,
        years=args.years,
        concurrency=args.concurrency,
        refresh_existing=args.refresh_existing,
        limit=args.limit,
        data_dir=args.data_dir,
        output_dir=args.output,
    )
    print(
        f"{profile.name}监测完成：相关候选 {report['candidate_count']}，"
        f"新增 {report['new_count']}，本次抓取 {report['crawl_count']}，"
        f"成功 {report['crawl_success_count']}"
    )
    for item in report["candidates"]:
        print(
            f"[{item['kind']}] {item['published_at']} "
            f"分数={item['relevance_score']} {item['title']}\n  {item['source_url']}"
        )
    print(f"运行报告：{report['report_path']}")


def execute_portrait(args: argparse.Namespace) -> None:
    profile = load_topic_profile(args.topic, args.topics_dir)
    if args.reuse_run and not args.pages_dir:
        raise ValueError("--reuse-run 需要同时提供原始 --pages-dir")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    pages_dir = args.pages_dir
    if pages_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix=f"policy-{profile.key}-")
        pages_dir = Path(temporary.name) / profile.key
        repository = PolicyRepository()
        try:
            count = repository.export_topic_documents(profile.key, pages_dir)
        finally:
            repository.close()
        print(f"从 PostgreSQL 载入正文：{count} 条")

    repository = PolicyRepository()
    try:
        run_id = repository.create_run(
            "policy_portrait",
            {
                "pages_dir": str(pages_dir),
                "reuse_run": str(args.reuse_run) if args.reuse_run else None,
                "embedding_model": args.embedding_model,
                "llm_model": args.llm_model,
                "embedding_dimensions": args.embedding_dimensions,
                "similarity_threshold": args.similarity_threshold,
            },
            requirement_name=profile.name,
            topic_key=profile.key,
        )
    finally:
        repository.close()

    try:
        if args.reuse_run:
            result = rebuild_portrait_from_raw(pages_dir, profile, args.reuse_run)
        else:
            result = run_portrait(
                pages_dir=pages_dir,
                profile=profile,
                output_dir=args.output,
                api_key=llm_api_key() or "",
                base_url=os.getenv("LLM_BASE_URL", DASHSCOPE_BASE_URL),
                embedding_model=args.embedding_model,
                llm_model=args.llm_model,
                embedding_dimensions=args.embedding_dimensions,
                similarity_threshold=args.similarity_threshold,
            )
    except Exception as exc:
        repository = PolicyRepository()
        try:
            repository.finish_run(run_id, {"error": str(exc)}, status="failed")
        finally:
            repository.close()
        raise
    finally:
        if temporary is not None:
            temporary.cleanup()

    repository = PolicyRepository()
    try:
        stored = repository.store_policy_portrait(run_id, Path(result["output_dir"]))
        repository.finish_run(
            run_id,
            {
                **result,
                "stored": stored,
                "files_retained": True,
            },
        )
    except Exception as exc:
        repository.finish_run(run_id, {"error": str(exc)}, status="failed")
        raise
    finally:
        repository.close()
    print(
        f"画像分析完成：文档 {result['document_count']}，section {result['section_count']}，"
        f"相关 section {result['selected_section_count']}，measure {result['measure_count']}"
    )
    if result["source_warning_count"] or result["extraction_warning_count"]:
        print(
            f"告警：来源映射 {result['source_warning_count']}，"
            f"抽取校验 {result['extraction_warning_count']}"
        )
    print(f"输出目录：{result['output_dir']}")


def add_extraction_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fields", type=parse_fields, default=parse_fields(DEFAULT_FIELDS), help="逗号分隔的输出字段")
    parser.add_argument("--requirements", default="", help="给 LLM 的额外抽取要求")
    parser.add_argument("--provider", default=os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER), help="LiteLLM provider/model")
    parser.add_argument(
        "--format", choices=["none", "json", "csv", "both"], default="none",
        help="可选文件导出格式；主数据始终写入 PostgreSQL",
    )
    parser.add_argument("--output", type=Path, default=Path("output"))


def add_batch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url-file", type=Path, required=True, help="每行一个政策详情页 URL，支持空行和 # 注释")
    parser.add_argument("--concurrency", type=positive_int, default=2, help="并发页面数，默认 2")
    add_extraction_arguments(parser)


def add_single_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", required=True, help="政策原文的完整 URL")
    add_extraction_arguments(parser)


def add_schedule_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="单个政策原文 URL")
    source.add_argument("--url-file", type=Path, help="每行一个政策详情页 URL")
    parser.add_argument("--concurrency", type=positive_int, default=2, help="批量模式并发数，默认 2")
    add_extraction_arguments(parser)


def add_monitor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--topic", required=True, help="config/topics 下的主题文件名（不含 .json）")
    parser.add_argument(
        "--topics-dir", type=Path, default=Path("config/topics"), help="主题配置目录"
    )
    parser.add_argument("--years", type=positive_int, default=2, help="向前搜索多少年，默认 2")
    parser.add_argument("--concurrency", type=positive_int, default=2, help="Crawl4AI 并发数，默认 2")
    parser.add_argument(
        "--limit", type=positive_int, help="本轮最多抓取的详情页数；其余候选保留待后续抓取"
    )
    parser.add_argument("--refresh-existing", action="store_true", help="重新抓取数据库中已有的页面")
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data"),
        help="兼容旧命令的保留参数；主数据写入 PostgreSQL",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("output"),
        help="兼容旧命令的保留参数；运行报告写入 PostgreSQL",
    )


def add_portrait_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pages-dir",
        type=Path,
        help="可选旧版文件目录；省略时从 PostgreSQL 读取该主题正文",
    )
    parser.add_argument("--topic", default="zhuchanye", help="config/topics 下的主题文件名")
    parser.add_argument(
        "--topics-dir", type=Path, default=Path("config/topics"), help="主题配置目录"
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help="DashScope 文本向量模型",
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=positive_int,
        default=int(os.getenv("EMBEDDING_DIMENSIONS", "1024")),
        help="Embedding 维度，默认 1024",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("LLM_PROVIDER", DEFAULT_LLM_MODEL),
        help="用于 measure 抽取的 Qwen 模型",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.42,
        help="向量召回阈值；命中主题词的 section 不受阈值限制",
    )
    parser.add_argument("--output", type=Path, default=Path("output"), help="画像输出根目录")
    parser.add_argument(
        "--reuse-run",
        type=Path,
        help="复用已有运行目录的召回和原始 LLM 结果，仅重新校验与聚合",
    )


def add_trigger_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cron", help='五段 crontab，例如 "0 8 * * *"')
    group.add_argument("--interval-minutes", type=positive_int, help="每隔多少分钟执行")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="政府政策网页结构化抽取工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="立即执行一次")
    add_single_arguments(run_parser)

    batch_parser = subparsers.add_parser("batch", help="从文件批量抓取多个详情页")
    add_batch_arguments(batch_parser)

    schedule_parser = subparsers.add_parser("schedule", help="启动常驻定时任务")
    add_schedule_arguments(schedule_parser)
    add_trigger_arguments(schedule_parser)

    monitor_parser = subparsers.add_parser("monitor", help="按主题配置运行一次永春县监测（无 LLM）")
    add_monitor_arguments(monitor_parser)

    monitor_schedule_parser = subparsers.add_parser(
        "monitor-schedule", help="按主题配置定时运行永春县监测（无 LLM）"
    )
    add_monitor_arguments(monitor_schedule_parser)
    add_trigger_arguments(monitor_schedule_parser)

    portrait_parser = subparsers.add_parser(
        "portrait", help="从已抓取页面生成原子 measure 和产业政策画像"
    )
    add_portrait_arguments(portrait_parser)

    migrate_parser = subparsers.add_parser(
        "migrate-postgres", help="把旧 SQLite 文档和画像结果迁移到 PostgreSQL"
    )
    migrate_parser.add_argument("--project-root", type=Path, default=Path("."))
    migrate_parser.add_argument(
        "--sqlite", type=Path, default=Path("data/policy_monitor.db"),
        help="旧监测 SQLite 文件",
    )
    return parser


def positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("间隔分钟数必须大于 0")
    return value


def main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    if args.command in {"run", "batch"}:
        execute(args)
        return
    if args.command == "monitor":
        execute_monitor(args)
        return
    if args.command == "portrait":
        execute_portrait(args)
        return
    if args.command == "migrate-postgres":
        repository = PolicyRepository()
        try:
            counts = migrate_legacy_data(
                repository, args.project_root.resolve(), args.sqlite.resolve()
            )
        finally:
            repository.close()
        print(
            f"迁移完成：主题文档 {counts['topic_documents']} 条，"
            f"政策画像运行 {counts['portrait_runs']} 次"
        )
        return

    timezone = os.getenv("TIMEZONE", "Asia/Shanghai")
    scheduler = BlockingScheduler(timezone=timezone)
    trigger = (
        CronTrigger.from_crontab(args.cron, timezone=timezone)
        if args.cron
        else IntervalTrigger(minutes=args.interval_minutes, timezone=timezone)
    )
    scheduler.add_job(
        execute_monitor if args.command == "monitor-schedule" else execute,
        trigger=trigger,
        args=[args],
        id=f"topic-monitor-{args.topic}" if args.command == "monitor-schedule" else "policy-crawl",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    print(f"定时任务已启动，时区：{timezone}。按 Ctrl+C 停止。")
    scheduler.start()


if __name__ == "__main__":
    main()
