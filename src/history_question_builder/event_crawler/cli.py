from __future__ import annotations

"""命令行主流程：抓取 -> 筛选 -> 导出。"""

import argparse
from dataclasses import replace
from datetime import date
from pathlib import Path

from .config import DEFAULT_SOURCE_NAMES, DEFAULT_START_DATE, load_settings
from .filters import filter_and_enrich_events
from .http_client import build_session
from .models import CandidateEvent
from .sources import build_source_registry
from .sources.base import CrawlContext
from .storage import ensure_output_dirs, write_events_csv, write_events_jsonl


def parse_args() -> argparse.Namespace:
    """定义并解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Crawl candidate historical events from multiple sources."
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE.isoformat(),
        help="Start date in YYYY-MM-DD format (default: 2026-01-01).",
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="End date in YYYY-MM-DD format (default: today).",
    )
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCE_NAMES),
        help=(
            "Comma-separated sources from: "
            "wikipedia,gdelt,ifes,fomc,bls,fred,tradingeconomics,reliefweb."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="data/event",
        help="Event output directory (default: data/event).",
    )
    return parser.parse_args()


def _parse_iso_date(value: str, argument_name: str) -> date:
    """解析 YYYY-MM-DD；失败时给出明确参数错误。"""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid {argument_name}: {value}") from exc


def _parse_sources(value: str) -> list[str]:
    """解析逗号分隔的数据源 key 列表。"""
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def run() -> int:
    """CLI 执行入口主流程。"""
    args = parse_args()
    start_date = _parse_iso_date(args.start_date, "--start-date")
    end_date = _parse_iso_date(args.end_date, "--end-date")
    if end_date < start_date:
        raise SystemExit("--end-date must be after or equal to --start-date")

    # 在发起网络请求前先校验数据源名称。
    requested_sources = _parse_sources(args.sources)
    registry = build_source_registry()
    unknown_sources = [name for name in requested_sources if name not in registry]
    if unknown_sources:
        raise SystemExit(f"Unknown sources: {', '.join(unknown_sources)}")

    # 先加载运行配置，再用命令行路径覆盖默认输出路径。
    settings = load_settings(Path.cwd())
    settings = replace(
        settings,
        event_output_dir=(Path(args.output_dir)).resolve(),
    )
    ensure_output_dirs(settings.event_output_dir)

    context = CrawlContext(
        start_date=start_date,
        end_date=end_date,
        settings=settings,
    )
    session = build_session(settings.user_agent)

    all_events: list[CandidateEvent] = []
    source_stats: list[str] = []

    # 按来源逐个抓取；每个来源返回标准化事件和原始日志摘要。
    for source_name in requested_sources:
        crawler = registry[source_name]
        can_run, reason = crawler.can_run(context)
        if not can_run:
            print(f"[SKIP] {source_name}: {reason}")
            source_stats.append(f"{source_name}: skipped ({reason})")
            continue

        print(f"[RUN ] {source_name} ...")
        events, _raw_payloads = crawler.fetch(context, session)
        all_events.extend(events)
        source_stats.append(f"{source_name}: events={len(events)}")
        print(f"[DONE] {source_name}: {len(events)} events")

    # 应用基础筛选并排序，保证输出稳定可复现。
    filtered_events, dropped_events = filter_and_enrich_events(all_events)
    filtered_events.sort(key=lambda e: (e.event_date, e.source, e.title))
    dropped_events.sort(key=lambda e: (e.event_date, e.source, e.title))

    range_label = f"{start_date.isoformat()}_to_{end_date.isoformat()}"
    jsonl_path = settings.event_output_dir / f"events_{range_label}.jsonl"
    csv_path = settings.event_output_dir / f"events_{range_label}.csv"
    dropped_jsonl_path = settings.event_output_dir / f"dropped_events_{range_label}.jsonl"

    # 同时导出保留和丢弃结果，保证过程可追踪。
    write_events_jsonl(jsonl_path, filtered_events)
    write_events_csv(csv_path, filtered_events)
    write_events_jsonl(dropped_jsonl_path, dropped_events)

    print("")
    print("==== Crawl Summary ====")
    for line in source_stats:
        print(f"- {line}")
    print(f"total candidates: {len(all_events)}")
    print(f"kept after filters: {len(filtered_events)}")
    print(f"dropped by filters/dedup: {len(dropped_events)}")
    print(f"jsonl: {jsonl_path}")
    print(f"csv:   {csv_path}")
    print(f"dropped jsonl: {dropped_jsonl_path}")
    return 0


def main() -> int:
    """独立主入口，便于后续测试或复用。"""
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
