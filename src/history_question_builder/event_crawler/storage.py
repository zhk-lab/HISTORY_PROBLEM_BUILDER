from __future__ import annotations

"""Persistence helpers for processed event outputs."""

import csv
import json
from pathlib import Path
from typing import Iterable

from .models import CandidateEvent


def ensure_output_dirs(event_output_dir: Path) -> None:
    """Ensure the event output directory exists."""
    event_output_dir.mkdir(parents=True, exist_ok=True)


def write_events_jsonl(path: Path, events: Iterable[CandidateEvent]) -> None:
    """Write events as JSONL, one event per line."""
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(
                json.dumps(event.as_serializable_dict(), ensure_ascii=False) + "\n"
            )


def write_events_csv(path: Path, events: list[CandidateEvent]) -> None:
    """Write events as a UTF-8-SIG CSV for spreadsheet review."""
    fieldnames = [
        "event_id",
        "source",
        "domain",
        "event_date",
        "topic",
        "summary",
        "source_url",
        "evidence_urls",
        "quality_flags",
        "filter_reason",
        "fetched_at",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            writer.writerow(event.as_csv_row())
