from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from policy_crawler.policy_portrait import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LLM_MODEL,
    rebuild_portrait_from_raw,
    run_portrait,
)
from policy_crawler.storage import PolicyRepository
from policy_crawler.topic_monitor import load_topic_profile


DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 PostgreSQL 正文生成产业政策画像")
    parser.add_argument("--topic", default="zhuchanye", help="config/topics 下的主题文件名")
    parser.add_argument("--topics-dir", type=Path, default=Path("config/topics"))
    parser.add_argument("--pages-dir", type=Path, help="可选的本地正文目录；默认从 PostgreSQL 读取")
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=positive_int,
        default=1024,
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("LLM_PROVIDER", DEFAULT_LLM_MODEL),
    )
    parser.add_argument("--similarity-threshold", type=float, default=0.42)
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument(
        "--reuse-run",
        type=Path,
        help="复用已有运行目录的原始 LLM 结果，仅重新校验与聚合",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
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
                api_key=os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "",
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
        repository.finish_run(run_id, {**result, "stored": stored, "files_retained": True})
    except Exception as exc:
        repository.finish_run(run_id, {"error": str(exc)}, status="failed")
        raise
    finally:
        repository.close()

    print(
        f"画像分析完成：文档 {result['document_count']}，section {result['section_count']}，"
        f"相关 section {result['selected_section_count']}，measure {result['measure_count']}"
    )
    print(f"输出目录：{result['output_dir']}")


if __name__ == "__main__":
    main()
