from __future__ import annotations

"""Wikipedia Current Events 日页面爬虫。"""

import re
from datetime import date
from typing import Iterable, Iterator

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

    _TOPIC_SEPARATOR = " / "
    _MAX_TOPIC_CHARS = 220

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
                self._walk_li(
                    entry,
                    ancestor_topics=[],
                    event_date=event_date,
                    page_url=page_url,
                    category=current_category,
                    out=parsed_events,
                )

        if parsed_events:
            return parsed_events

        for entry in container.select("li"):
            self._walk_li(
                entry,
                ancestor_topics=[],
                event_date=event_date,
                page_url=page_url,
                category="current_events",
                out=parsed_events,
            )
        return parsed_events

    def _walk_li(
        self,
        entry: Tag,
        *,
        ancestor_topics: list[str],
        event_date: date,
        page_url: str,
        category: str,
        out: list[CandidateEvent],
    ) -> None:
        """递归处理 li：含直接外链则产出一个事件，否则把自身主题入栈再下钻。"""
        direct_external_urls: list[str] = []
        direct_wiki_links: list[str] = []
        for anchor in self._iter_direct_anchors(entry):
            absolute = absolutize(page_url, anchor.get("href"))
            if not absolute:
                continue
            if absolute.startswith("https://en.wikipedia.org/wiki/"):
                direct_wiki_links.append(absolute)
                continue
            if absolute.startswith("http") and "wikipedia.org" not in absolute:
                direct_external_urls.append(absolute)

        topic_label = self._first_direct_anchor_text(entry)

        if direct_external_urls:
            summary = self._extract_leaf_summary(entry, page_url)
            if summary:
                if ancestor_topics:
                    topic = self._TOPIC_SEPARATOR.join(ancestor_topics)
                else:
                    topic = topic_label or summary
                topic = topic[: self._MAX_TOPIC_CHARS]
                domain = self._CATEGORY_DOMAIN_HINTS.get(category.lower(), "other")
                topic_path = list(ancestor_topics) if ancestor_topics else (
                    [topic_label] if topic_label else []
                )
                out.append(
                    CandidateEvent.from_source(
                        source=self.source_name,
                        event_date=event_date,
                        topic=topic,
                        summary=summary,
                        domain=domain,
                        source_url=page_url,
                        evidence_urls=sorted(set(direct_external_urls)),
                        raw={
                            "wikipedia_links": sorted(set(direct_wiki_links)),
                            "topic_path": topic_path,
                            "category": category,
                        },
                    )
                )

        new_ancestors = (
            ancestor_topics + [topic_label] if topic_label else list(ancestor_topics)
        )
        for nested in self._iter_direct_nested_lis(entry):
            self._walk_li(
                nested,
                ancestor_topics=new_ancestors,
                event_date=event_date,
                page_url=page_url,
                category=category,
                out=out,
            )

    def _extract_leaf_summary(self, entry: Tag, page_url: str) -> str:
        summary = self._direct_list_item_text(entry, page_url)
        if not summary:
            summary = normalize_whitespace(entry.get_text(" ", strip=True))
        summary = self._strip_trailing_source_citation(summary)
        return clip_text(summary, limit=1800)

    @staticmethod
    def _iter_direct_anchors(entry: Tag) -> Iterator[Tag]:
        for child in entry.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue
            if child.name in {"ul", "ol", "sup"}:
                continue
            if child.name == "a":
                yield child
            else:
                yield from child.find_all("a")

    @staticmethod
    def _iter_direct_nested_lis(entry: Tag) -> Iterator[Tag]:
        for child in entry.children:
            if isinstance(child, Tag) and child.name in {"ul", "ol"}:
                for nested in child.find_all("li", recursive=False):
                    yield nested

    def _first_direct_anchor_text(self, entry: Tag) -> str | None:
        for anchor in self._iter_direct_anchors(entry):
            text = normalize_whitespace(anchor.get_text())
            if text:
                return text[: self._MAX_TOPIC_CHARS]
        return None

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
        remaining = " ".join(text for text, _ in pieces[1:]).strip()
        return bool(remaining and remaining[0].isupper())

    @staticmethod
    def _strip_trailing_source_citation(summary: str) -> str:
        cleaned = re.sub(r"\s*\([^()]{1,80}\)\s*$", "", summary).strip()
        return WikipediaCurrentEventsCrawler._normalize_summary_text(cleaned)

    @staticmethod
    def _normalize_summary_text(value: str) -> str:
        normalized = normalize_whitespace(value)
        normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
        normalized = re.sub(r"([\(\[])\s+", r"\1", normalized)
        normalized = re.sub(r"\s+([\)\]])", r"\1", normalized)
        return normalized
