from __future__ import annotations

"""Question-generation agents and prompt construction."""

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import requests
from pydantic import ValidationError

from ..event_crawler.models import CandidateEvent
from .models import AgentCandidatePayload, AgentResult, AgentRejectedPayload


SYSTEM_PROMPT = """You are a historical prediction question builder.
Your task is not to summarize news and not to rewrite news into a factual
question. Your task is to decide whether a historical event can be turned into
an N-choice prediction question that could have been asked before the outcome
was publicly known.

You must follow these rules strictly.

Language requirements:
1. All output field values must be written in English.
2. This includes event_name, question, options, ground_truth, resolution_detail,
   and reject_reason.
3. Do not output Chinese or mixed-language text.
4. If the source event contains non-English text, translate it into concise
   English before writing any output field.

Hard question structure:
1. Every question must be a multiple-choice question.
2. Yes/No is a valid two-option multiple-choice question.
3. The finite options must be provided in the options array.
4. The options must be mutually exclusive and directly resolvable.
5. Each question must predict exactly one outcome dimension.
6. Do not create compound questions with "and will", "and whether", or two
   separate prediction targets.
7. The question must start with "As of YYYY-MM-DD," where YYYY-MM-DD is the
   prediction_date.
8. Do not include the answer options inside question. Put them only in options.
9. options must use labels exactly like "A. ...", "B. ...", "C. ...".
10. ground_truth must be only the correct option label, such as "A" or "B".

How to design a question:
1. First decompose the event result into Time + Subject + Action + Outcome.
2. Choose exactly one prediction dimension:
   - time: when something will happen.
   - subject: who or which entity will do, win, decide, or receive something.
   - outcome: what status, amount, direction, range, or result will occur.
3. If the chosen dimension is discrete, list its possible values as options.
4. If the chosen dimension is continuous or open-ended, discretize it using one
   of these methods:
   - direction: increase, decrease, or unchanged.
   - threshold_deadline: whether a bottom line is reached, or whether something
     happens by a deadline.
   - range_bucket: which explicit range or bucket the result falls into.
   - magnitude_margin: which amount, margin, or difference bucket applies.

A question may be generated only if all of these conditions are met:
1. At the end of prediction_date, the answer was still genuinely uncertain.
2. A clear ground_truth later became available.
3. The question has a clear time boundary.
4. The prediction target is specific.
5. The answer options are finite, mutually exclusive, and objective.
6. The ground_truth can be verified from public sources, or at least checked
   reliably.
7. The question has retrospective value and is not random trivia.
8. The question is not merely asking for a fact that had already happened.

Reject the event if any of these conditions apply:
1. The event is only immediate news that already happened, with no natural
   future outcome point.
2. The question can only be created through hindsight, because the answer was
   already known on prediction_date.
3. The answer depends on rumors, screenshots, social media fragments, or
   sources that cannot be traced.
4. The question relies on vague judgments such as whether something was
   successful, major, better, or clearly improved.
5. The question is pure numerical luck, such as predicting the exact closing
   price of a stock on a specific day.
6. The question is too private, too niche, or low in research value.
7. You cannot determine whether ground_truth directly answers question.
8. The event has no public source URL in source_url or evidence_urls.

Priority domains:
1. politics: elections, referendums, cabinet formation, ceasefires, sanctions,
   wars, and diplomatic meetings.
2. macro: FOMC, central bank rates, CPI, PPI, nonfarm payrolls, PCE, GDP,
   inflation, exchange rates, gold, oil, and indexes.
3. public_risk: extreme weather, epidemic reports, court rulings, airport
   recovery, travel warnings, and disaster response.
4. sports: major titles, qualification, promotion/relegation, and finals.
5. entertainment: Oscars, Grammys, box office charts, music charts, and book
   charts.

Banned question forms:
1. Do not ask "What was the outcome...".
2. Do not ask "What happened...".
3. Do not ask "What resolution did...".
4. Do not ask any open-ended retrospective factual question.
5. Do not ask broad impact questions such as "How will X affect Y?".

Good and bad examples:
Bad: What was the outcome of the 2026 Antiguan general election on May 1, 2026?
Good question: As of 2026-04-30, will Gaston Browne's ABLP win more than 10 of the 17 seats in the 2026 Antiguan general election?
Good options: ["A. Yes", "B. No"]
Good ground_truth: "A"
Good resolution_detail: "ABLP won 15 of 17 seats."
Good question: As of 2026-04-30, which party will win the most seats in the 2026 Antiguan general election?
Good options: ["A. ABLP", "B. UPP", "C. BPM", "D. Another party"]
Good ground_truth: "A"
Bad: As of 2026-04-30, will ABLP win more than 10 seats and will Browne be sworn in for a fourth term?
Good question: As of 2024-09-17, which federal funds target range will the FOMC announce on September 18, 2024?
Good options: ["A. 5.25%-5.50%", "B. 5.00%-5.25%", "C. 4.75%-5.00%", "D. Another range"]
Good question: As of 2026-05-01, will the airport reopen to commercial flights by May 5, 2026?
Good options: ["A. Yes", "B. No"]

Output requirements:
Return exactly one JSON object.
Do not output Markdown.
Do not output explanations.
Do not output multiple candidate questions.
ground_truth must be exactly one option label from options, such as "A", "B",
"C", "D", "E", or "F".
resolution_detail must briefly explain why ground_truth is correct.
Do not output resolution_source. Source URLs are already provided by the event.

If a question can be generated, output:
{
  "event_name": "...",
  "domain": "politics | macro | public_risk | sports | entertainment",
  "question": "...",
  "options": ["A. ...", "B. ..."],
  "prediction_date": "YYYY-MM-DD",
  "ground_truth": "A",
  "resolution_detail": "..."
}

If no question should be generated, output:
{
  "reject_reason": "..."
}
"""


