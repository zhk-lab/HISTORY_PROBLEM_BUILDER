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
MAX_RESOLUTION_DETAIL_CHARS = 1200
MIN_REWRITE_TOKEN_OVERLAP = 0.55

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

CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
OPTION_LABEL_PATTERN = re.compile(r"^[A-F]\. .+")
GROUND_TRUTH_LABEL_PATTERN = re.compile(r"^[A-F]$")
MEANINGFUL_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
YES_NO_OPTION_SETS = {("yes", "no"), ("no", "yes")}
REWRITE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "did",
    "do",
    "does",
    "for",
    "from",
    "have",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "to",
    "was",
    "were",
    "what",
    "which",
    "will",
    "with",
}
THRESHOLD_OR_DEADLINE_PATTERNS = [
    r"\b(?:more|less|fewer|greater|higher|lower)\s+than\b",
    r"\b(?:at\s+least|at\s+most|no\s+more\s+than|no\s+less\s+than)\b",
    r"\b(?:over|under|above|below)\s+\d",
    r"\bbetween\s+\d",
    r"\bby\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}\b",
    r"\bby\s+20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
]


def validate_question(event: CandidateEvent, candidate: QuestionCandidate) -> list[str]:
    """Return objective review risk flags without rejecting the candidate."""
    flags: list[str] = []
    event_name = candidate.event_name.strip()
    question = candidate.question.strip()
    question_lower = question.lower()
    options = [option.strip() for option in candidate.options]
    ground_truth = candidate.ground_truth.strip()
    resolution_detail = candidate.resolution_detail.strip()
    source_urls = candidate.source_urls.strip()

    if _contains_cjk(event_name, question, *options, ground_truth, resolution_detail):
        flags.append("non_english_output")

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

    if _looks_like_low_information_event_rewrite(event, question, options):
        flags.append("low_information_event_rewrite")

    if not options or len(options) < 2:
        flags.append("missing_choice_options")
    elif len(options) > 6:
        flags.append("too_many_choice_options")

    if _has_duplicate_options(options):
        flags.append("duplicate_choice_options")

    if options and not _has_valid_option_labels(options):
        flags.append("invalid_choice_option_label")

    if not ground_truth:
        flags.append("ground_truth_not_direct_answer")
    elif not _ground_truth_matches_options(ground_truth, options):
        flags.append("ground_truth_not_in_options")

    if resolution_detail and _resolution_detail_length_is_abnormal(resolution_detail):
        flags.append("resolution_detail_length_abnormal")

    if not source_urls:
        flags.append("weak_or_missing_source_urls")
    elif not _has_valid_source_urls(source_urls):
        flags.append("invalid_source_urls")

    if _prediction_date_may_be_invalid(candidate.prediction_date, event):
        flags.append("prediction_date_may_be_invalid")

    if "weak_or_missing_source_urls" in flags or "invalid_source_urls" in flags:
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


def _resolution_detail_length_is_abnormal(resolution_detail: str) -> bool:
    return len(resolution_detail) > MAX_RESOLUTION_DETAIL_CHARS


def _has_broad_question_pattern(question_lower: str) -> bool:
    return any(
        re.search(pattern, question_lower, flags=re.IGNORECASE)
        for pattern in BANNED_BROAD_QUESTION_PATTERNS
    )


def _looks_like_low_information_event_rewrite(
    event: CandidateEvent, question: str, options: list[str]
) -> bool:
    if not _is_yes_no_options(options):
        return False
    if "will" not in question.lower():
        return False
    if _has_threshold_or_deadline_boundary(question):
        return False

    event_tokens = _meaningful_tokens(f"{event.topic} {event.summary}")
    question_tokens = _meaningful_tokens(question)
    if not event_tokens or not question_tokens:
        return False

    overlap = len(question_tokens & event_tokens) / len(question_tokens)
    return overlap >= MIN_REWRITE_TOKEN_OVERLAP


def _is_yes_no_options(options: list[str]) -> bool:
    stripped = tuple(_strip_option_label(option).lower() for option in options)
    return stripped in YES_NO_OPTION_SETS


def _strip_option_label(option: str) -> str:
    return re.sub(r"^[A-F]\.\s+", "", option.strip(), count=1)


def _has_threshold_or_deadline_boundary(question: str) -> bool:
    without_as_of = re.sub(
        r"^\s*as\s+of\s+20\d{2}[-/]\d{1,2}[-/]\d{1,2},?\s*",
        "",
        question,
        flags=re.IGNORECASE,
    )
    return any(
        re.search(pattern, without_as_of, flags=re.IGNORECASE)
        for pattern in THRESHOLD_OR_DEADLINE_PATTERNS
    )


def _meaningful_tokens(text: str) -> set[str]:
    return {
        _normalize_token(token)
        for token in MEANINGFUL_TOKEN_PATTERN.findall(text.lower())
        if len(token) > 2 and token not in REWRITE_STOPWORDS
    }


def _normalize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _contains_cjk(*values: str) -> bool:
    return any(CJK_PATTERN.search(value) for value in values)


def _has_duplicate_options(options: list[str]) -> bool:
    normalized = [option.lower() for option in options]
    return len(normalized) != len(set(normalized))


def _has_valid_option_labels(options: list[str]) -> bool:
    expected_labels = [chr(ord("A") + index) for index in range(len(options))]
    actual_labels = []
    for option in options:
        if not OPTION_LABEL_PATTERN.match(option):
            return False
        actual_labels.append(option[0])
    return actual_labels == expected_labels


def _ground_truth_matches_options(ground_truth: str, options: list[str]) -> bool:
    if not GROUND_TRUTH_LABEL_PATTERN.match(ground_truth):
        return False
    return ground_truth in {option[0] for option in options if option}


def _prediction_date_may_be_invalid(prediction_date: str, event: CandidateEvent) -> bool:
    try:
        parsed = date.fromisoformat(prediction_date)
    except ValueError:
        return True
    return parsed >= event.event_date


def _is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _has_valid_source_urls(source_urls: str) -> bool:
    urls = [url.strip() for url in source_urls.split(";") if url.strip()]
    return bool(urls) and all(_is_valid_http_url(url) for url in urls)


def _dedupe(flags: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped = []
    for flag in flags:
        if flag in seen:
            continue
        seen.add(flag)
        deduped.append(flag)
    return deduped
