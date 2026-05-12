from __future__ import annotations

"""Rule-based screening before spending model calls on events."""

import re
from dataclasses import dataclass
from typing import Literal

from ..event_crawler.models import CandidateEvent


ScreenPriority = Literal["high", "medium", "low"]

HIGH_PRIORITY_SOURCES = {
    "ifes_electionguide",
    "fomc_calendar",
    "bls_release_calendar",
    "fred_release_calendar",
}

PRIMARY_DOMAINS = {"politics", "macro", "public_risk"}
LOW_PRIORITY_DOMAINS = {"sports", "entertainment"}

HIGH_VALUE_PATTERNS = [
    r"\belection\b",
    r"\breferendum\b",
    r"\brunoff\b",
    r"\bparliament\b",
    r"\bpresidential\b",
    r"\bcabinet\b",
    r"\bgovernment formation\b",
    r"\bfomc\b",
    r"\binterest rate\b",
    r"\bfederal funds\b",
    r"\bcpi\b",
    r"\bppi\b",
    r"\bpce\b",
    r"\bgdp\b",
    r"\binflation\b",
    r"\bunemployment\b",
    r"\bemployment situation\b",
    r"\bnonfarm payrolls?\b",
    r"\bcentral bank\b",
    r"\bcourt\b",
    r"\bruling\b",
    r"\bverdict\b",
    r"\bsentence\b",
    r"\bceasefire\b",
    r"\bsanctions?\b",
    r"\bdeadline\b",
    r"\bsummit\b",
    r"\bvote\b",
    r"\bbill\b",
    r"\bhurricane\b",
    r"\bstorm\b",
    r"\bearthquake\b",
    r"\bflood\b",
    r"\boutbreak\b",
    r"\bairport\b",
    r"\breopen(?:ing|ed)?\b",
    r"\brestore(?:d|s|ation)?\b",
]

MEDIUM_VALUE_PATTERNS = [
    r"\bfinal\b",
    r"\bchampionship\b",
    r"\bworld cup\b",
    r"\bgrand prix\b",
    r"\baward\b",
    r"\boscars?\b",
    r"\bgrammys?\b",
    r"\bbox office\b",
    r"\bbillboard\b",
    r"\branking\b",
]

IMMEDIATE_NEWS_PATTERNS = [
    r"\battack(?:ed|s)?\b",
    r"\bkilled\b",
    r"\binjured\b",
    r"\barrest(?:ed|s)?\b",
    r"\bdetain(?:ed|s)?\b",
    r"\bprotest(?:ed|s)?\b",
    r"\bdemonstration\b",
    r"\bexplosion\b",
    r"\bshooting\b",
    r"\bstrike(?:s|d)?\b",
]

FUTURE_OUTCOME_PATTERNS = [
    r"\bceasefire\b",
    r"\bdeadline\b",
    r"\btrial\b",
    r"\bcourt\b",
    r"\bruling\b",
    r"\bverdict\b",
    r"\bvot(?:e|ed|ing)\b",
    r"\belection\b",
    r"\breferendum\b",
    r"\bresult(?:s)?\b",
    r"\bmeeting\b",
    r"\bsummit\b",
    r"\bannounce(?:d|ment)?\b",
]


@dataclass(frozen=True)
class ScreenDecision:
    """Decision produced by rule-based screening."""

    selected: bool
    reason: str
    priority: ScreenPriority = "low"


def screen_event(
    event: CandidateEvent, *, include_low_priority: bool = False
) -> ScreenDecision:
    """Decide whether an event should be sent to the question-generation agent."""
    text = _event_text(event)

    if event.source in HIGH_PRIORITY_SOURCES:
        return ScreenDecision(True, "high_priority_source", "high")

    if not event.source_url and not event.evidence_urls:
        return ScreenDecision(False, "missing_public_source", "low")

    has_high_value_signal = _matches_any(HIGH_VALUE_PATTERNS, text)
    has_medium_signal = _matches_any(MEDIUM_VALUE_PATTERNS, text)
    is_immediate_news = _matches_any(IMMEDIATE_NEWS_PATTERNS, text)
    has_future_outcome = _matches_any(FUTURE_OUTCOME_PATTERNS, text)

    if is_immediate_news and not has_future_outcome:
        return ScreenDecision(False, "immediate_news_without_future_result", "low")

    if event.domain in PRIMARY_DOMAINS and (has_high_value_signal or not is_immediate_news):
        return ScreenDecision(True, f"primary_domain:{event.domain}", "high")

    if has_high_value_signal:
        return ScreenDecision(True, "high_value_keyword", "high")

    if has_medium_signal:
        if include_low_priority or event.domain in LOW_PRIORITY_DOMAINS:
            return ScreenDecision(True, "supplemental_domain_keyword", "medium")
        return ScreenDecision(False, "low_priority_supplemental_event", "medium")

    if event.domain in LOW_PRIORITY_DOMAINS and include_low_priority:
        return ScreenDecision(True, f"low_priority_domain:{event.domain}", "low")

    if event.domain in {"conflict", "other"}:
        return ScreenDecision(False, f"domain_requires_future_result:{event.domain}", "low")

    return ScreenDecision(False, "no_predictable_result_signal", "low")


def _event_text(event: CandidateEvent) -> str:
    return " ".join([event.source, event.domain, event.title, event.summary]).lower()


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
