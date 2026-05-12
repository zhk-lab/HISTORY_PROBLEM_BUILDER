from __future__ import annotations

"""美联储 FOMC 官方日历爬虫。"""

import re
from calendar import monthrange
from datetime import date

from bs4 import BeautifulSoup
from requests import Session

from ..http_client import fetch_html, soup_from_html
from ..models import CandidateEvent
from ..utils import normalize_whitespace
from .base import BaseSourceCrawler, CrawlContext


class FOMCCalendarCrawler(BaseSourceCrawler):
    """提取 FOMC 会议日程和关联链接。"""

    source_name = "fomc_calendar"
    page_url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

    _month_lookup = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }

    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """解析官方页面，并按年份区块抽取事件。"""
        raw_payloads: list[dict] = []
        try:
            html = fetch_html(
                session,
                self.page_url,
                timeout=context.settings.request_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raw_payloads.append({"error": str(exc)})
            return [], raw_payloads

        soup = soup_from_html(html)
        link_index = self._build_date_link_index(soup)
        text_lines = self._extract_text_lines(soup)
        events: list[CandidateEvent] = []
        for year in range(context.start_date.year, context.end_date.year + 1):
            year_events = self._extract_year_events(
                lines=text_lines,
                year=year,
                link_index=link_index,
            )
            for event in year_events:
                if context.start_date <= event.event_date <= context.end_date:
                    events.append(event)
        raw_payloads.append({"parsed_event_count": len(events)})
        return events, raw_payloads

    def _extract_text_lines(self, soup: BeautifulSoup) -> list[str]:
        """将页面文本扁平化为规范化行序列。"""
        return [
            normalize_whitespace(line)
            for line in soup.get_text("\n").splitlines()
            if normalize_whitespace(line)
        ]

    def _build_date_link_index(self, soup: BeautifulSoup) -> dict[str, list[str]]:
        """按 URL 中的日期 token 建立链接索引。"""
        index: dict[str, list[str]] = {}
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            if not href:
                continue
            token_match = re.search(r"(20\d{6})", href)
            if token_match is None:
                continue
            token = token_match.group(1)
            index.setdefault(token, []).append(
                "https://www.federalreserve.gov" + href
                if href.startswith("/")
                else href
            )
        return index

    def _extract_year_events(
        self,
        *,
        lines: list[str],
        year: int,
        link_index: dict[str, list[str]],
    ) -> list[CandidateEvent]:
        """扫描某一年的会议区块并构建事件列表。"""
        section_title = f"{year} FOMC Meetings"
        try:
            start_index = lines.index(section_title)
        except ValueError:
            return []

        end_index = len(lines)
        for idx in range(start_index + 1, len(lines)):
            if re.fullmatch(r"\d{4} FOMC Meetings", lines[idx]):
                end_index = idx
                break
        section = lines[start_index + 1 : end_index]

        events: list[CandidateEvent] = []
        idx = 0
        while idx < len(section):
            month_label = section[idx]
            if not self._is_month_label(month_label):
                idx += 1
                continue
            if idx + 1 >= len(section):
                break
            date_token_text = section[idx + 1]
            meeting_date = self._parse_meeting_date(year, month_label, date_token_text)
            idx += 2
            if meeting_date is None:
                continue
            ym_token = meeting_date.strftime("%Y%m%d")
            related_links = sorted(set(link_index.get(ym_token, [])))
            events.append(
                CandidateEvent.from_source(
                    source=self.source_name,
                    event_date=meeting_date,
                    title=f"FOMC meeting ({month_label} {date_token_text}, {year})",
                    summary=(
                        "Federal Open Market Committee scheduled meeting date "
                        "from official calendar."
                    ),
                    domain="macro",
                    source_url=self.page_url,
                    evidence_urls=related_links,
                    raw={
                        "month": month_label,
                        "meeting_days": date_token_text,
                        "related_links": related_links,
                    },
                )
            )
        return events

    def _is_month_label(self, value: str) -> bool:
        """判断文本是否像月份标签（含跨月写法）。"""
        value_lower = value.lower()
        if "/" in value_lower:
            parts = [part.strip() for part in value_lower.split("/")]
            return all(part in self._month_lookup for part in parts)
        return value_lower in self._month_lookup

    def _parse_meeting_date(
        self, year: int, month_label: str, day_text: str
    ) -> date | None:
        """
        解析类似 "27-28"、"Apr/May 30-1" 的日期表达。

        这里使用“结束日”作为会议日期，因为政策结果通常在会议结束时公布。
        """
        day_text = day_text.replace("*", "").strip()
        # 兼容 notation vote 和单日写法。
        day_match = re.search(r"(\d{1,2})(?:-(\d{1,2}))?", day_text)
        if day_match is None:
            return None

        day_start = int(day_match.group(1))
        day_end = int(day_match.group(2) or day_start)
        month_parts = [part.strip().lower() for part in month_label.split("/")]
        start_month = self._month_lookup.get(month_parts[0])
        if start_month is None:
            return None

        end_month = start_month
        if len(month_parts) > 1:
            end_month = self._month_lookup.get(month_parts[1], start_month)
        elif day_end < day_start:
            # 只给了一个月份标签但结束日小于开始日，视为跨月。
            end_month = 1 if start_month == 12 else start_month + 1

        end_year = year
        if end_month < start_month:
            end_year += 1

        max_day = monthrange(end_year, end_month)[1]
        safe_day = min(day_end, max_day)
        try:
            return date(end_year, end_month, safe_day)
        except ValueError:
            return None
