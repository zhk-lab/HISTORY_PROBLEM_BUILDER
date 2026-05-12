from __future__ import annotations

"""BLS 年度经济发布日历爬虫。"""

from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from requests import Session

from ..http_client import fetch_html, soup_from_html
from ..models import CandidateEvent
from ..utils import clip_text, normalize_whitespace, parse_date_guess
from .base import BaseSourceCrawler, CrawlContext


class BLSReleaseCalendarCrawler(BaseSourceCrawler):
    """从 BLS schedule 页面提取发布日历事件。"""

    source_name = "bls_release_calendar"
    base_url = "https://www.bls.gov/schedule"

    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """按年份抓取并聚合 BLS 日历数据。"""
        events: list[CandidateEvent] = []
        raw_payloads: list[dict] = []
        for year in range(context.start_date.year, context.end_date.year + 1):
            year_url = f"{self.base_url}/{year}/"
            try:
                html = fetch_html(
                    session,
                    year_url,
                    timeout=context.settings.request_timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                raw_payloads.append({"year": year, "url": year_url, "error": str(exc)})
                continue

            soup = soup_from_html(html)
            parsed = self._parse_year_page(soup, year, year_url)
            events.extend(
                event
                for event in parsed
                if context.start_date <= event.event_date <= context.end_date
            )
            raw_payloads.append({"year": year, "url": year_url, "event_count": len(parsed)})
        return events, raw_payloads

    def _parse_year_page(
        self, soup: BeautifulSoup, year: int, page_url: str
    ) -> list[CandidateEvent]:
        """解析单个年份页面中的所有表格。"""
        events: list[CandidateEvent] = []
        for table in soup.select("table"):
            release_group = self._table_heading_text(table) or "BLS release"
            for row in table.select("tr"):
                cells = row.find_all(["th", "td"])
                if len(cells) < 2:
                    continue
                date_text = normalize_whitespace(cells[0].get_text())
                release_name = normalize_whitespace(cells[1].get_text())
                if not date_text or not release_name:
                    continue
                parsed_date = parse_date_guess(f"{date_text} {year}", default_year=year)
                if parsed_date is None:
                    parsed_date = parse_date_guess(date_text, default_year=year)
                if parsed_date is None:
                    continue
                detail_link = None
                anchor = cells[1].find("a")
                if anchor:
                    detail_link = urljoin(page_url, anchor.get("href", ""))
                summary = ""
                # 表格第三列通常是补充说明或注释。
                if len(cells) >= 3:
                    summary = clip_text(cells[2].get_text(" ", strip=True), limit=600)
                if release_group and release_group.lower() not in summary.lower():
                    summary = clip_text(f"{release_group} | {summary}" if summary else release_group, limit=600)
                events.append(
                    CandidateEvent.from_source(
                        source=self.source_name,
                        event_date=parsed_date,
                        title=release_name,
                        summary=summary,
                        domain="macro",
                        source_url=detail_link or page_url,
                        evidence_urls=[detail_link] if detail_link else [],
                        raw={"date_text": date_text, "release_group": release_group},
                    )
                )
        return events

    def _table_heading_text(self, table: Tag) -> str | None:
        """向上查找表格最近标题，用作发布分组信息。"""
        node = table.previous_sibling
        while node is not None:
            if isinstance(node, Tag) and node.name in {"h2", "h3", "h4"}:
                text = normalize_whitespace(node.get_text())
                if text:
                    return text
            node = node.previous_sibling
        return None
