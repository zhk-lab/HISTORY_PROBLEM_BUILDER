from __future__ import annotations

"""Storage helpers for question candidates and rejected events."""

import csv
import json
from pathlib import Path
from typing import Iterable

from .models import (
    CANDIDATE_FIELDNAMES,
    REJECTED_FIELDNAMES,
    QuestionCandidate,
    RejectedQuestionEvent,
)


def ensure_question_output_dir(output_dir: Path) -> None:
    """Ensure the question-output directory exists."""
    output_dir.mkdir(parents=True, exist_ok=True)


def write_question_candidates_jsonl(
    path: Path, candidates: Iterable[QuestionCandidate]
) -> None:
    """Write review candidates as JSONL with the public 13-field schema."""
    with path.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(
                json.dumps(candidate.as_serializable_dict(), ensure_ascii=False) + "\n"
            )


def write_question_candidates_csv(
    path: Path, candidates: list[QuestionCandidate]
) -> None:
    """Write review candidates as a UTF-8-SIG CSV for spreadsheet review."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDNAMES)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(candidate.as_csv_row())


def write_rejected_question_events_jsonl(
    path: Path, rejected_events: Iterable[RejectedQuestionEvent]
) -> None:
    """Write rejected event records separately from candidate questions."""
    with path.open("w", encoding="utf-8") as handle:
        for rejected in rejected_events:
            handle.write(
                json.dumps(rejected.as_serializable_dict(), ensure_ascii=False) + "\n"
            )
