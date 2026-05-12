from __future__ import annotations

"""Models used by the historical question generation pipeline."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..event_crawler.models import CandidateEvent
from ..event_crawler.utils import clip_text


CANDIDATE_FIELDNAMES = [
    "question_id",
    "event_id",
    "domain",
    "event_name",
    "question",
    "prediction_date",
    "ground_truth",
    "resolution_source",
    "risk_flags",
    "event_summary",
    "source_urls",
    "review_status",
    "review_notes",
]

REJECTED_FIELDNAMES = [
    "event_id",
    "reject_stage",
    "reject_reason",
    "event_summary",
    "source_urls",
]

ALLOWED_QUESTION_DOMAINS = {
    "politics",
    "macro",
    "public_risk",
    "sports",
    "entertainment",
}


def build_question_id(event_id: str, question: str) -> str:
    """Build a stable id from the source event and question text."""
    payload = f"{event_id}|{question.strip().lower()}"
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:20]


def event_summary_text(event: CandidateEvent) -> str:
    """Return a compact event snapshot for review tables."""
    return " | ".join(
        [
            event.event_date.isoformat(),
            event.source,
            clip_text(event.title, limit=220),
            clip_text(event.summary, limit=900),
        ]
    )


def source_urls_text(event: CandidateEvent) -> str:
    """Return source and evidence URLs in a single human-review field."""
    urls = []
    if event.source_url:
        urls.append(event.source_url)
    urls.extend(event.evidence_urls)
    seen: set[str] = set()
    deduped = []
    for url in urls:
        cleaned = url.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return "; ".join(deduped)


class AgentCandidatePayload(BaseModel):
    """Validated JSON payload returned by a generation agent."""

    model_config = ConfigDict(extra="ignore")

    event_name: str
    domain: str
    question: str
    prediction_date: str
    ground_truth: str
    resolution_source: str = ""


class AgentRejectedPayload(BaseModel):
    """Validated JSON payload returned when the agent declines an event."""

    model_config = ConfigDict(extra="ignore")

    reject_reason: str


class AgentResult(BaseModel):
    """Normalized model result used by the pipeline."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["candidate", "rejected", "parse_error"]
    candidate: AgentCandidatePayload | None = None
    reject_reason: str = ""
    raw_output: str = ""


class QuestionCandidate(BaseModel):
    """A compact candidate question record for human review."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    event_id: str
    domain: str
    event_name: str
    question: str
    prediction_date: str
    ground_truth: str
    resolution_source: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    event_summary: str
    source_urls: str = ""
    review_status: str = "unreviewed"
    review_notes: str = ""

    @classmethod
    def from_agent_payload(
        cls,
        event: CandidateEvent,
        payload: AgentCandidatePayload,
        *,
        risk_flags: list[str] | None = None,
    ) -> "QuestionCandidate":
        """Create a review record from a source event and validated agent JSON."""
        domain = normalize_question_domain(payload.domain, fallback=event.domain)
        question = payload.question.strip()
        return cls(
            question_id=build_question_id(event.event_id, question),
            event_id=event.event_id,
            domain=domain,
            event_name=payload.event_name.strip(),
            question=question,
            prediction_date=payload.prediction_date.strip(),
            ground_truth=payload.ground_truth.strip(),
            resolution_source=preferred_resolution_source(event, payload),
            risk_flags=risk_flags or [],
            event_summary=event_summary_text(event),
            source_urls=source_urls_text(event),
        )

    def as_serializable_dict(self) -> dict[str, object]:
        """Return exactly the public candidate fields for JSONL output."""
        data = self.model_dump(mode="json")
        return {field: data[field] for field in CANDIDATE_FIELDNAMES}

    def as_csv_row(self) -> dict[str, str]:
        """Flatten list values for CSV review."""
        data = self.as_serializable_dict()
        return {
            field: "; ".join(data[field]) if field == "risk_flags" else str(data[field])
            for field in CANDIDATE_FIELDNAMES
        }


class RejectedQuestionEvent(BaseModel):
    """A compact rejected-event record kept separate from the review table."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    reject_stage: Literal["pre_screen", "agent", "parse", "validation"]
    reject_reason: str
    event_summary: str
    source_urls: str = ""

    @classmethod
    def from_event(
        cls,
        event: CandidateEvent,
        *,
        reject_stage: Literal["pre_screen", "agent", "parse", "validation"],
        reject_reason: str,
    ) -> "RejectedQuestionEvent":
        """Build a rejected-event row from a source event."""
        return cls(
            event_id=event.event_id,
            reject_stage=reject_stage,
            reject_reason=reject_reason.strip(),
            event_summary=event_summary_text(event),
            source_urls=source_urls_text(event),
        )

    def as_serializable_dict(self) -> dict[str, str]:
        """Return exactly the public rejected fields for JSONL output."""
        data = self.model_dump(mode="json")
        return {field: str(data[field]) for field in REJECTED_FIELDNAMES}


def normalize_question_domain(value: str, *, fallback: str = "") -> str:
    """Map crawler domains to the smaller question-domain set."""
    cleaned = (value or "").strip().lower()
    if cleaned in ALLOWED_QUESTION_DOMAINS:
        return cleaned
    if cleaned == "conflict":
        return "politics"
    fallback_cleaned = (fallback or "").strip().lower()
    if fallback_cleaned in ALLOWED_QUESTION_DOMAINS:
        return fallback_cleaned
    if fallback_cleaned == "conflict" or cleaned in {"election", "government"}:
        return "politics"
    return "politics"


def preferred_resolution_source(
    event: CandidateEvent, payload: AgentCandidatePayload
) -> str:
    """Use the agent source first, then fall back to event evidence URLs."""
    if payload.resolution_source.strip():
        return payload.resolution_source.strip()
    if event.evidence_urls:
        return event.evidence_urls[0].strip()
    return (event.source_url or "").strip()
