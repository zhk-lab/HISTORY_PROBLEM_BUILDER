from __future__ import annotations

"""Rule-based screening before spending model calls on events."""

import re
from dataclasses import dataclass, field
from typing import Literal

from ..event_crawler.models import CandidateEvent


ScreenPriority = Literal["high", "medium", "low"]

# 高质量结构化来源：这些来源本身就是“有日程/有结果/可验证”的数据源，
# 所以即使文本关键词不充分，也应该优先送入 agent。
HIGH_PRIORITY_SOURCES = {
    "ifes_electionguide",
    "fomc_calendar",
    "bls_release_calendar",
    "fred_release_calendar",
}

# 主领域只给轻微加分，不能单独决定放行。
PRIMARY_DOMAINS = {"politics", "macro", "public_risk"}

# 低优先级领域默认更严格，但如果命中强结构组合，仍然可以放行。
LOW_PRIORITY_DOMAINS = {"sports", "entertainment"}

# 主题价值信号：说明事件领域较容易构造有复盘价值的问题。
# 这些词只表示“值得检查”，不能单独证明事件有合格预测点。
TOPIC_VALUE_PATTERNS = [
    r"\belections?\b",
    r"\breferendums?\b",
    r"\brunoffs?\b",
    r"\bparliament(?:ary)?\b",
    r"\bnational assembly\b",
    r"\blegislative assembly\b",
    r"\bpresidential\b",
    r"\bcabinet\b",
    r"\bgovernment formation\b",
    r"\bpresident\b",
    r"\bprime minister\b",
    r"\bfirst minister\b",
    r"\bdefen[cs]e minister\b",
    r"\bminister\b",
    r"\bspeaker\b",
    r"\bno[- ]confidence\b",
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
    r"\bjustice\b",
    r"\bsupreme court\b",
    r"\bconstitutional\b",
    r"\brul(?:e|ed|ing)s?\b",
    r"\bverdict\b",
    r"\bsentence\b",
    r"\bappeal(?:s|late)?\b",
    r"\binjunction\b",
    r"\bceasefire\b",
    r"\bsanctions?\b",
    r"\brestrictions?\b",
    r"\bdeadline\b",
    r"\bsummit\b",
    r"\bvotes?\b",
    r"\bbills?\b",
    r"\bderby\b",
    r"\bcup\b",
    r"\bfinals?\b",
    r"\bchampionship\b",
    r"\btournament\b",
    r"\bgrand prix\b",
    r"\bolympic(?:s| games)?\b",
    r"\bhurricane\b",
    r"\bstorm\b",
    r"\bearthquake\b",
    r"\bflood\b",
    r"\boutbreak\b",
    r"\bairport\b",
    r"\bpassenger traffic\b",
    r"\breopen(?:ing|ed)?\b",
    r"\brestore(?:d|s|ation)?\b",
    r"\brelations?\b",
    r"\bagreements?\b",
]

# 预测设置/流程信号：表示事件可能存在“结果公开前”的自然提问点，
# 例如投票、决赛、裁决、官方发布、截止日期或政策生效时间。
FORECAST_SETUP_PATTERNS = [
    r"\bscheduled\b",
    r"\bschedule[ds]?\b",
    r"\bset to\b",
    r"\bwill start\b",
    r"\bwill begin\b",
    r"\bbegins?\b",
    r"\bbeginning\b",
    r"\bstarts?\b",
    r"\btakes? effect\b",
    r"\beffective\b",
    r"\bdeadline\b",
    r"\bby\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}\b",
    r"\bby\s+20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
    r"\bon\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}\b",
    r"\btrial\b",
    r"\bhearing\b",
    r"\brul(?:e|ed|ing)s?\b",
    r"\bverdict\b",
    r"\bvot(?:e|ed|ing|es)\b",
    r"\belections?\b",
    r"\breferendums?\b",
    r"\bfinals?\b",
    r"\bchampionship\b",
    r"\bderby\b",
    r"\bcup\b",
    r"\bofficial release\b",
    r"\breport for\b",
]

