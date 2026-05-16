from __future__ import annotations

"""领域推断逻辑。"""

import re

from .models import CandidateEvent


_DOMAIN_KEYWORDS = {
    "politics": ["election", "parliament", "president", "referendum", "cabinet"],
    "conflict": ["strike", "ceasefire", "war", "sanction", "military", "attack"],
    "macro": [
        "fomc",
        "interest rate",
        "cpi",
        "ppi",
        "employment",
        "unemployment",
        "gdp",
        "inflation",
        "central bank",
    ],
    "public_risk": [
        "earthquake",
        "flood",
        "storm",
        "hurricane",
        "wildfire",
        "outbreak",
        "humanitarian",
        "disaster",
    ],
}


def _guess_domain(event: CandidateEvent) -> str:
    """通过关键词和来源兜底规则推断粗粒度领域。"""
    joined = " ".join([event.topic, event.summary]).lower()
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(keyword in joined for keyword in keywords):
            return domain
    if event.source in {"ifes_electionguide"}:
        return "politics"
    if event.source in {"fomc_calendar", "bls_release_calendar", "fred_release_calendar"}:
        return "macro"
    if event.source in {"reliefweb"}:
        return "public_risk"
    return event.domain or "other"


def filter_and_enrich_events(
    events: list[CandidateEvent],
) -> tuple[list[CandidateEvent], list[CandidateEvent]]:
    """对每条事件做领域归类。topic 已包含父子主题，去重交由爬虫层。"""
    kept: list[CandidateEvent] = []
    dropped: list[CandidateEvent] = []
    seen_summary_keys: set[str] = set()
    for event in events:
        event.domain = _guess_domain(event)
        summary_key = _summary_dedupe_key(event.summary)
        if summary_key:
            if summary_key in seen_summary_keys:
                if "duplicate_summary" not in event.quality_flags:
                    event.quality_flags.append("duplicate_summary")
                event.filter_reason = "duplicate_summary"
                dropped.append(event)
                continue
            seen_summary_keys.add(summary_key)
        kept.append(event)
    return kept, dropped


def _summary_dedupe_key(summary: str) -> str:
    """Return a stable key for duplicate non-empty event summaries."""
    cleaned = re.sub(r"\s+", " ", summary.strip()).casefold()
    return cleaned
