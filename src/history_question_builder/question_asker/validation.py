from __future__ import annotations

"""Risk-flag validation for generated historical prediction questions."""

import re
from datetime import date
from urllib.parse import urlparse

from ..event_crawler.models import CandidateEvent
from .models import QuestionCandidate


DATE_OR_TIME_PATTERNS = [
    r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}\b",
    r"\bby\s+(?:the\s+end\s+of\s+)?20\d{2}\b",
    r"\bon\s+20\d{2}\b",
    r"\bthrough\s+20\d{2}\b",
    r"\b\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\b",
    r"\b到\s*\d{4}\s*年",
]

VAGUE_TERMS = [
    "成功",
    "重大",
    "明显",
    "更好",
    "比较鹰派",
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

IMMEDIATE_NEWS_TERMS = [
    "attack",
    "attacked",
    "killed",
    "injured",
    "arrested",
    "detained",
    "protest",
    "demonstration",
    "explosion",
    "shooting",
]

FOLLOW_UP_TERMS = [
    "election",
    "referendum",
    "vote",
    "verdict",
    "ruling",
    "sentence",
    "ceasefire",
    "deadline",
    "meeting",
    "summit",
    "release",
    "announce",
    "result",
    "winner",
    "议席",
    "选举",
    "公投",
    "宣判",
    "结果",
    "利率",
]

AUTHORITATIVE_HINTS = [
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "federalreserve.gov",
    "bls.gov",
    "fred.stlouisfed.org",
    "electionguide.org",
    "who.int",
    "un.org",
    "reliefweb.int",
    "noaa.gov",
    "ecb.europa.eu",
    "boj.or.jp",
    "imf.org",
    "worldbank.org",
    "fifa.com",
    "nba.com",
    "theacademy.org",
    "grammy.com",
]


def validate_question(event: CandidateEvent, candidate: QuestionCandidate) -> list[str]:
    """Return review risk flags without rejecting the candidate."""
    flags: list[str] = []
    question = candidate.question.strip()
    question_lower = question.lower()
    ground_truth = candidate.ground_truth.strip()
    resolution_source = candidate.resolution_source.strip()

    if not _has_time_boundary(question):
        flags.append("ambiguous_time_boundary")

    if _has_vague_terms(question):
        flags.append("question_contains_vague_words")

    if _has_unclear_resolution_criteria(question_lower):
        flags.append("unclear_resolution_criteria")

    if not resolution_source or "wikipedia.org/wiki/portal:current_events" in resolution_source.lower():
        flags.append("weak_or_missing_resolution_source")

    if not ground_truth:
        flags.append("ground_truth_not_direct_answer")

    if _prediction_date_may_be_invalid(candidate.prediction_date, event):
        flags.append("prediction_date_may_be_invalid")

    if _looks_like_immediate_event(event) and not _has_follow_up_outcome(question_lower):
        flags.append("event_not_naturally_predictable")

    if resolution_source and not _looks_authoritative(resolution_source):
        flags.append("source_not_authoritative")

    if "weak_or_missing_resolution_source" in flags or "source_not_authoritative" in flags:
        flags.append("needs_external_fact_check")

    return _dedupe(flags)


def _has_time_boundary(question: str) -> bool:
    return any(re.search(pattern, question, flags=re.IGNORECASE) for pattern in DATE_OR_TIME_PATTERNS)


def _has_vague_terms(question: str) -> bool:
    lower = question.lower()
    return any(term.lower() in lower for term in VAGUE_TERMS)


def _has_unclear_resolution_criteria(question_lower: str) -> bool:
    unclear_phrases = [
        "what will happen",
        "what happens",
        "how will",
        "what impact",
        "影响如何",
        "会怎样",
    ]
    return any(phrase in question_lower for phrase in unclear_phrases)


def _prediction_date_may_be_invalid(prediction_date: str, event: CandidateEvent) -> bool:
    try:
        parsed = date.fromisoformat(prediction_date)
    except ValueError:
        return True
    return parsed >= event.event_date


def _looks_like_immediate_event(event: CandidateEvent) -> bool:
    text = " ".join([event.title, event.summary]).lower()
    return any(term in text for term in IMMEDIATE_NEWS_TERMS)


def _has_follow_up_outcome(question_lower: str) -> bool:
    return any(term in question_lower for term in FOLLOW_UP_TERMS)


def _looks_authoritative(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not host:
        return False
    return any(hint in host for hint in AUTHORITATIVE_HINTS) or host.endswith(".gov")


def _dedupe(flags: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped = []
    for flag in flags:
        if flag in seen:
            continue
        seen.add(flag)
        deduped.append(flag)
    return deduped
