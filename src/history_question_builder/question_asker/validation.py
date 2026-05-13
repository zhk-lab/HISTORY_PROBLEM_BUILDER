from __future__ import annotations

"""Mechanical validation for generated historical prediction questions.

This module deliberately stays in the "cheap and objective" lane. It checks
format, dates, URLs, lengths, and banned wording, then emits risk flags for
human review or later pipeline decisions. Semantic checks such as whether the
answer truly resolves the question belong in a separate verifier.
"""

import re
from datetime import date
from urllib.parse import urlparse

from ..event_crawler.models import CandidateEvent
from .models import ALLOWED_QUESTION_DOMAINS, QuestionCandidate


MIN_QUESTION_CHARS = 20
MAX_QUESTION_CHARS = 280
MAX_GROUND_TRUTH_CHARS = 1200

DATE_OR_TIME_PATTERNS = [
    r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}\b",
    r"\b(?:by|before|after|on|as\s+of|until|through)\s+(?:the\s+end\s+of\s+)?20\d{2}\b",
    r"\b(?:end|start|beginning)\s+of\s+20\d{2}\b",
    r"\b\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\b",
    r"(?:截至|截止|到|在|至)\s*20\d{2}\s*年",
    r"20\d{2}\s*年\s*(?:底|末|初|前|后)",
]

VAGUE_TERMS = [
    "成功",
    "重大",
    "明显",
    "更好",
    "更差",
    "涨很多",
    "跌很多",
    "significant",
    "successful",
    "major",
    "better",
    "worse",
    "hawkish",
    "dovish",
    "substantial",
    "dramatic",
]

BANNED_BROAD_QUESTION_PATTERNS = [
    r"\bwhat\s+will\s+happen\b",
    r"\bwhat\s+happens\b",
    r"\bhow\s+will\b",
    r"\bwhat\s+impact\b",
    r"\bwhat\s+effect\b",
    r"\bhow\s+will\s+.*\s+affect\b",
    r"影响如何",
    r"会怎样",
    r"会如何影响",
]

DISALLOWED_RESOLUTION_SOURCE_HOSTS = [
    "wikipedia.org",
]


def validate_question(event: CandidateEvent, candidate: QuestionCandidate) -> list[str]:
    """Return objective review risk flags without rejecting the candidate."""
    flags: list[str] = []
    question = candidate.question.strip()
    question_lower = question.lower()
    ground_truth = candidate.ground_truth.strip()
    resolution_source = candidate.resolution_source.strip()

    if candidate.domain not in ALLOWED_QUESTION_DOMAINS:
        flags.append("invalid_question_domain")

    if _question_length_is_abnormal(question):
        flags.append("question_length_abnormal")

    if not _has_time_boundary(question):
        flags.append("ambiguous_time_boundary")

    if _has_vague_terms(question):
        flags.append("question_contains_vague_words")

    if _has_broad_question_pattern(question_lower):
        flags.append("unclear_resolution_criteria")

    if not resolution_source:
        flags.append("weak_or_missing_resolution_source")
    elif not _is_valid_http_url(resolution_source):
        flags.append("invalid_resolution_source_url")
    elif _is_disallowed_resolution_source(resolution_source):
        flags.append("weak_or_missing_resolution_source")

    if not ground_truth:
        flags.append("ground_truth_not_direct_answer")
    elif _ground_truth_length_is_abnormal(ground_truth):
        flags.append("ground_truth_length_abnormal")

    if _prediction_date_may_be_invalid(candidate.prediction_date, event):
        flags.append("prediction_date_may_be_invalid")

    if (
        "weak_or_missing_resolution_source" in flags
        or "invalid_resolution_source_url" in flags
    ):
        flags.append("needs_external_fact_check")

    return _dedupe(flags)


def _has_time_boundary(question: str) -> bool:
    return any(
        re.search(pattern, question, flags=re.IGNORECASE)
        for pattern in DATE_OR_TIME_PATTERNS
    )


def _has_vague_terms(question: str) -> bool:
    lower = question.lower()
    return any(term.lower() in lower for term in VAGUE_TERMS)


def _question_length_is_abnormal(question: str) -> bool:
    return len(question) < MIN_QUESTION_CHARS or len(question) > MAX_QUESTION_CHARS


def _ground_truth_length_is_abnormal(ground_truth: str) -> bool:
    return len(ground_truth) > MAX_GROUND_TRUTH_CHARS


def _has_broad_question_pattern(question_lower: str) -> bool:
    return any(
        re.search(pattern, question_lower, flags=re.IGNORECASE)
        for pattern in BANNED_BROAD_QUESTION_PATTERNS
    )


def _prediction_date_may_be_invalid(prediction_date: str, event: CandidateEvent) -> bool:
    try:
        parsed = date.fromisoformat(prediction_date)
    except ValueError:
        return True
    return parsed >= event.event_date


def _is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_disallowed_resolution_source(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    is_disallowed_host = any(
        host == item or host.endswith(f".{item}")
        for item in DISALLOWED_RESOLUTION_SOURCE_HOSTS
    )
    return is_disallowed_host and "portal:current_events" in path


def _dedupe(flags: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped = []
    for flag in flags:
        if flag in seen:
            continue
        seen.add(flag)
        deduped.append(flag)
    return deduped
