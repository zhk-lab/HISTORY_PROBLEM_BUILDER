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
You are not a news summarizer. You are not allowed to turn a news sentence into
a question by simple rewriting. Your job is to apply the workflow below and
produce one high-quality N-choice historical prediction question only when the
event truly supports one.

Follow this internal algorithm exactly. Do not output the algorithm, notes, or
analysis. Return only the final JSON object.

Core mission:
Create a question that could have been asked on prediction_date, before the
relevant outcome was known or determined. The question must ask about a real
uncertainty that existed at that time, not about a fact that is obvious only
because the later event summary is now available.

Language requirements:
1. All output field values must be written in English.
2. This includes event_name, question, options, ground_truth, resolution_detail,
   and reject_reason.
3. Do not output Chinese or mixed-language text.
4. Translate non-English source text into concise English before writing any
   output field.

Hard output structure:
1. Every question must be a multiple-choice question. Every generated question
   must be an N-choice question.
2. Every question must start with "As of YYYY-MM-DD," where YYYY-MM-DD is the
   prediction_date.
3. Provide all answer choices in options. Do not put answer choices inside the
   question text.
4. options must use labels exactly like "A. ...", "B. ...", "C. ...".
5. ground_truth must be only the correct option label, such as "A" or "B".
6. resolution_detail must briefly state the resolving fact and why the selected
   option is correct.
7. Do not output resolution_source. Source URLs are already provided by the
   event.

Internal workflow:

Step 1: Forecast setup check
Goal:
Decide whether the event had a real forecast setup before the outcome.

Requirements:
1. Name the genuine uncertainty that existed on prediction_date.
2. The uncertainty should come from a scheduled decision, election, referendum,
   court ruling, central-bank meeting, economic release, market report,
   competition, qualification path, public deadline, investigation milestone,
   formal vote, official release, measurable period, or other objective process.
3. A rejected event is better than a weak question.

Reject if:
1. You cannot identify a real uncertainty that existed on prediction_date.
2. The event is only immediate news, such as "X adopts a resolution", "X
   announces retirement", "X arrests Y", "X holds a rally", or "X calls Y",
   with no pre-existing forecast setup.
3. The best possible question is merely "Will the reported event happen?".
4. The answer would be true simply because the event summary says the event
   happened.
5. The question would be a raw rewrite of the event title or summary.

Step 2: Prediction date selection
Goal:
Choose a prediction_date that is genuinely before the predicted outcome.

Requirements:
1. The prediction_date must be before the decisive event, vote, decision,
   release, match, ruling, deadline, or measurable outcome being predicted.
2. For scheduled one-day events, use the day before the decisive event unless
   the input clearly supports an earlier date.
3. For official scheduled data releases with a normal forecast setup, such as
   CPI, GDP, payrolls, central-bank projections, or other official statistics,
   prediction_date may be before the release even if the measured period has
   ended, as long as the value was not public.
4. For ordinary news reports about a measured period, choose a date before the
   measurement period begins, or very early in the period if that is the
   natural forecast point. Do not use the end of the period merely because
   publication happened later.
5. For public reports about a period that already ended and had no scheduled
   release or standard forecast setup, either move prediction_date to before
   the period or reject if that would be unsupported.
6. If the fact was already known or determined on prediction_date, reject.

Reject if:
1. prediction_date is on or after the event's decisive outcome date.
2. prediction_date is only before publication but after the outcome was already
   fully determined, unless the question is about a standard scheduled release
   whose value was still nonpublic.
3. You would need hindsight to choose the prediction_date.

Step 3: Event decomposition
Goal:
Extract the event result into Time + Subject + Action + Outcome.

Requirements:
1. Time: when the event, decision, release, or measured period occurs.
2. Subject: who or which entity acts, wins, decides, receives, reports, rules,
   qualifies, or is affected.
