from __future__ import annotations

"""FRED 发布日历爬虫（需要 API Key）。"""

from datetime import date

from requests import Session

from ..http_client import fetch_json
from ..models import CandidateEvent
from ..utils import normalize_whitespace
from .base import BaseSourceCrawler, CrawlContext


class FREDReleaseCalendarCrawler(BaseSourceCrawler):
    """先取 release 列表，再取各 release 的发布日期。"""

    source_name = "fred_release_calendar"
    required_credential = "fred_api_key"
    releases_endpoint = "https://api.stlouisfed.org/fred/releases"
    release_dates_endpoint = "https://api.stlouisfed.org/fred/release/dates"

    _IMPORTANT_RELEASE_KEYWORDS = [
        "consumer price index",
        "producer price index",
        "employment situation",
        "gross domestic product",
        "personal consumption expenditures",
        "retail sales",
        "federal funds",
        "industrial production",
        "unemployment",
        "trade",
    ]

    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """抓取并过滤关键宏观数据发布日程。"""
        raw_payloads: list[dict] = []
        api_key = context.settings.fred_api_key
        if not api_key:
            return [], [{"skipped": "missing fred_api_key"}]

        try:
            releases_payload = fetch_json(
                session,
                self.releases_endpoint,
                params={"api_key": api_key, "file_type": "json", "limit": 1000},
                timeout=context.settings.request_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return [], [{"error": str(exc)}]

        releases = releases_payload.get("releases", [])
        selected_releases = self._select_releases(releases)
        raw_payloads.append(
            {
                "total_releases": len(releases),
                "selected_release_count": len(selected_releases),
            }
        )

        events: list[CandidateEvent] = []
        for release in selected_releases:
            release_id = release.get("id")
            if release_id is None:
                continue
            release_name = normalize_whitespace(str(release.get("name", "")))
            if not release_name:
                continue

            params = {
                "api_key": api_key,
                "file_type": "json",
                "release_id": release_id,
                "realtime_start": context.start_date.isoformat(),
                "realtime_end": context.end_date.isoformat(),
                "include_release_dates_with_no_data": "false",
            }
            try:
                dates_payload = fetch_json(
                    session,
                    self.release_dates_endpoint,
                    params=params,
                    timeout=context.settings.request_timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                raw_payloads.append(
                    {"release_id": release_id, "release_name": release_name, "error": str(exc)}
                )
                continue

            release_dates = dates_payload.get("release_dates", [])
            for item in release_dates:
                raw_date = item.get("date")
                if not raw_date:
                    continue
                try:
                    event_date = date.fromisoformat(raw_date)
                except ValueError:
                    continue
                if not (context.start_date <= event_date <= context.end_date):
                    continue
                release_link = str(release.get("link", "")).strip() or (
                    f"https://fred.stlouisfed.org/release?rid={release_id}"
                )
                events.append(
                    CandidateEvent.from_source(
                        source=self.source_name,
                        event_date=event_date,
                        topic=release_name,
                        summary="FRED economic data release calendar entry.",
                        domain="macro",
                        source_url=release_link,
                        evidence_urls=[release_link],
                        raw={"release_id": release_id, "release_date": raw_date},
                    )
                )
        return events, raw_payloads

    def _select_releases(self, releases: list[dict]) -> list[dict]:
        """优先保留高价值宏观发布主题，减少噪声。"""
        selected = []
        for release in releases:
            name = normalize_whitespace(str(release.get("name", ""))).lower()
            if any(keyword in name for keyword in self._IMPORTANT_RELEASE_KEYWORDS):
                selected.append(release)
        # 若关键词匹配为空，回退部分数据，避免整个来源无输出。
        if not selected:
            return releases[:50]
        return selected
