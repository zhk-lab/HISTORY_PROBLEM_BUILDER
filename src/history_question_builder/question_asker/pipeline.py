from __future__ import annotations

"""CLI pipeline for generating reviewable historical prediction questions."""

import argparse
import re
from pathlib import Path

from dotenv import load_dotenv

from ..event_crawler.models import CandidateEvent
from .agent import build_question_agent
from .models import QuestionCandidate, RejectedQuestionEvent
from .screening import screen_event
from .storage import (
    ensure_question_output_dir,
    write_question_candidates_csv,
    write_question_candidates_jsonl,
    write_rejected_question_events_jsonl,
)
from .validation import validate_question


def parse_args() -> argparse.Namespace:
    """Define and parse question-pipeline CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Generate reviewable historical prediction question candidates."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Event JSONL path, e.g. data/event/events_2026-05-01_to_2026-05-09.jsonl.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/questions",
        help="Question output directory (default: data/questions).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of pre-screened events to send to the agent.",
    )
    parser.add_argument(
        "--agent-provider",
        default=None,
        choices=["mock", "chat_completions"],
        help="Generation provider. Defaults to QUESTION_AGENT_PROVIDER or mock.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name for chat_completions provider. Defaults to QUESTION_AGENT_MODEL.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Model temperature for chat_completions provider (default: 0.2).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Maximum retries after unparsable model output (default: 2).",
    )
    parser.add_argument(
        "--include-low-priority",
        action="store_true",
        help="Allow supplemental sports, entertainment, and cultural ranking events.",
    )
    return parser.parse_args()


def run() -> int:
    """Execute the end-to-end question generation pipeline."""
    load_dotenv()
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    ensure_question_output_dir(output_dir)

    provider = args.agent_provider or _env_default_provider()
    agent = build_question_agent(
        provider=provider,
        model=args.model,
        temperature=args.temperature,
        max_retries=args.max_retries,
    )

    events = load_events_jsonl(input_path)
    selected: list[CandidateEvent] = []
    rejected: list[RejectedQuestionEvent] = []

    for event in events:
        decision = screen_event(event, include_low_priority=args.include_low_priority)
        if decision.selected:
            selected.append(event)
            continue
        rejected.append(
            RejectedQuestionEvent.from_event(
                event,
                reject_stage="pre_screen",
                reject_reason=decision.reason,
            )
        )

    selected_for_agent = selected[: args.limit] if args.limit is not None else selected
    candidates: list[QuestionCandidate] = []

    for event in selected_for_agent:
        result = agent.generate(event)
        if result.status == "candidate" and result.candidate is not None:
            candidate = QuestionCandidate.from_agent_payload(event, result.candidate)
            candidate.risk_flags = validate_question(event, candidate)
            candidates.append(candidate)
            continue
        if result.status == "rejected":
            rejected.append(
                RejectedQuestionEvent.from_event(
                    event,
                    reject_stage="agent",
                    reject_reason=result.reject_reason or "agent rejected event",
                )
            )
            continue
        rejected.append(
            RejectedQuestionEvent.from_event(
                event,
                reject_stage="parse",
                reject_reason=result.reject_reason or "agent output parse error",
            )
        )

    label = _range_label_from_input(input_path)
    candidate_jsonl_path = output_dir / f"question_candidates_{label}.jsonl"
    candidate_csv_path = output_dir / f"question_candidates_{label}.csv"
    rejected_jsonl_path = output_dir / f"rejected_question_candidates_{label}.jsonl"

    write_question_candidates_jsonl(candidate_jsonl_path, candidates)
    write_question_candidates_csv(candidate_csv_path, candidates)
    write_rejected_question_events_jsonl(rejected_jsonl_path, rejected)

    print("")
    print("==== Question Pipeline Summary ====")
    print(f"input events: {len(events)}")
    print(f"selected by pre-screen: {len(selected)}")
    print(f"sent to agent: {len(selected_for_agent)}")
    print(f"question candidates: {len(candidates)}")
    print(f"rejected records: {len(rejected)}")
    print(f"candidate jsonl: {candidate_jsonl_path}")
    print(f"candidate csv:   {candidate_csv_path}")
    print(f"rejected jsonl:  {rejected_jsonl_path}")
    return 0


def load_events_jsonl(path: Path) -> list[CandidateEvent]:
    """Read CandidateEvent records from event JSONL."""
    events: list[CandidateEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(CandidateEvent.model_validate_json(stripped))
            except Exception as exc:  # noqa: BLE001
                raise SystemExit(f"Invalid event JSONL at line {line_number}: {exc}") from exc
    return events


def _range_label_from_input(path: Path) -> str:
    match = re.search(
        r"events_(\d{4}-\d{2}-\d{2}_to_\d{4}-\d{2}-\d{2})\.jsonl$",
        path.name,
    )
    if match:
        return match.group(1)
    return "custom"


def _env_default_provider() -> str:
    import os

    return os.getenv("QUESTION_AGENT_PROVIDER", "mock").strip() or "mock"


def main() -> int:
    """Standalone entry point."""
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
