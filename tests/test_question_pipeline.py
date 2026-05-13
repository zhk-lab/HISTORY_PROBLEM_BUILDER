from __future__ import annotations

import csv
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from history_question_builder.event_crawler.models import CandidateEvent
from history_question_builder.question_asker.agent import parse_agent_output
from history_question_builder.question_asker.models import (
    CANDIDATE_FIELDNAMES,
    REJECTED_FIELDNAMES,
    QuestionCandidate,
    RejectedQuestionEvent,
)
from history_question_builder.question_asker.pipeline import load_events_jsonl
from history_question_builder.question_asker.screening import screen_event
from history_question_builder.question_asker.storage import (
    write_question_candidates_csv,
    write_question_candidates_jsonl,
    write_rejected_question_events_jsonl,
)
from history_question_builder.question_asker.validation import validate_question


class QuestionPipelineTests(unittest.TestCase):
    def test_candidate_and_rejected_field_shapes(self) -> None:
        event = _event(
            source="ifes_electionguide",
            domain="politics",
            title="2026 Example general election",
            summary="Official results show Party A won the most seats.",
            evidence_urls=["https://www.electionguide.org/elections/id/1/"],
        )
        payload = parse_agent_output(
            json.dumps(
                {
                    "event_name": "2026 Example general election",
                    "domain": "politics",
                    "question": "On 2026-05-01, which party will win the most seats?",
                    "prediction_date": "2026-04-30",
                    "ground_truth": "Party A",
                    "resolution_source": "https://www.electionguide.org/elections/id/1/",
                }
            )
        ).candidate
        self.assertIsNotNone(payload)
        candidate = QuestionCandidate.from_agent_payload(event, payload)
        rejected = RejectedQuestionEvent.from_event(
            event, reject_stage="pre_screen", reject_reason="test reject"
        )

        temp = _test_output_dir()
        try:
            candidates_jsonl = temp / "candidates.jsonl"
            candidates_csv = temp / "candidates.csv"
            rejected_jsonl = temp / "rejected.jsonl"
            write_question_candidates_jsonl(candidates_jsonl, [candidate])
            write_question_candidates_csv(candidates_csv, [candidate])
            write_rejected_question_events_jsonl(rejected_jsonl, [rejected])

            candidate_json = json.loads(candidates_jsonl.read_text(encoding="utf-8"))
            rejected_json = json.loads(rejected_jsonl.read_text(encoding="utf-8"))
            self.assertEqual(list(candidate_json.keys()), CANDIDATE_FIELDNAMES)
            self.assertEqual(list(rejected_json.keys()), REJECTED_FIELDNAMES)

            with candidates_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                self.assertEqual(next(reader), CANDIDATE_FIELDNAMES)
        finally:
            _cleanup_test_files(
                temp / "candidates.jsonl",
                temp / "candidates.csv",
                temp / "rejected.jsonl",
            )

    def test_pre_screen_keeps_high_value_sources(self) -> None:
        event = _event(
            source="fomc_calendar",
            domain="macro",
            title="FOMC meeting (May 1, 2026)",
            summary="Federal Open Market Committee scheduled meeting date.",
        )
        decision = screen_event(event)
        self.assertTrue(decision.selected)
        self.assertEqual(decision.priority, "high")

    def test_pre_screen_rejects_immediate_news(self) -> None:
        event = _event(
            source="wikipedia_current_events",
            domain="other",
            title="City protest",
            summary="Police arrested 20 people during a protest.",
        )
        decision = screen_event(event)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "immediate_news_without_future_result")

    def test_agent_parse_candidate_and_rejected(self) -> None:
        candidate = parse_agent_output(
            """```json
            {
              "event_name": "FOMC meeting",
              "domain": "macro",
              "question": "On 2026-05-01, what will the federal funds target range be?",
              "prediction_date": "2026-04-30",
              "ground_truth": "4.25%-4.50%",
              "resolution_source": "https://www.federalreserve.gov/"
            }
            ```"""
        )
        rejected = parse_agent_output('{"reject_reason": "No natural prediction point."}')
        bad = parse_agent_output("not json")

        self.assertEqual(candidate.status, "candidate")
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(bad.status, "parse_error")

    def test_validation_flags_common_risks(self) -> None:
        event = _event(
            source="wikipedia_current_events",
            domain="other",
            title="City protest",
            summary="Police arrested 20 people during a protest.",
            source_url=None,
            evidence_urls=[],
        )
        payload = parse_agent_output(
            json.dumps(
                {
                    "event_name": "City protest",
                    "domain": "politics",
                    "question": "Will the protest be successful?",
                    "prediction_date": "2026-05-01",
                    "ground_truth": "",
                    "resolution_source": "",
                }
            )
        ).candidate
        self.assertIsNotNone(payload)
        candidate = QuestionCandidate.from_agent_payload(event, payload)
        flags = validate_question(event, candidate)

        self.assertIn("ambiguous_time_boundary", flags)
        self.assertIn("question_contains_vague_words", flags)
        self.assertIn("weak_or_missing_resolution_source", flags)
        self.assertIn("ground_truth_not_direct_answer", flags)

    def test_validation_flags_mechanical_structure_errors(self) -> None:
        event = _event(
            source="fomc_calendar",
            domain="macro",
            title="FOMC meeting",
            summary="Federal Reserve decision summary.",
        )
        candidate = QuestionCandidate(
            question_id="test",
            event_id=event.event_id,
            domain="unknown",
            event_name="FOMC meeting",
            question="On 2026-05-01, what will happen?",
            prediction_date="2026-05-01",
            ground_truth="A" * 1201,
            resolution_source="not-a-url",
            event_summary="test event summary",
        )

        flags = validate_question(event, candidate)

        self.assertIn("invalid_question_domain", flags)
        self.assertIn("unclear_resolution_criteria", flags)
        self.assertIn("invalid_resolution_source_url", flags)
        self.assertIn("ground_truth_length_abnormal", flags)
        self.assertIn("prediction_date_may_be_invalid", flags)
        self.assertIn("needs_external_fact_check", flags)

    def test_validation_accepts_structurally_clean_question(self) -> None:
        event = _event(
            source="fomc_calendar",
            domain="macro",
            title="FOMC meeting",
            summary="Federal Reserve decision summary.",
            evidence_urls=["https://www.federalreserve.gov/monetarypolicy.htm"],
        )
        payload = parse_agent_output(
            json.dumps(
                {
                    "event_name": "FOMC meeting",
                    "domain": "macro",
                    "question": "On 2026-05-01, what will the federal funds target range be?",
                    "prediction_date": "2026-04-30",
                    "ground_truth": "4.25%-4.50%",
                    "resolution_source": "https://www.federalreserve.gov/monetarypolicy.htm",
                }
            )
        ).candidate
        self.assertIsNotNone(payload)
        candidate = QuestionCandidate.from_agent_payload(event, payload)

        self.assertEqual(validate_question(event, candidate), [])

    def test_load_events_jsonl(self) -> None:
        event = _event(
            source="ifes_electionguide",
            domain="politics",
            title="2026 Example election",
            summary="Election result summary.",
        )
        temp = _test_output_dir()
        path = temp / "events.jsonl"
        try:
            path.write_text(
                json.dumps(event.as_serializable_dict(), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            loaded = load_events_jsonl(path)
        finally:
            _cleanup_test_files(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].event_id, event.event_id)


def _event(
    *,
    source: str,
    domain: str,
    title: str,
    summary: str,
    source_url: str | None = "https://example.com/source",
    evidence_urls: list[str] | None = None,
) -> CandidateEvent:
    return CandidateEvent.from_source(
        source=source,
        event_date=date(2026, 5, 1),
        title=title,
        summary=summary,
        domain=domain,
        source_url=source_url,
        evidence_urls=["https://example.com/evidence"] if evidence_urls is None else evidence_urls,
    )


def _test_output_dir() -> Path:
    path = Path.cwd() / "data" / "questions" / "test_outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup_test_files(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


if __name__ == "__main__":
    unittest.main()