# 可验证结果信号：表示文本里可能已经包含可判定 ground_truth 的结果，
# 例如胜负、当选、通过/失败、裁决、票数、席位、比分或正式状态变化。
RESOLUTION_PATTERNS = [
    r"\bwins?\b",
    r"\bwon\b",
    r"\bbeats?\b",
    r"\bdefeat(?:ed|s)?\b",
    r"\bloses?\b",
    r"\blost\b",
    r"\belected\b",
    r"\bsworn in\b",
    r"\bappointed\b",
    r"\bpasses?\b",
    r"\bpassed\b",
    r"\bfails?\b",
    r"\bfailed\b",
    r"\bapprov(?:e|ed|es)\b",
    r"\breject(?:ed|s)?\b",
    r"\brul(?:e|ed|ing)s?\b",
    r"\bsentence(?:d|s)?\b",
    r"\breduced\b",
    r"\bsuspends?\b",
    r"\blift(?:ed|s)?\b",
    r"\bimpos(?:e|ed|es)\b",
    r"\bsign(?:s|ed)?\b.{0,40}\bagreements?\b",
    r"\bagree(?:d|s)? to\b",
    r"\brestore(?:d|s)? relations\b",
    r"\breported\b",
    r"\breports?\b",
    r"\bconfirmed\b",
    r"\bheld\b",
    r"\bparticipat(?:e|ed|es|ing)\b",
    r"\bousted\b",
    r"\bconcedes?\b",
    r"\bclaims? victory\b",
    r"\bbecomes?\b",
    r"\bresigns?\b",
    r"\breconvenes?\b",
    r"\bvotes?\b",
    r"\bseats?\b",
    r"\bscore\b",
    r"\bmargin\b",
    r"\bpercent(?:age)?\b",
    r"\b\d+(?:\.\d+)?%\b",
]

# 即时新闻负向信号：多见于事后伤亡、袭击、逮捕、抗议等报道。
# 如果没有独立预测设置或强结构结果，通常应拒绝。
IMMEDIATE_NEWS_PATTERNS = [
    r"\battack(?:ed|s)?\b",
    r"\bairstrikes?\b",
    r"\bdrone strikes?\b",
    r"\bmissiles?\b",
    r"\bintercept(?:ed|s|ion)?\b",
    r"\bkilled\b",
    r"\bdied\b",
    r"\bdead\b",
    r"\bdeaths?\b",
    r"\binjured\b",
    r"\bwounded\b",
    r"\barrest(?:ed|s)?\b",
    r"\bdetain(?:ed|s)?\b",
    r"\babduct(?:ed|s)?\b",
    r"\bkidnapp(?:ed|s)?\b",
    r"\bprotest(?:ed|s|ers)?\b",
    r"\bdemonstration\b",
    r"\bexplosion\b",
    r"\bshooting\b",
    r"\bstrike(?:s|d)?\b",
    r"\bcrash(?:ed|es)?\b",
    r"\boverturns?\b",
    r"\bcollisions?\b",
    r"\bfire\b",
    r"\bevacuated\b",
]

# 新闻改写风险信号：常表示“某人说/宣布/讨论了什么”。
# 若没有预测设置或可验证结果，通常只能生成低质量新闻改写题。
REWRITE_RISK_PATTERNS = [
    r"\bsays?\b",
    r"\bstates?\b",
    r"\bannounc(?:e|ed|es)\b",
    r"\breports?\b",
    r"\bholds? a phone call\b",
    r"\bphone call\b",
    r"\bdiscuss(?:ed|es)?\b",
    r"\bcriticiz(?:e|ed|es)\b",
    r"\bexpects?\b",
    r"\bexpected\b",
    r"\bmay resume\b",
    r"\brefuses?\b",
]