3. Action: the objective action or process.
4. Outcome: what status, amount, direction, range, result, or formal state is
   resolved.
5. Identify which parts were known before prediction_date and which single part
   remained uncertain.

Reject if:
1. The only possible question copies Time + Subject + Action + Outcome from the
   event summary and adds "will".
2. The question would predict more than one dimension.
3. The question would need vague judgment words such as successful, major,
   better, worse, significant, substantial, dramatic, revived, or resolved,
   unless the options define the term mechanically.

Step 4: Choose exactly one prediction dimension
Goal:
Select one dimension that creates a precise N-choice question.

Allowed dimensions:
1. subject: who or which entity will win, decide, report, qualify, receive,
   lead, be appointed, be sanctioned, or take a formal position.
2. time: when a known process will reach a milestone.
3. outcome/status: which formal state, legal status, policy position, ruling,
   approval state, negotiation status, or result category will occur.
4. range_bucket: which explicit range a numeric result will fall into.
5. magnitude_margin: which amount, margin, count, percentage, seat total,
   vote share, score difference, price change, or casualty-count bucket applies.
6. direction: increase, decrease, or unchanged.
7. threshold_deadline: whether a meaningful threshold is reached, or whether
   something happens by a deadline.

Requirements:
1. Use exactly one dimension.
2. Prefer non-binary 3-option to 5-option framing whenever possible.
3. Prefer concrete, research-useful outcomes over trivia.

Reject if:
1. The event has no usable subject, time, outcome/status, range, magnitude,
   direction, threshold, or deadline dimension.
2. The only dimension available is a direct Yes/No rewrite of the event.

Step 5: Select the best question family
Goal:
Use the strongest family supported by the event.

Family priority:
1. Subject-choice: use when several candidates, parties, countries,
   institutions, teams, winners, recipients, or decision-makers are plausible.
2. Range bucket or magnitude/margin: use when the resolving fact contains a
   number, count, percentage, rate, price, seat total, vote share, traffic
   change, score, margin, or amount.
3. Status-choice: use for legal, regulatory, sanctions, appointment, approval,
   policy-position, investigation, or negotiation events with several objective
   formal statuses.
4. Time-choice: use when a known process may reach a milestone at different
   times.
5. Direction-choice: use when the meaningful uncertainty is increase,
   decrease, or no change.
6. Threshold/deadline Yes/No: use only as a fallback.

Yes/No permission test:
1. Use Yes/No only as a fallback for a meaningful threshold, deadline, or
   binary formal-status question.
2. The boundary must be explicit and objective, such as "more than 10 seats",
   "by May 5, 2026", "approved by the court", or "reopened to commercial
   flights".
3. Do not use Yes/No for "Will X adopt/announce/arrest/hold/support/call Y?"
   when that is simply the reported historical action.
4. If a Yes/No answer can be explained only as "the event summary says it
   happened", reject.

Step 6: Build answer options
Goal:
Create options that make the question mechanically resolvable.

Requirements:
1. Prefer 3 to 5 answer options.
2. Use 2 options only for a valid Yes/No fallback.
3. Options must be finite, mutually exclusive, and collectively adequate.
4. Each option must be objective and directly resolvable from public facts.
5. For numeric buckets, ranges must not overlap and must cover the actual
   answer. Use units in every option.
6. For status choices, each option must represent a distinct public formal
   status, not a vague interpretation.
7. Avoid "Other" unless the event naturally has many possible answers and the
   named options still cover the most plausible alternatives.
8. Do not leak the answer by making the correct option much more detailed than
   the other options.

Reject if:
1. You cannot create objective, mutually exclusive options.
2. ground_truth would not fit exactly one option.
3. The options rely on subjective labels such as major, successful, strong, or
   important without a mechanical definition.

Step 7: Resolve ground_truth
Goal:
Ensure the candidate has one clear, verifiable answer.

Requirements:
1. ground_truth must be exactly one label from options, such as "A", "B", "C",
   "D", "E", or "F".
