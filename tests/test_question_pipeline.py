from __future__ import annotations

import csv
import json
import os
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from history_question_builder.event_crawler.models import CandidateEvent
from history_question_builder.question_asker.agent import (
    ChatCompletionsQuestionAgent,
    PromptBuilder,
    SYSTEM_PROMPT,
    build_question_agent,
    parse_agent_output,
)
from history_question_builder.question_asker.models import (
    CANDIDATE_FIELDNAMES,
    REJECTED_FIELDNAMES,
    QuestionCandidate,
    RejectedQuestionEvent,
)
from history_question_builder.question_asker.pipeline import (
    _event_dedupe_key,
    _question_dedupe_key,
    load_events_jsonl,
)
from history_question_builder.question_asker.screening import screen_event
from history_question_builder.question_asker.storage import (
    write_question_candidates_csv,
    write_question_candidates_jsonl,
    write_rejected_question_events_jsonl,
)
from history_question_builder.question_asker.validation import validate_question


class QuestionPipelineTests(unittest.TestCase):
    def test_candidate_and_rejected_field_shapes(self) -> None:
        self.assertEqual(
            CANDIDATE_FIELDNAMES,
            [
                "question",
                "options",
                "prediction_date",
                "ground_truth",
                "resolution_detail",
                "question_id",
                "event_id",
                "domain",
                "event_name",
                "event_summary",
                "source_urls",
                "risk_flags",
                "review_status",
                "review_notes",
            ],
        )
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
                    "question": "As of 2026-04-30, which party will win the most seats?",
                    "options": ["A. Party A", "B. Party B", "C. Another party"],
                    "prediction_date": "2026-04-30",
                    "ground_truth": "A",
                    "resolution_detail": "Party A won the most seats.",
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
              "question": "As of 2026-04-30, which federal funds target range will be announced?",
              "options": ["A. 4.25%-4.50%", "B. 4.50%-4.75%", "C. Another range"],
              "prediction_date": "2026-04-30",
              "ground_truth": "A",
              "resolution_detail": "The target range was 4.25%-4.50%."
            }
            ```"""
        )
        rejected = parse_agent_output('{"reject_reason": "No natural prediction point."}')
        bad = parse_agent_output("not json")

        self.assertEqual(candidate.status, "candidate")
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(bad.status, "parse_error")

    def test_prompt_instructions_are_english(self) -> None:
        event = _event(
            source="wikipedia_current_events",
            domain="politics",
            title="Example election",
            summary="Official results show Party A won.",
        )
        messages = PromptBuilder().build_messages(event)
        prompt_text = "\n".join(message["content"] for message in messages)

        self.assertNotRegex(SYSTEM_PROMPT, r"[\u3400-\u9fff]")
        self.assertNotRegex(prompt_text, r"[\u3400-\u9fff]")
        self.assertIn("All output field values must be written in English", SYSTEM_PROMPT)
        self.assertIn("Internal workflow", SYSTEM_PROMPT)
        self.assertIn("Step 1: Forecast setup check", SYSTEM_PROMPT)
        self.assertIn("Step 2: Prediction date selection", SYSTEM_PROMPT)
        self.assertIn("official scheduled data releases", SYSTEM_PROMPT)
        self.assertIn("Step 3: Event decomposition", SYSTEM_PROMPT)
        self.assertIn("Step 4: Choose exactly one prediction dimension", SYSTEM_PROMPT)
        self.assertIn("Step 5: Select the best question family", SYSTEM_PROMPT)
        self.assertIn("Step 6: Build answer options", SYSTEM_PROMPT)
        self.assertIn("Step 7: Resolve ground_truth", SYSTEM_PROMPT)
        self.assertIn("Step 8: Final quality gate", SYSTEM_PROMPT)
        self.assertIn("N-choice", SYSTEM_PROMPT)
        self.assertIn("Every question must be a multiple-choice question", SYSTEM_PROMPT)
        self.assertIn("Use Yes/No only as a fallback", SYSTEM_PROMPT)
        self.assertIn("Prefer 3 to 5 answer options", SYSTEM_PROMPT)
        self.assertIn("options must use labels exactly like", SYSTEM_PROMPT)
        self.assertIn("ground_truth must be only the correct option label", SYSTEM_PROMPT)
        self.assertIn("Time + Subject + Action + Outcome", SYSTEM_PROMPT)
        self.assertIn("time: when a known process", SYSTEM_PROMPT)
        self.assertIn("subject: who or which entity", SYSTEM_PROMPT)
        self.assertIn("outcome/status: which formal state", SYSTEM_PROMPT)
        self.assertIn("Direction-choice", SYSTEM_PROMPT)
        self.assertIn("threshold_deadline", SYSTEM_PROMPT)
        self.assertIn("range_bucket", SYSTEM_PROMPT)
        self.assertIn("magnitude_margin", SYSTEM_PROMPT)
        self.assertIn('start with "As of YYYY-MM-DD,"', SYSTEM_PROMPT)
        self.assertIn('It does not ask "What was the outcome', SYSTEM_PROMPT)
        self.assertIn("raw rewrite of the event title or summary", SYSTEM_PROMPT)
        self.assertIn("Negative answers require evidence", SYSTEM_PROMPT)
        self.assertIn("Yes/No permission test", SYSTEM_PROMPT)
        self.assertIn("Bad immediate-news rewrite", SYSTEM_PROMPT)
        self.assertIn("Correct action: reject", SYSTEM_PROMPT)
        self.assertIn("Bad: What was the outcome", SYSTEM_PROMPT)
        self.assertIn("Good question: As of 2026-04-30", SYSTEM_PROMPT)
        self.assertIn("Bad measured-period date", SYSTEM_PROMPT)
        self.assertIn("Bad ambiguous negotiation question", SYSTEM_PROMPT)
        self.assertIn("Apply the internal workflow", prompt_text)
        self.assertIn("non-binary 3-option to 5-option framing", prompt_text)
        self.assertIn("real forecast setup", prompt_text)
        self.assertIn("options array contains A./B. labeled choices", prompt_text)

    def test_chat_completions_agent_can_be_built_from_generic_env(self) -> None:
        original_values = {
            key: os.environ.get(key)
            for key in [
                "QUESTION_AGENT_API_KEY",
                "QUESTION_AGENT_BASE_URL",
                "QUESTION_AGENT_MODEL",
            ]
        }
        try:
            os.environ["QUESTION_AGENT_API_KEY"] = "test-key"
            os.environ["QUESTION_AGENT_BASE_URL"] = "https://vendor.example/v1"
            os.environ["QUESTION_AGENT_MODEL"] = "vendor-model"

            agent = build_question_agent(
                provider="chat_completions",
                model=None,
                temperature=0.2,
                max_retries=0,
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertIsInstance(agent, ChatCompletionsQuestionAgent)

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
                    "options": [],
                    "prediction_date": "2026-05-01",
                    "ground_truth": "",
                    "resolution_detail": "",
                }
            )
        ).candidate
        self.assertIsNotNone(payload)
        candidate = QuestionCandidate.from_agent_payload(event, payload)
        flags = validate_question(event, candidate)

        self.assertIn("ambiguous_time_boundary", flags)
        self.assertIn("question_contains_vague_words", flags)
        self.assertIn("missing_choice_options", flags)
        self.assertIn("weak_or_missing_source_urls", flags)
        self.assertIn("ground_truth_not_direct_answer", flags)

    def test_validation_flags_non_english_output(self) -> None:
        event = _event(
            source="wikipedia_current_events",
            domain="politics",
            title="Example election",
            summary="Official results show Party A won.",
        )
        candidate = QuestionCandidate(
            question_id="test",
            event_id=event.event_id,
            domain="politics",
            event_name="\u4f0a\u6717 ceasefire",
            question="On 2026-05-01, will \u4f0a\u6717 sign a ceasefire agreement?",
            options=["A. Yes", "B. No"],
            prediction_date="2026-04-30",
            ground_truth="B",
            resolution_detail="No agreement was signed.",
            event_summary="test event summary",
            source_urls="https://example.com/result",
        )

        flags = validate_question(event, candidate)

        self.assertIn("non_english_output", flags)

    def test_validation_flags_low_information_event_rewrite(self) -> None:
        event = _event(
            source="wikipedia_current_events",
            domain="politics",
            title="Russia-European Union relations",
            summary=(
                "The European Parliament adopts a resolution supporting the "
                "establishment of a special tribunal to prosecute Russian "
                "leaders for crimes related to the war in Ukraine."
            ),
        )
        weak_candidate = QuestionCandidate(
            question_id="test",
            event_id=event.event_id,
            domain="politics",
            event_name="Russia-European Union relations",
            question=(
                "As of 2026-04-30, will the European Parliament adopt a "
                "resolution supporting a special tribunal for Russian leaders "
                "related to the Ukraine war?"
            ),
            options=["A. Yes", "B. No"],
            prediction_date="2026-04-30",
            ground_truth="A",
            resolution_detail="The European Parliament adopted the resolution.",
            event_summary="test event summary",
            source_urls="https://example.com/result",
        )
        threshold_candidate = QuestionCandidate(
            question_id="test2",
            event_id="threshold-event",
            domain="politics",
            event_name="2026 Antiguan general election",
            question=(
                "As of 2026-04-30, will Gaston Browne's ABLP win more than "
                "10 of the 17 seats in the 2026 Antiguan general election?"
            ),
            options=["A. Yes", "B. No"],
            prediction_date="2026-04-30",
            ground_truth="A",
            resolution_detail="ABLP won 15 of 17 seats.",
            event_summary="test event summary",
            source_urls="https://example.com/result",
        )
        threshold_event = _event(
            source="wikipedia_current_events",
            domain="politics",
            title="2026 Antiguan general election",
            summary=(
                "Official results indicated that the ABLP led by Gaston "
                "Browne won 15 of 17 seats."
            ),
        )

        weak_flags = validate_question(event, weak_candidate)
        threshold_flags = validate_question(threshold_event, threshold_candidate)

        self.assertIn("low_information_event_rewrite", weak_flags)
        self.assertNotIn("low_information_event_rewrite", threshold_flags)

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
            options=["A. Yes", "A. Yes"],
            prediction_date="2026-05-01",
            ground_truth="C",
            resolution_detail="A" * 1201,
            event_summary="test event summary",
            source_urls="not-a-url",
        )

        flags = validate_question(event, candidate)

        self.assertIn("invalid_question_domain", flags)
        self.assertIn("unclear_resolution_criteria", flags)
        self.assertIn("duplicate_choice_options", flags)
        self.assertIn("invalid_choice_option_label", flags)
        self.assertIn("ground_truth_not_in_options", flags)
        self.assertIn("invalid_source_urls", flags)
        self.assertIn("resolution_detail_length_abnormal", flags)
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
                    "question": "As of 2026-04-30, which federal funds target range will be announced?",
                    "options": ["A. 4.25%-4.50%", "B. 4.50%-4.75%", "C. Another range"],
                    "prediction_date": "2026-04-30",
                    "ground_truth": "A",
                    "resolution_detail": "The target range was 4.25%-4.50%.",
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

    def test_dedupe_keys_ignore_event_title_and_question_punctuation(self) -> None:
        first = _event(
            source="wikipedia_current_events",
            domain="public_risk",
            title="Dubai International Airport",
            summary=(
                "Dubai International Airport reports a 66% drop in passenger "
                "traffic in March 2026."
            ),
        )
        duplicate = _event(
            source="wikipedia_current_events",
            domain="public_risk",
            title="Economic impact of the 2026 Iran war",
            summary=(
                "Dubai International Airport reports a 66% drop in passenger "
                "traffic in March 2026."
            ),
        )

        self.assertEqual(_event_dedupe_key(first), _event_dedupe_key(duplicate))
        self.assertEqual(
            _question_dedupe_key("As of 2026-02-28, what range?"),
            _question_dedupe_key("as of 2026 02 28 what range"),
        )


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