# 数值结果信号：提示问题可以落到票数、席位、比分、百分比、利率、涨跌幅或数量上。
NUMERIC_RESULT_PATTERNS = [
    r"\bpercent(?:age)?\b",
    r"\b\d+(?:\.\d+)?%\b",
    r"\brates?\b",
    r"\btarget range\b",
    r"\bvotes?\b",
    r"\bseats?\b",
    r"\bscore\b",
    r"\bmargin\b",
    r"\bcount\b",
    r"\btotal\b",
    r"\bdrop(?:ped|s)?\b",
    r"\bdeclin(?:e|ed|es)\b",
    r"\bincreas(?:e|ed|es)\b",
    r"\bdecreas(?:e|ed|es)\b",
    r"\b\d+\s*(?:-|–)\s*\d+\b",
]

# 低优先级主题信号：这些事件并非无价值，但默认需要更强结构组合。
LOW_PRIORITY_TOPIC_PATTERNS = [
    r"\bawards?\b",
    r"\boscars?\b",
    r"\bgrammys?\b",
    r"\bbox office\b",
    r"\bbillboard\b",
    r"\branking\b",
    r"\bsports?\b",
    r"\bhorse racing\b",
    r"\bfootball\b",
    r"\bbasketball\b",
]

# Backward-compatible aliases for older tests/imports and easier diff review.
HIGH_VALUE_PATTERNS = TOPIC_VALUE_PATTERNS
MEDIUM_VALUE_PATTERNS = LOW_PRIORITY_TOPIC_PATTERNS
FUTURE_OUTCOME_PATTERNS = FORECAST_SETUP_PATTERNS


@dataclass(frozen=True)
class ScreenDecision:
    """Decision produced by rule-based screening."""

    selected: bool
    reason: str
    priority: ScreenPriority = "low"


@dataclass(frozen=True)
class ScreenSignals:
    """Keyword-derived signals used by the pre-screen scorer."""

    score: int
    topic_value: bool = False
    forecast_setup: bool = False
    resolution: bool = False
    immediate_news: bool = False
    rewrite_risk: bool = False
    numeric_result: bool = False
    low_priority_topic: bool = False
    primary_domain: bool = False
    high_priority_source: bool = False
    sports_result: bool = False
    court_result: bool = False
    vote_result: bool = False
    election_result: bool = False
    official_numeric_result: bool = False
    diplomatic_result: bool = False
    olympic_policy_result: bool = False
    summit_attendance_result: bool = False
    office_transition_result: bool = False
    matched: dict[str, list[str]] = field(default_factory=dict)


def screen_event(
    event: CandidateEvent, *, include_low_priority: bool = False
) -> ScreenDecision:
    """Decide whether an event should be sent to the question-generation agent."""
    if not event.source_url and not event.evidence_urls:
        return ScreenDecision(False, "missing_public_source", "low")

    text = _event_text(event)
    signals = build_screen_signals(event)

    if signals.high_priority_source:
        return ScreenDecision(True, "structured_source", "high")

    if _looks_like_election_activity_without_result(text):
        return ScreenDecision(False, "election_activity_without_result", "low")

    if _looks_like_debate_without_result(text):
        return ScreenDecision(False, "debate_without_decision_result", "low")

    if signals.immediate_news and not signals.forecast_setup and not _has_structured_keyword_combo(signals):
        return ScreenDecision(False, "immediate_news_without_forecast_setup", "low")

    if signals.rewrite_risk and not signals.forecast_setup and not signals.resolution:
        return ScreenDecision(False, "rewrite_risk_too_high", "low")

    if not signals.resolution and not _has_structured_keyword_combo(signals):
        return ScreenDecision(False, "missing_resolution_signal", "low")

    if (
        signals.low_priority_topic
        and not include_low_priority
        and signals.score < 6
        and not _has_strong_keyword_combo(signals)
    ):
        return ScreenDecision(False, "low_priority_without_opt_in", "medium")

    if signals.score >= 5 and _has_structured_keyword_combo(signals):
        return ScreenDecision(True, "high_score_keyword_combo", "high")

    if signals.score >= 3 and _has_structured_keyword_combo(signals):
        return ScreenDecision(True, "borderline_keyword_combo", "medium")

    if event.domain in {"conflict", "other"}:
        return ScreenDecision(False, f"domain_requires_structured_signal:{event.domain}", "low")

    return ScreenDecision(False, "low_score_no_predictable_result", "low")