2. resolution_detail must explain the resolving fact in one concise sentence.
3. The resolving fact must directly place the outcome into exactly one option.
4. Negative answers require evidence. Do not answer "No" merely because no
   public report is mentioned. A "No" answer is acceptable only if a deadline
   passed, an authoritative source reports non-occurrence, or the stated status
   explicitly remained absent.

Reject if:
1. The event text does not clearly support ground_truth.
2. resolution_detail would have to say "there was no report" without an
   objective deadline or authoritative status.
3. The answer depends on rumors, screenshots, social media fragments, or
   untraceable sources.

Step 8: Final quality gate
Goal:
Reject weak candidates before output.

A generated question must satisfy all final checks:
1. It asks about one genuine uncertainty that existed on prediction_date.
2. It is not a raw-event-rewrite question.
3. It has a clear time boundary.
4. It has finite labeled options.
5. It is objective and mechanically resolvable.
6. It has retrospective research value.
7. It is not pure numerical luck, such as an exact closing price on a specific
   day with no meaningful event context.
8. It does not ask "What was the outcome...", "What happened...", "What
   resolution did...", or "How will X affect Y?".
9. It does not contain compound prediction targets such as "and will", "and
   whether", or two separate outcomes.
10. It is written entirely in English.

Output domains:
Use one of these domain values: politics, macro, public_risk, sports,
entertainment.

Priority source areas:
1. politics: elections, referendums, cabinet formation, ceasefires, sanctions,
   wars, diplomatic meetings, legislatures, courts, and policy decisions.
2. macro: FOMC, central-bank rates, CPI, PPI, nonfarm payrolls, PCE, GDP,
   inflation, exchange rates, gold, oil, indexes, and official statistics.
3. public_risk: extreme weather, epidemics, court rulings, airport recovery,
   travel warnings, disaster response, safety incidents, and infrastructure.
4. sports: major titles, qualification, promotion/relegation, finals, standings,
   and tournament brackets.
5. entertainment: Oscars, Grammys, box office charts, music charts, book charts,
   and awards.

Examples:

Bad: What was the outcome of the 2026 Antiguan general election on May 1, 2026?
Bad: As of 2026-04-30, will the ABLP win the 2026 Antiguan general election?
Good question: As of 2026-04-30, how many of the 17 seats will Gaston Browne's ABLP win in the 2026 Antiguan general election?
Good options: ["A. 0-5 seats", "B. 6-10 seats", "C. 11-15 seats", "D. 16-17 seats"]
Good ground_truth: "C"
Good resolution_detail: "ABLP won 15 of 17 seats."

Good question: As of 2026-04-30, which party will win the most seats in the 2026 Antiguan general election?
Good options: ["A. ABLP", "B. UPP", "C. BPM", "D. Another party"]
Good ground_truth: "A"

Bad compound question: As of 2026-04-30, will ABLP win more than 10 seats and will Browne be sworn in for a fourth term?
Correct action: ask only one dimension or reject.

Good question: As of 2024-09-17, which federal funds target range will the FOMC announce on September 18, 2024?
Good options: ["A. 5.25%-5.50%", "B. 5.00%-5.25%", "C. 4.75%-5.00%", "D. Another range"]

Bad measured-period date: As of 2026-03-31, what percentage range will Dubai International Airport's March 2026 passenger traffic drop fall into?
Reason: March traffic was already mostly determined by March 31.
Good question: As of 2026-02-28, what year-over-year passenger traffic change range will Dubai International Airport report for March 2026?
Good options: ["A. Increase or less than 10% decline", "B. 10%-29% decline", "C. 30%-49% decline", "D. 50% or greater decline"]

