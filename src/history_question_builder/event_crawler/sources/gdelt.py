from __future__ import annotations

"""GDELT DOC API 候选文章爬虫。"""

from datetime import datetime

from requests import Session

from ..http_client import fetch_json
from ..models import CandidateEvent
from ..utils import clip_text, normalize_whitespace
from .base import BaseSourceCrawler, CrawlContext


class GDELTDocCrawler(BaseSourceCrawler):
    """从 GDELT DOC 接口抓取事件相关新闻条目。"""

    source_name = "gdelt"
    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """按时间范围查询 GDELT，并标准化返回记录。"""
        params = {
            "query": context.settings.gdelt_query,
            "mode": "ArtList",
            "maxrecords": 250,
            "sort": "DateDesc",
            "format": "json",
            "startdatetime": context.start_date.strftime("%Y%m%d000000"),
            "enddatetime": context.end_date.strftime("%Y%m%d235959"),
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

        # GDELT 成功返回时通常是包含 articles 字段的字典。
        articles = payload.get("articles", []) if isinstance(payload, dict) else []
        events: list[CandidateEvent] = []
        for article in articles:
            title = normalize_whitespace(str(article.get("title", "")))
            if not title:
                continue
            seendate = str(article.get("seendate", "")).strip()
            event_date = self._parse_date(seendate)
            if event_date is None:
                continue
            if not (context.start_date <= event_date <= context.end_date):
                continue

            source_url = str(article.get("url", "")).strip() or None
            summary_parts = [
                str(article.get("domain", "")).strip(),
                str(article.get("sourceCountry", "")).strip(),
                str(article.get("language", "")).strip(),
            ]
            summary = clip_text(
                normalize_whitespace(
                    " | ".join(part for part in summary_parts if part)
                ),
                limit=500,
            )
            events.append(
                CandidateEvent.from_source(
                    source=self.source_name,
                    event_date=event_date,
                    topic=title,
                    summary=summary,
                    source_url=source_url,
                    raw={
                        "seendate": seendate,
                        "domain": article.get("domain"),
                        "socialimage": article.get("socialimage"),
                    },
                )
            )

        raw_payloads.append({"response_count": len(articles)})
        return events, raw_payloads

    @staticmethod
    def _parse_date(raw: str):
        """解析 GDELT 的时间串（YYYYMMDD...）为日期。"""
        if not raw:
            return None
        clean = raw.replace("T", "").replace("Z", "").strip()
        if len(clean) < 8:
            return None
        try:
            return datetime.strptime(clean[:8], "%Y%m%d").date()
        except ValueError:
            return None