def build_screen_signals(event: CandidateEvent) -> ScreenSignals:
    """Extract keyword signals and compute a lightweight pre-screen score."""
    text = _event_text(event)
    matched = {
        "topic_value": _matched_patterns(TOPIC_VALUE_PATTERNS, text),
        "forecast_setup": _matched_patterns(FORECAST_SETUP_PATTERNS, text),
        "resolution": _matched_patterns(RESOLUTION_PATTERNS, text),
        "immediate_news": _matched_patterns(IMMEDIATE_NEWS_PATTERNS, text),
        "rewrite_risk": _matched_patterns(REWRITE_RISK_PATTERNS, text),
        "numeric_result": _matched_patterns(NUMERIC_RESULT_PATTERNS, text),
        "low_priority_topic": _matched_patterns(LOW_PRIORITY_TOPIC_PATTERNS, text),
    }

    topic_value = bool(matched["topic_value"])
    forecast_setup = bool(matched["forecast_setup"])
    resolution = bool(matched["resolution"])
    immediate_news = bool(matched["immediate_news"])
    rewrite_risk = bool(matched["rewrite_risk"])
    numeric_result = bool(matched["numeric_result"])
    low_priority_topic = bool(matched["low_priority_topic"])
    primary_domain = event.domain in PRIMARY_DOMAINS
    high_priority_source = event.source in HIGH_PRIORITY_SOURCES
    sports_result = _has_sports_result(text)
    court_result = _has_court_result(text)
    vote_result = _has_vote_result(text)
    election_result = _has_election_result(text)
    official_numeric_result = _has_official_numeric_result(text)
    diplomatic_result = _has_diplomatic_result(text)
    olympic_policy_result = _has_olympic_policy_result(text)
    summit_attendance_result = _has_summit_attendance_result(text)
    office_transition_result = _has_office_transition_result(text)

    score = 0
    if high_priority_source:
        score += 4
    if topic_value:
        score += 2
    if forecast_setup:
        score += 3
    if resolution:
        score += 3
    if numeric_result:
        score += 2
    if primary_domain:
        score += 1
    if official_numeric_result:
        score += 2
    if diplomatic_result or olympic_policy_result or summit_attendance_result:
        score += 2
    if office_transition_result:
        score += 1
    if low_priority_topic and event.domain not in LOW_PRIORITY_DOMAINS:
        score -= 1
    if immediate_news:
        score -= 3
    if rewrite_risk:
        score -= 2

    return ScreenSignals(
        score=score,
        topic_value=topic_value,
        forecast_setup=forecast_setup,
        resolution=resolution,
        immediate_news=immediate_news,
        rewrite_risk=rewrite_risk,
        numeric_result=numeric_result,
        low_priority_topic=low_priority_topic,
        primary_domain=primary_domain,
        high_priority_source=high_priority_source,
        sports_result=sports_result,
        court_result=court_result,
        vote_result=vote_result,
        election_result=election_result,
        official_numeric_result=official_numeric_result,
        diplomatic_result=diplomatic_result,
        olympic_policy_result=olympic_policy_result,
        summit_attendance_result=summit_attendance_result,
        office_transition_result=office_transition_result,
        matched={key: value for key, value in matched.items() if value},
    )


def _has_structured_keyword_combo(signals: ScreenSignals) -> bool:
    return (
        (signals.topic_value and signals.forecast_setup and signals.resolution)
        or signals.sports_result
        or signals.court_result
        or signals.vote_result
        or signals.election_result
        or signals.official_numeric_result
        or signals.diplomatic_result
        or signals.olympic_policy_result
        or signals.summit_attendance_result
        or signals.office_transition_result
    )


