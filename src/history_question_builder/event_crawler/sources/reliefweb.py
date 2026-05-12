from __future__ import annotations

"""ReliefWeb 报告爬虫（需要预批准 appname）。"""

from datetime import date

from dateutil import parser as date_parser
from requests import Session

from ..models import CandidateEvent
from ..utils import clip_text, normalize_whitespace
from .base import BaseSourceCrawler, CrawlContext


class ReliefWebCrawler(BaseSourceCrawler):
    """按日期范围分页抓取 ReliefWeb 报告。"""

    source_name = "reliefweb"
    required_credential = "reliefweb_appname"
    endpoint = "https://api.reliefweb.int/v2/reports"

    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """调用 ReliefWeb v2 接口并分页汇总事件。"""
        appname = context.settings.reliefweb_appname
        if not appname:
            return [], [{"skipped": "missing reliefweb_appname"}]

        events: list[CandidateEvent] = []
        raw_payloads: list[dict] = []
        limit = 100
        max_pages = 30
        for page in range(max_pages):
            offset = page * limit
            body = {
                "limit": limit,
                "offset": offset,
                "sort": ["date.created:desc"],
                "fields": {
                    "include": [
                        "id",
                        "url_alias",
                        "title",
                        "date.created",
                        "theme.name",
                        "disaster.name",
                        "source.name",
                        "body",
                    ]
                },
                "filter": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "date.created",
                            "value": {
                                "from": context.start_date.isoformat(),
                                "to": context.end_date.isoformat(),
                            },
                        }
                    ],
                },
            }
            try:
                response = session.post(
                    self.endpoint,
                    params={"appname": appname},
                    json=body,
                    timeout=context.settings.request_timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                raw_payloads.append({"offset": offset, "error": str(exc)})
                break

            data = payload.get("data", [])
            raw_payloads.append({"offset": offset, "response_count": len(data)})
            if not data:
                break
            for item in data:
                event = self._to_event(item, context.start_date, context.end_date)
                if event:
                    events.append(event)
            if len(data) < limit:
                break
        return events, raw_payloads

    def _to_event(
        self, item: dict, start_date: date, end_date: date
    ) -> CandidateEvent | None:
        """将单条 ReliefWeb 记录映射到统一事件模型。"""
        fields = item.get("fields", {})
        title = normalize_whitespace(str(fields.get("title", "")))
        if not title:
            return None

        raw_created = (
            fields.get("date", {}).get("created")
            if isinstance(fields.get("date"), dict)
            else fields.get("date.created")
        )
        if not raw_created:
            return None
        try:
            event_date = date_parser.parse(str(raw_created)).date()
        except (ValueError, OverflowError):
            return None
        if not (start_date <= event_date <= end_date):
            return None

        source_url = str(fields.get("url_alias", "")).strip()
        if source_url and source_url.startswith("/"):
            source_url = f"https://reliefweb.int{source_url}"
        if not source_url:
            source_url = f"https://reliefweb.int/report/{item.get('id')}"

        # 主题/灾害名称用于增强 summary 可读性。
        themes = []
        if isinstance(fields.get("theme"), list):
            themes = [normalize_whitespace(str(x.get("name", ""))) for x in fields["theme"] if x]
        disasters = []
        if isinstance(fields.get("disaster"), list):
            disasters = [normalize_whitespace(str(x.get("name", ""))) for x in fields["disaster"] if x]

        body_text = fields.get("body")
        summary = clip_text(normalize_whitespace(str(body_text or "")), limit=1500)
        theme_summary = ", ".join(part for part in themes + disasters if part)
        if theme_summary:
            summary = clip_text(f"{theme_summary} | {summary}" if summary else theme_summary, limit=1500)
        if not summary:
            summary = "ReliefWeb report entry."

        return CandidateEvent.from_source(
            source=self.source_name,
            event_date=event_date,
            title=title,
            summary=summary,
            domain="public_risk",
            source_url=source_url,
            evidence_urls=[source_url],
            raw={"id": item.get("id"), "themes": themes, "disasters": disasters},
        )