@dataclass(frozen=True)
class PromptBuilder:
    """Build prompts for the question-generation agent."""

    def build_messages(
        self, event: CandidateEvent, *, parse_error: str | None = None
    ) -> list[dict[str, str]]:
        user_prompt = (
            "Decide whether the historical event below can be turned into a "
            "valid N-choice historical prediction question.\n\n"
            "Event JSON:\n"
            f"{json.dumps(_event_payload(event), ensure_ascii=False, indent=2)}\n\n"
            "Check especially:\n"
            "1. whether the event had a natural prediction point before the "
            "outcome was public;\n"
            "2. what prediction_date should be;\n"
            "3. whether ground_truth is clearly present in the event text or "
            "sources;\n"
            "4. whether question leaks the later answer;\n"
            "5. whether the event can be decomposed into Time + Subject + "
            "Action + Outcome;\n"
            "6. which single dimension should be predicted: time, subject, or "
            "outcome;\n"
            "7. whether continuous dimensions are discretized by direction, "
            "threshold_deadline, range_bucket, or magnitude_margin;\n"
            "8. whether the question starts with As of prediction_date and "
            "the options array contains A./B. labeled choices;\n"
            "9. whether ground_truth is exactly one option label from options;\n"
            "10. whether every output field value is written in English.\n\n"
            "Return only one JSON object."
        )
        if parse_error:
            user_prompt += (
                "\n\nYour previous output could not be parsed as schema-valid "
                "JSON.\n"
                f"Parse error: {parse_error}\n\n"
                "Return one JSON object again.\n"
                "Do not use Markdown.\n"
                "Do not add any explanatory text.\n"
                "Write all output field values in English.\n"
                "The question must be an N-choice prediction question with an "
                "options array using labels such as A. and B.; ground_truth "
                "must be only the correct label."
            )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]


class QuestionAgent(ABC):
    """Abstract generation agent."""

    @abstractmethod
    def generate(self, event: CandidateEvent) -> AgentResult:
        """Generate or reject one question candidate for an event."""


class MockQuestionAgent(QuestionAgent):
    """Deterministic local agent for tests and dry runs."""

    def generate(self, event: CandidateEvent) -> AgentResult:
        text = " ".join([event.source, event.domain, event.title, event.summary]).lower()
        if not any(
            token in text
            for token in [
                "election",
                "fomc",
                "interest rate",
                "cpi",
                "gdp",
                "inflation",
                "referendum",
                "court",
                "verdict",
            ]
        ):
            return AgentResult(
                status="rejected",
                reject_reason="mock agent only generates for clear scheduled or result-oriented events",
            )

        prediction_date = (event.event_date - timedelta(days=1)).isoformat()
        payload = AgentCandidatePayload(
            event_name=event.title,
            domain=_mock_domain(event),
            question=(
                f"As of {prediction_date}, will {event.title} have its reported "
                f"outcome by {event.event_date.isoformat()}?"
            ),
            options=["A. Yes", "B. No"],
            prediction_date=prediction_date,
            ground_truth="A",
            resolution_detail=event.summary or event.title,
        )
        return AgentResult(status="candidate", candidate=payload)