def _has_strong_keyword_combo(signals: ScreenSignals) -> bool:
    return (
        signals.sports_result
        or signals.court_result
        or signals.vote_result
        or signals.election_result
        or signals.official_numeric_result
        or signals.diplomatic_result
        or signals.olympic_policy_result
        or signals.summit_attendance_result
        or signals.office_transition_result
    )


def _looks_like_election_activity_without_result(text: str) -> bool:
    if not re.search(r"\bvoters?\b.{0,180}\belect\b", text, flags=re.IGNORECASE):
        return False
    result_terms = [
        r"\bwon\b",
        r"\bwins?\b",
        r"\bdefeat(?:ed|s)?\b",
        r"\bconcedes?\b",
        r"\bclaims? victory\b",
        r"\bresults?\b",
        r"\bofficial results?\b",
        r"\bvote share\b",
    ]
    return not _matches_any(result_terms, text)


def _looks_like_debate_without_result(text: str) -> bool:
    if not re.search(r"\bdebates?\b.{0,120}\bno[- ]confidence\b", text, flags=re.IGNORECASE):
        return False
    result_terms = [
        r"\bvot(?:e|ed|ing|es)\b",
        r"\bpasses?\b",
        r"\bpassed\b",
        r"\bfails?\b",
        r"\bfailed\b",
        r"\bapprov(?:e|ed|es)\b",
        r"\breject(?:ed|s)?\b",
    ]
    return not _matches_any(result_terms, text)


def _has_sports_result(text: str) -> bool:
    sports_terms = [
        r"\bderby\b",
        r"\bcup\b",
        r"\bfinals?\b",
        r"\bchampionship\b",
        r"\btournament\b",
        r"\bgrand prix\b",
        r"\bhorse racing\b",
        r"\bfootball\b",
        r"\bbasketball\b",
    ]
    result_terms = [
        r"\bwins?\b",
        r"\bwon\b",
        r"\bbeats?\b",
        r"\bdefeat(?:ed|s)?\b",
        r"\bscore\b",
        r"\b\d+\s*(?:-|–)\s*\d+\b",
    ]
    return _matches_any(sports_terms, text) and _matches_any(result_terms, text)


def _has_court_result(text: str) -> bool:
    court_terms = [r"\bcourt\b", r"\bjustice\b", r"\bconstitutional\b", r"\bappeal(?:s|late)?\b"]
    result_terms = [
        r"\brul(?:e|ed|ing)s?\b",
        r"\bsentence(?:d|s)?\b",
        r"\breduced\b",
        r"\bsuspends?\b",
        r"\boverturn(?:ed|s)?\b",
        r"\billegal\b",
        r"\binjunction\b",
    ]
    return _matches_any(court_terms, text) and _matches_any(result_terms, text)


def _has_vote_result(text: str) -> bool:
    vote_terms = [
        r"\bvot(?:e|ed|ing|es)\b",
        r"\bparliament\b",
        r"\bnational assembly\b",
        r"\blegislative assembly\b",
        r"\bno[- ]confidence\b",
    ]
    result_terms = [
        r"\bpasses?\b",
        r"\bpassed\b",
        r"\bfails?\b",
        r"\bfailed\b",
        r"\bapprov(?:e|ed|es)\b",
        r"\breject(?:ed|s)?\b",
        r"\bousted\b",
        r"\bsuccessful\b",
        r"\belected\b",
        r"\bvotes?\b",
    ]
    return _matches_any(vote_terms, text) and _matches_any(result_terms, text)


