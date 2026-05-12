from __future__ import annotations

"""启发式筛选、领域推断与去重逻辑。"""

import re

from .models import CandidateEvent

_NON_EVENT_PATTERNS = [
    re.compile(r"\b(opinion|editorial|analysis|preview|outlook)\b", re.IGNORECASE),
    re.compile(r"\b(photo gallery|podcast|video recap)\b", re.IGNORECASE),
    re.compile(r"\b(maintenance|site update|about this page)\b", re.IGNORECASE),
]

_EVENT_HINT_PATTERNS = [
    re.compile(
        r"\b(elected|won|approved|rejected|signed|announced|released|"
        r"killed|injured|struck|meeting|vote|ceasefire|earthquake|flood|"
        r"hurricane|inflation|interest rate|cpi|ppi|employment|gdp)\b",
        re.IGNORECASE,
    )
]

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


def _looks_non_event(text: str) -> bool:
    """判断文本是否更像非事件内容（观点、维护信息等）。"""
    return any(pattern.search(text) for pattern in _NON_EVENT_PATTERNS)


def _looks_event_like(text: str) -> bool:
    """判断文本是否包含事件动作信号词。"""
    return any(pattern.search(text) for pattern in _EVENT_HINT_PATTERNS)


def _guess_domain(event: CandidateEvent) -> str:
    """通过关键词和来源兜底规则推断粗粒度领域。"""
    joined = " ".join([event.title, event.summary]).lower()
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
    """
    应用基础质量规则，并拆分为保留/丢弃两组。

    同时写入 quality_flags 与 filter_reason，便于后续人工复核。
    """
    kept: list[CandidateEvent] = []
    dropped: list[CandidateEvent] = []
    seen_event_ids: set[str] = set()

    for event in events:
        # 第一步：先做领域归类，并生成规则判断用文本。
        event.domain = _guess_domain(event)
        merged_text = " ".join([event.title, event.summary]).strip()

        if not event.title.strip():
            event.filter_reason = "missing_title"
            dropped.append(event)
            continue
        if event.source_url is None:
            event.quality_flags.append("missing_source_url")
        if _looks_non_event(merged_text):
            event.filter_reason = "non_event_pattern"
            dropped.append(event)
            continue
        if len(merged_text) < 15:
            event.filter_reason = "too_short"
            dropped.append(event)
            continue
        if not _looks_event_like(merged_text):
            event.quality_flags.append("weak_event_signal")
        # 去重以 event_id 为准，策略偏保守，宁可少删不误删。
        if event.event_id in seen_event_ids:
            event.filter_reason = "duplicate"
            dropped.append(event)
            continue

        seen_event_ids.add(event.event_id)
        kept.append(event)

    return kept, dropped
