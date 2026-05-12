from __future__ import annotations

"""Wikipedia Current Events 日页面爬虫。"""

import re
from datetime import date
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag
from requests import Session

from ..http_client import fetch_html, soup_from_html
from ..models import CandidateEvent
from ..utils import absolutize, clip_text, iter_dates, normalize_whitespace
from .base import BaseSourceCrawler, CrawlContext


class WikipediaCurrentEventsCrawler(BaseSourceCrawler):
    """从 Wikipedia 每日事件页提取候选事件和外部证据链接。"""

    source_name = "wikipedia_current_events"
    base_url = "https://en.wikipedia.org/wiki/Portal:Current_events"

    _CATEGORY_DOMAIN_HINTS = {
        "politics and elections": "politics",
        "armed conflicts and attacks": "conflict",
        "international relations": "conflict",
        "disasters and accidents": "public_risk",
        "health and environment": "public_risk",
        "business and economy": "macro",
    }

    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """按日期范围逐天抓取并解析页面。"""
        events: list[CandidateEvent] = []
        raw_payloads: list[dict] = []
        for day in iter_dates(context.start_date, context.end_date):
            page_url = (
                f"{self.base_url}/{day.year}_{day.strftime('%B')}_{day.day}"
            )
            try:
                html = fetch_html(
                    session,
                    page_url,
                    timeout=context.settings.request_timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                raw_payloads.append(
                    {
                        "date": day.isoformat(),
                        "url": page_url,
                        "error": str(exc),
                    }
                )
                continue

            soup = soup_from_html(html)
            parsed = list(self._parse_page(day, page_url, soup))
            events.extend(parsed)
            raw_payloads.append(
                {
                    "date": day.isoformat(),
                    "url": page_url,
                    "event_count": len(parsed),
                }
            )
        return events, raw_payloads

    def _parse_page(
        self, event_date: date, page_url: str, soup: BeautifulSoup
    ) -> Iterable[CandidateEvent]:
        """将页面 HTML 解析为候选事件对象列表。"""
        container = soup.select_one("div.current-events")
        if container is None:
            container = soup.select_one("div.mw-parser-output")
        if container is None:
            return []

        current_category = "uncategorized"
        parsed_events: list[CandidateEvent] = []

        # 路径 A：页面有清晰结构（标题 + 直接 ul/li）。
        for node in container.find_all(recursive=False):
            if not isinstance(node, Tag):
                continue
            if node.name in {"h2", "h3", "h4"}:
                headline = node.find(class_="mw-headline")
                if headline:
                    current_category = normalize_whitespace(headline.get_text())
                else:
                    current_category = normalize_whitespace(node.get_text())
                continue

            if node.name != "ul":
                continue

            for entry in node.find_all("li", recursive=False):
                event = self._parse_list_item(
                    entry=entry,
                    event_date=event_date,
                    page_url=page_url,
                    category=current_category,
                )
                if event is not None:
                    parsed_events.append(event)

        if parsed_events:
            return parsed_events

        # 路径 B：页面较扁平时，直接遍历所有 li 作为兜底。
        for entry in container.select("li"):
            event = self._parse_list_item(
                entry=entry,
                event_date=event_date,
                page_url=page_url,
                category="current_events",
            )
            if event is not None:
                parsed_events.append(event)

        return parsed_events

    def _parse_list_item(
        self,
        *,
        entry: Tag,
        event_date: date,
        page_url: str,
        category: str,
    ) -> CandidateEvent | None:
        """将单条列表项解析为统一事件结构。"""
        summary = self._extract_summary(entry, page_url)
        if not summary:
            return None

        first_link = entry.find("a")
        title = normalize_whitespace(first_link.get_text()) if first_link else summary
        title = title[:220]

        evidence_urls: list[str] = []
        wiki_links: list[str] = []
        # 区分站内链接和外部证据链接。
        for anchor in entry.find_all("a"):
            href = anchor.get("href")
            absolute = absolutize(page_url, href)
            if not absolute:
                continue
            if absolute.startswith("https://en.wikipedia.org/wiki/"):
                wiki_links.append(absolute)
                continue
            if absolute.startswith("http") and "wikipedia.org" not in absolute:
                evidence_urls.append(absolute)

        if not evidence_urls:
            return None

        domain = self._CATEGORY_DOMAIN_HINTS.get(category.lower(), "other")

        return CandidateEvent.from_source(
            source=self.source_name,
            event_date=event_date,
            title=title,
            summary=summary,
            domain=domain,
            source_url=page_url,
            evidence_urls=sorted(set(evidence_urls)),
            raw={
                "wikipedia_links": sorted(set(wiki_links)),
            },
        )

    def _extract_summary(self, entry: Tag, page_url: str) -> str:
        """提取列表项中的事件叙述句，尽量避开主题层级文本。"""
        summary_entry = self._deepest_entry_with_evidence(entry, page_url)
        summary = self._direct_list_item_text(summary_entry, page_url)
        if not summary:
            summary = normalize_whitespace(summary_entry.get_text(" ", strip=True))
        summary = self._strip_trailing_source_citation(summary)
        return clip_text(summary, limit=1800)

    def _deepest_entry_with_evidence(self, entry: Tag, page_url: str) -> Tag:
        """优先使用含外部证据链接的最内层 li，避免把父级主题并入摘要。"""
        candidates = [entry, *entry.find_all("li")]
        evidence_entries = [
            candidate
            for candidate in candidates
            if self._has_external_evidence_link(candidate, page_url)
        ]
        if not evidence_entries:
            return entry

        for candidate in evidence_entries:
            nested_evidence_entries = [
                nested
                for nested in candidate.find_all("li")
                if self._has_external_evidence_link(nested, page_url)
            ]
            if not nested_evidence_entries:
                return candidate
        return evidence_entries[-1]

    def _has_external_evidence_link(self, entry: Tag, page_url: str) -> bool:
        """判断列表项内是否有非 Wikipedia 的外部证据链接。"""
        for anchor in entry.find_all("a"):
            absolute = absolutize(page_url, anchor.get("href"))
            if absolute and absolute.startswith("http") and "wikipedia.org" not in absolute:
                return True
        return False

    def _direct_list_item_text(self, entry: Tag, page_url: str) -> str:
        """只读取当前 li 的直接文本，跳过嵌套列表和开头主题链接。"""
        pieces: list[tuple[str, bool]] = []
        for child in entry.children:
            if isinstance(child, NavigableString):
                text = normalize_whitespace(str(child))
                if text:
                    pieces.append((text, False))
                continue
            if not isinstance(child, Tag) or child.name in {"ul", "ol", "sup"}:
                continue

            text = normalize_whitespace(child.get_text(" ", strip=True))
            if not text:
                continue
            absolute = absolutize(page_url, child.get("href")) if child.name == "a" else None
            is_internal_anchor = bool(
                absolute and absolute.startswith("https://en.wikipedia.org/wiki/")
            )
            pieces.append((text, is_internal_anchor))

        if pieces and pieces[0][1] and self._looks_like_topic_prefix(pieces):
            pieces = pieces[1:]

        return self._normalize_summary_text(" ".join(text for text, _ in pieces))

    @staticmethod
    def _looks_like_topic_prefix(pieces: list[tuple[str, bool]]) -> bool:
        """判断首个内链是否更像主题标签，而非句子主语。"""
        remaining = " ".join(text for text, _ in pieces[1:]).strip()
        return bool(remaining and remaining[0].isupper())

    @staticmethod
    def _strip_trailing_source_citation(summary: str) -> str:
        """移除末尾形如 (Reuters) 的来源标记，证据链接已单独保存。"""
        cleaned = re.sub(r"\s*\([^()]{1,80}\)\s*$", "", summary).strip()
        return WikipediaCurrentEventsCrawler._normalize_summary_text(cleaned)

    @staticmethod
    def _normalize_summary_text(value: str) -> str:
        """整理 HTML 文本抽取后常见的标点空格问题。"""
        normalized = normalize_whitespace(value)
        normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
        normalized = re.sub(r"([\(\[])\s+", r"\1", normalized)
        normalized = re.sub(r"\s+([\)\]])", r"\1", normalized)
        return normalized