def _has_election_result(text: str) -> bool:
    election_terms = [r"\belections?\b", r"\breferendums?\b", r"\bpresidential\b"]
    result_terms = [
        r"\bwins?\b",
        r"\bwon\b",
        r"\belected\b",
        r"\bseats?\b",
        r"\bvote share\b",
        r"\bconcedes?\b",
        r"\bclaims? victory\b",
        r"\bsworn in\b",
    ]
    if _looks_like_election_activity_without_result(text):
        return False
    party_result = re.search(r"\bparty\b", text, flags=re.IGNORECASE) and _matches_any(
        [r"\bconcedes? defeat\b", r"\bclaims? victory\b", r"\blosing her seat\b"],
        text,
    )
    return party_result or (_matches_any(election_terms, text) and _matches_any(result_terms, text))


def _has_official_numeric_result(text: str) -> bool:
    official_terms = [
        r"\bcpi\b",
        r"\bppi\b",
        r"\bpce\b",
        r"\bgdp\b",
        r"\binflation\b",
        r"\bunemployment\b",
        r"\bnonfarm payrolls?\b",
        r"\binterest rate\b",
        r"\bfederal funds\b",
        r"\bpassenger traffic\b",
        r"\bairport\b",
        r"\boil inventor(?:y|ies)\b",
    ]
    reporting_terms = [r"\breports?\b", r"\breported\b", r"\bestimat(?:e|ed|es)\b"]
    return (
        _matches_any(official_terms, text)
        and _matches_any(reporting_terms, text)
        and _matches_any(NUMERIC_RESULT_PATTERNS, text)
    )


def _has_diplomatic_result(text: str) -> bool:
    diplomatic_terms = [
        r"\brelations?\b",
        r"\bdiplomatic\b",
        r"\brestore(?:d|s|ation)?\b",
        r"\bmediated\b",
        r"\btalks?\b",
        r"\bsummit\b",
        r"\bagreements?\b",
        r"\bdefense agreement\b",
    ]
    result_terms = [
        r"\bagree(?:d|s)? to\b",
        r"\brestore(?:d|s)? relations\b",
        r"\breopen(?:ed|s)?\b",
        r"\bsign(?:s|ed)?\b.{0,40}\bagreements?\b",
    ]
    return _matches_any(diplomatic_terms, text) and _matches_any(result_terms, text)


def _has_olympic_policy_result(text: str) -> bool:
    olympic_terms = [r"\bolympic(?:s| games)?\b", r"\bolympic committee\b", r"\bathletes?\b"]
    result_terms = [
        r"\blift(?:ed|s)? restrictions?\b",
        r"\bremain limited\b",
        r"\bneutral participation\b",
        r"\bsuspension\b",
    ]
    return _matches_any(olympic_terms, text) and _matches_any(result_terms, text)


def _has_summit_attendance_result(text: str) -> bool:
    summit_terms = [r"\bsummit\b", r"\bconference\b"]
    result_terms = [
        r"\bheld\b",
        r"\bleaders? from\b",
        r"\bparticipat(?:e|ed|es|ing)\b",
        r"\b\d+ countries\b",
        r"\bnearly \d+ countries\b",
    ]
    return _matches_any(summit_terms, text) and _matches_any(result_terms, text)


def _has_office_transition_result(text: str) -> bool:
    office_terms = [
        r"\bpresident\b",
        r"\bprime minister\b",
        r"\bfirst minister\b",
        r"\bdefen[cs]e minister\b",
        r"\bminister\b",
        r"\bpremiership\b",
        r"\bspeaker\b",
        r"\bmember of parliament\b",
        r"\bhouse of representatives\b",
    ]
    result_terms = [
        r"\bsworn in\b",
        r"\belected as\b",
        r"\bis elected\b",
        r"\bappointed\b",
        r"\bousted\b",
        r"\bbecomes?\b",
        r"\bresigns?\b",
    ]
    return _matches_any(office_terms, text) and _matches_any(result_terms, text)


def _event_text(event: CandidateEvent) -> str:
    return " ".join([event.source, event.domain, event.title, event.summary]).lower()


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _matched_patterns(patterns: list[str], text: str) -> list[str]:
    return [
        pattern
        for pattern in patterns
        if re.search(pattern, text, flags=re.IGNORECASE)
    ]






