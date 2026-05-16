from __future__ import annotations

"""IFES ElectionGuide 列表页与详情页爬虫。"""

import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from requests import Session

from ..http_client import fetch_html, soup_from_html
from ..models import CandidateEvent
from ..utils import clip_text, normalize_whitespace, parse_date_guess
from .base import BaseSourceCrawler, CrawlContext


class IFESElectionGuideCrawler(BaseSourceCrawler):
    """从 IFES 列表页发现选举链接，并抓取详情页事件。"""

    source_name = "ifes_electionguide"
    base_url = "https://www.electionguide.org"
    list_urls = [
        "https://www.electionguide.org/elections/type/past/",
        "https://www.electionguide.org/elections/type/upcoming/",
    ]
    election_link_pattern = re.compile(r"/elections/id/\d+/?$")

    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """先发现详情页 URL，再逐页解析。"""
        raw_payloads: list[dict] = []
        detail_urls = self._collect_detail_urls(
            context=context,
            session=session,
            raw_payloads=raw_payloads,
        )
        events: list[CandidateEvent] = []
        for detail_url in sorted(detail_urls):
            event = self._fetch_detail_event(
                detail_url=detail_url,
                context=context,
                session=session,
            )
            if event is None:
                continue
            events.append(event)
        raw_payloads.append({"detail_url_count": len(detail_urls)})
        return events, raw_payloads

    def _collect_detail_urls(
        self,
        *,
        context: CrawlContext,
        session: Session,
        raw_payloads: list[dict],
    ) -> set[str]:
        """对分页列表做广度优先遍历，收集详情页链接。"""
        discovered: set[str] = set()
        max_pages_per_stream = 20
        for start_url in self.list_urls:
            visited: set[str] = set()
            queue = [start_url]
            page_count = 0
            while queue and page_count < max_pages_per_stream:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                page_count += 1
                try:
                    html = fetch_html(
                        session,
                        url,
                        timeout=context.settings.request_timeout_seconds,
                    )
                except Exception as exc:  # noqa: BLE001
                    raw_payloads.append({"list_url": url, "error": str(exc)})
                    continue

                soup = soup_from_html(html)
                discovered.update(self._extract_election_links(soup, url))
                for next_url in self._extract_pagination_links(soup, url):
                    if next_url not in visited:
                        queue.append(next_url)
                raw_payloads.append({"list_url": url, "discovered_count": len(discovered)})
        return discovered

    def _extract_election_links(self, soup: BeautifulSoup, page_url: str) -> set[str]:
        """从列表页提取 election 详情链接。"""
        links: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            if not href:
                continue
            if not self.election_link_pattern.search(href):
                continue
            links.add(urljoin(page_url, href))
        return links

    def _extract_pagination_links(self, soup: BeautifulSoup, page_url: str) -> set[str]:
        """提取可继续翻页的链接。"""
        pagination_links: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            text = normalize_whitespace(anchor.get_text())
            if not href:
                continue
            absolute = urljoin(page_url, href)
            if "/elections/type/" not in absolute:
                continue
            if "page=" in absolute or text.lower() in {"next", "next »", "older"}:
                pagination_links.add(absolute)
        return pagination_links

    def _fetch_detail_event(
        self, *, detail_url: str, context: CrawlContext, session: Session
    ) -> CandidateEvent | None:
        """将单个选举详情页解析成候选事件。"""
        try:
            html = fetch_html(
                session,
                detail_url,
                timeout=context.settings.request_timeout_seconds,
            )
        except Exception:
            return None

        soup = soup_from_html(html)
        title = self._extract_title(soup)
        if not title:
            return None

        metadata = self._extract_metadata(soup)
        event_date = self._extract_event_date(metadata, title)
        if event_date is None:
            return None
        if not (context.start_date <= event_date <= context.end_date):
            return None

        election_type = metadata.get("type") or metadata.get("election type") or "election"
        summary = clip_text(
            normalize_whitespace(
                " | ".join(
                    part
                    for part in [
                        election_type,
                        metadata.get("electoral system"),
                        metadata.get("election for"),
                        metadata.get("status"),
                    ]
                    if part
                )
            ),
            limit=900,
        )

        evidence_urls = self._extract_reference_urls(soup, detail_url)
        return CandidateEvent.from_source(
            source=self.source_name,
            event_date=event_date,
            topic=title,
            summary=summary,
            domain="politics",
            source_url=detail_url,
            evidence_urls=evidence_urls,
            raw={"metadata": metadata, "election_type": election_type},
        )

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        """兼容不同模板，提取页面主标题。"""
        for selector in ("h1", "h1.page-title", ".entry-title"):
            node = soup.select_one(selector)
            if node is None:
                continue
            text = normalize_whitespace(node.get_text())
            if text:
                return text
        return ""

    @staticmethod
    def _extract_metadata(soup: BeautifulSoup) -> dict[str, str]:
        """从元数据表格中提取键值对。"""
        metadata: dict[str, str] = {}
        for row in soup.select("table tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            key = normalize_whitespace(cells[0].get_text()).lower().rstrip(":")
            value = normalize_whitespace(cells[1].get_text())
            if key and value:
                metadata[key] = value
        return metadata

    @staticmethod
    def _extract_event_date(metadata: dict[str, str], title: str) -> date | None:
        """优先用元数据推断日期，失败时回退到标题文本。"""
        date_candidates = [
            metadata.get("election date"),
            metadata.get("date"),
            metadata.get("poll date"),
            title,
        ]
        for candidate in date_candidates:
            if not candidate:
                continue
            parsed = parse_date_guess(candidate)
            if parsed:
                return parsed
        return None

    @staticmethod
    def _extract_reference_urls(soup: BeautifulSoup, detail_url: str) -> list[str]:
        """提取并标准化详情页中的证据链接。"""
        urls: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            if not href:
                continue
            absolute = urljoin(detail_url, href)
            if absolute.startswith("http"):
                urls.add(absolute)
        return sorted(urls)
