from __future__ import annotations

"""Trading Economics 经济日历爬虫（需要 API Key）。"""

from dateutil import parser as date_parser
from requests import Session

from ..http_client import fetch_json
from ..models import CandidateEvent
from ..utils import clip_text, normalize_whitespace
from .base import BaseSourceCrawler, CrawlContext


class TradingEconomicsCalendarCrawler(BaseSourceCrawler):
    """抓取经济日历并做基础重要性过滤。"""

    source_name = "tradingeconomics_economic_calendar"
    required_credential = "trading_economics_key"
    endpoint = "https://api.tradingeconomics.com/calendar"

    _IMPORTANT_KEYWORDS = [
        "interest rate",
        "inflation",
        "consumer price",
        "producer price",
        "employment",
        "unemployment",
        "gdp",
        "pce",
        "retail sales",
        "central bank",
    ]

    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """拉取日期区间数据并转换为统一事件结构。"""
        key = context.settings.trading_economics_key
        if not key:
            return [], [{"skipped": "missing trading_economics_key"}]

        params = {
            "c": key,
            "f": "json",
            "d1": context.start_date.isoformat(),
            "d2": context.end_date.isoformat(),
        }
        raw_payloads: list[dict] = [{"request": params}]
        try:
            payload = fetch_json(
                session,
                self.endpoint,
                params=params,
                timeout=context.settings.request_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raw_payloads.append({"error": str(exc)})
            return [], raw_payloads

        if not isinstance(payload, list):
            raw_payloads.append({"unexpected_response_type": type(payload).__name__})
            return [], raw_payloads

        events: list[CandidateEvent] = []
        for item in payload:
            raw_date = str(item.get("Date", "")).strip()
            if not raw_date:
                continue
            try:
                event_date = date_parser.parse(raw_date).date()
            except (ValueError, OverflowError):
                continue
            if not (context.start_date <= event_date <= context.end_date):
                continue

            title = normalize_whitespace(str(item.get("Event") or item.get("Category") or ""))
            if not title:
                continue
            release_type = normalize_whitespace(str(item.get("Category", ""))) or "economic release"
            importance = int(item.get("Importance") or 0)
            text_for_priority = f"{title} {release_type}".lower()
            # 低重要性事件只有命中关键主题词时才保留。
            if importance < 2 and not any(
                keyword in text_for_priority for keyword in self._IMPORTANT_KEYWORDS
            ):
                continue

            url_value = str(item.get("URL", "")).strip()
            if url_value.startswith("/"):
                source_url = f"https://tradingeconomics.com{url_value}"
            else:
                source_url = url_value or "https://tradingeconomics.com/calendar"

            summary = clip_text(
                normalize_whitespace(
                    " | ".join(
                        part
                        for part in [
                            f"Type: {release_type}" if release_type else "",
                            f"Actual: {item.get('Actual')}" if item.get("Actual") not in (None, "") else "",
                            f"Forecast: {item.get('Forecast')}" if item.get("Forecast") not in (None, "") else "",
                            f"Previous: {item.get('Previous')}" if item.get("Previous") not in (None, "") else "",
                        ]
                        if part
                    )
                ),
                limit=700,
            )

            events.append(
                CandidateEvent.from_source(
                    source=self.source_name,
                    event_date=event_date,
                    topic=title,
                    summary=summary,
                    domain="macro",
                    source_url=source_url,
                    evidence_urls=[source_url],
                    raw={
                        "calendar_id": item.get("CalendarId"),
                        "importance": importance,
                        "release_type": release_type,
                        "reference": item.get("Reference"),
                        "ticker": item.get("Ticker"),
                    },
                )
            )

        raw_payloads.append({"response_count": len(payload), "kept_count": len(events)})
        return events, raw_payloads