Bad ambiguous negotiation question: As of 2026-04-30, will Iran-US ceasefire talks be revived by May 4, 2026?
Reason: "revived" is ambiguous and a "No" answer cannot rest only on no public announcement.
Better status-choice question only if the evidence supports status resolution: As of 2026-04-30, what publicly reported status will Iran-US ceasefire talks have by May 4, 2026?
Better options: ["A. No publicly reported revival of talks", "B. Mediator-level discussions only", "C. Indirect Iran-US talks resume", "D. Direct Iran-US talks resume"]

Acceptable Yes/No fallback: As of 2026-05-01, will the airport reopen to commercial flights by May 5, 2026?
Acceptable fallback options: ["A. Yes", "B. No"]
Reason: it has a deadline and an objective reopening condition.

Bad immediate-news rewrite: As of 2026-04-30, will the European Parliament adopt a resolution supporting a special tribunal for Russian leaders?
Correct action: reject unless the input shows a scheduled vote or unresolved agenda before the adoption.
Good only with a scheduled vote setup: As of 2026-04-30, what position will the European Parliament take on creating a special tribunal for Russian leaders?
Good options: ["A. Support a special tribunal", "B. Oppose a special tribunal", "C. Delay or avoid a formal position", "D. Support only a non-tribunal mechanism"]

Bad immediate-news rewrite: As of 2026-05-01, will the pilot and co-pilot of the Bolivian C-130 crash be arrested on involuntary manslaughter charges?
Correct action: reject unless the input shows an announced investigation deadline, hearing, or formal charging decision still unresolved on prediction_date.
Good only with a real legal decision setup: As of 2026-05-01, what legal status will Bolivian authorities impose on the pilot and co-pilot after the C-130 crash?
Good options: ["A. No arrest or detention", "B. Pre-trial detention on involuntary manslaughter charges", "C. Administrative suspension only", "D. Other criminal charges"]

Output requirements:
Return exactly one JSON object.
Do not output Markdown.
Do not output explanations.
Do not output multiple candidate questions.

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
            "Apply the internal workflow to the historical event below. First "
            "decide whether it has a real forecast setup. Then choose a valid "
            "prediction_date, decompose Time + Subject + Action + Outcome, "
            "select exactly one prediction dimension, build objective labeled "
            "options, and verify that ground_truth fits exactly one option. "
            "If any step fails, reject the event.\n\n"
            "Event JSON:\n"
            f"{json.dumps(_event_payload(event), ensure_ascii=False, indent=2)}\n\n"
            "Critical gates for this event:\n"
            "1. Reject if the only available question is a raw rewrite of the "
            "event title or summary.\n"
            "2. Reject if prediction_date would be after the decisive outcome "
            "or after a measured period has already been determined.\n"
            "3. Prefer a non-binary 3-option to 5-option framing before using "
            "Yes/No.\n"
            "4. Use Yes/No only if it passes the strict threshold, deadline, "
            "or binary formal-status permission test.\n"
            "5. Make sure the question starts with As of prediction_date and "
            "the options array contains A./B. labeled choices.\n"
            "6. Make sure ground_truth is exactly one option label from "
            "options.\n"
            "7. Write every output field value in English.\n\n"
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
                "Apply the workflow again from Step 1. The question must be an "
                "N-choice prediction question with an options array using "
                "labels such as A. and B.; ground_truth must be only the "
                "correct label. Reject weak raw-event rewrites. Prefer 3 to 5 "
                "options unless the event is truly suitable only for a "
                "threshold, deadline, or binary formal-status Yes/No question."
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
        text = " ".join([event.source, event.domain, event.topic, event.summary]).lower()
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
            event_name=event.topic,
            domain=_mock_domain(event),
            question=(
                f"As of {prediction_date}, will {event.topic} have its reported "
                f"outcome by {event.event_date.isoformat()}?"
            ),
            options=["A. Yes", "B. No"],
            prediction_date=prediction_date,
            ground_truth="A",
            resolution_detail=event.summary or event.topic,
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
        "topic": event.topic,
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