class ChatCompletionsQuestionAgent(QuestionAgent):
    """Agent backed by a Chat Completions-compatible endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0.2,
        max_retries: int = 2,
        timeout_seconds: int = 60,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.prompt_builder = prompt_builder or PromptBuilder()

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_retries: int = 2,
    ) -> "ChatCompletionsQuestionAgent":
        resolved_model = model or os.getenv("QUESTION_AGENT_MODEL")
        if not resolved_model:
            raise ValueError("--model or QUESTION_AGENT_MODEL is required")
        api_key = os.getenv("QUESTION_AGENT_API_KEY")
        if not api_key:
            raise ValueError(
                "QUESTION_AGENT_API_KEY is required for chat_completions provider"
            )
        base_url = os.getenv(
            "QUESTION_AGENT_BASE_URL",
            "https://api.openai.com/v1/chat/completions",
        )
        resolved_temperature = (
            temperature
            if temperature is not None
            else float(os.getenv("QUESTION_AGENT_TEMPERATURE", "0.2"))
        )
        return cls(
            model=resolved_model,
            api_key=api_key,
            base_url=base_url,
            temperature=resolved_temperature,
            max_retries=max_retries,
        )

    def generate(self, event: CandidateEvent) -> AgentResult:
        parse_error: str | None = None
        raw_output = ""
        for _attempt in range(self.max_retries + 1):
            try:
                raw_output = self._call_model(event, parse_error=parse_error)
            except requests.RequestException as exc:
                return AgentResult(
                    status="parse_error",
                    reject_reason=f"model request failed: {exc}",
                    raw_output=raw_output,
                )
            result = parse_agent_output(raw_output)
            if result.status != "parse_error":
                return result
            parse_error = result.reject_reason
        return AgentResult(
            status="parse_error",
            reject_reason=parse_error or "model output could not be parsed",
            raw_output=raw_output,
        )

    def _call_model(self, event: CandidateEvent, *, parse_error: str | None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": self.prompt_builder.build_messages(event, parse_error=parse_error),
        }
        response = requests.post(
            _chat_completions_url(self.base_url),
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"])


def build_question_agent(
    *, provider: str, model: str | None, temperature: float, max_retries: int
) -> QuestionAgent:
    """Factory used by the CLI."""
    if provider == "mock":
        return MockQuestionAgent()
    if provider == "chat_completions":
        return ChatCompletionsQuestionAgent.from_env(
            model=model, temperature=temperature, max_retries=max_retries
        )
    raise ValueError(f"Unknown agent provider: {provider}")


def parse_agent_output(raw_output: str) -> AgentResult:
    """Parse strict JSON candidate/rejection output from the model."""
    try:
        data = json.loads(_extract_json_text(raw_output))
    except json.JSONDecodeError as exc:
        return AgentResult(
            status="parse_error",
            reject_reason=f"invalid JSON: {exc.msg}",
            raw_output=raw_output,
        )
    if not isinstance(data, dict):
        return AgentResult(
            status="parse_error",
            reject_reason="model output must be a JSON object",
            raw_output=raw_output,
        )
    if "reject_reason" in data and not _has_candidate_fields(data):
        try:
            rejected = AgentRejectedPayload.model_validate(data)
        except ValidationError as exc:
            return AgentResult(
                status="parse_error",
                reject_reason=f"invalid rejected payload: {exc}",
                raw_output=raw_output,
            )
        return AgentResult(
            status="rejected",
            reject_reason=rejected.reject_reason,
            raw_output=raw_output,
        )
    try:
        candidate = AgentCandidatePayload.model_validate(data)
        _validate_candidate_date(candidate.prediction_date)
    except (ValidationError, ValueError) as exc:
        return AgentResult(
            status="parse_error",
            reject_reason=f"invalid candidate payload: {exc}",
            raw_output=raw_output,
        )
    return AgentResult(status="candidate", candidate=candidate, raw_output=raw_output)


def _event_payload(event: CandidateEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "source": event.source,
        "domain": event.domain,
        "event_date": event.event_date.isoformat(),
        "title": event.title,
        "summary": event.summary,
        "source_url": event.source_url,
        "evidence_urls": event.evidence_urls,
    }


def _extract_json_text(raw_output: str) -> str:
    cleaned = raw_output.strip()
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if code_block:
        return code_block.group(1).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        return cleaned[first : last + 1].strip()
    return cleaned


def _has_candidate_fields(data: dict[str, Any]) -> bool:
    required = {
        "event_name",
        "domain",
        "question",
        "options",
        "prediction_date",
        "ground_truth",
        "resolution_detail",
    }
    return any(field in data for field in required)


def _validate_candidate_date(value: str) -> None:
    from datetime import date

    date.fromisoformat(value)


def _chat_completions_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    if cleaned.endswith("/v1"):
        return f"{cleaned}/chat/completions"
    return cleaned


def _mock_domain(event: CandidateEvent) -> str:
    if event.domain == "macro":
        return "macro"
    if event.domain == "public_risk":
        return "public_risk"
    return "politics"
