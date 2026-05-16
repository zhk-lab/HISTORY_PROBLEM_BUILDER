from __future__ import annotations

"""爬虫流程使用的数据模型。"""

import hashlib
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def build_event_id(
    source: str, source_url: str | None, event_date: date, topic: str
) -> str:
    """生成稳定 event_id，用于去重与追踪。"""
    payload = "|".join(
        [
            source.strip().lower(),
            (source_url or "").strip().lower(),
            event_date.isoformat(),
            topic.strip().lower(),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:20]


class CandidateEvent(BaseModel):
    """统一事件结构，最终写入 JSONL/CSV。"""

    model_config = ConfigDict(extra="allow")

    event_id: str
    source: str
    domain: str = "other"
    event_date: date
    topic: str
    summary: str = ""
    source_url: str | None = None
    evidence_urls: list[str] = Field(default_factory=list)  #可以作证该事件的URL
    raw: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    filter_reason: str | None = None
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    @classmethod
    def from_source(
        cls,
        *,
        source: str,
        event_date: date,
        topic: str,
        summary: str = "",
        domain: str = "other",
        source_url: str | None = None,
        evidence_urls: list[str] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> "CandidateEvent":
        """将来源字段规范化后构造统一事件对象。"""
        return cls(
            event_id=build_event_id(source, source_url, event_date, topic),
            source=source,
            event_date=event_date,
            topic=topic.strip(),
            summary=summary.strip(),
            domain=domain,
            source_url=source_url.strip() if source_url else None,
            evidence_urls=evidence_urls or [],
            raw=raw or {},
        )

    def as_serializable_dict(self) -> dict[str, Any]:
        """返回可直接序列化为 JSON 的字典。"""
        return self.model_dump(mode="json")

    def as_csv_row(self) -> dict[str, str]:
        """返回扁平化 CSV 行数据。"""
        return {
            "event_id": self.event_id,
            "source": self.source,
            "domain": self.domain,
            "event_date": self.event_date.isoformat(),
            "topic": self.topic,
            "summary": self.summary,
            "source_url": self.source_url or "",
            "evidence_urls": "; ".join(self.evidence_urls),
            "quality_flags": "; ".join(self.quality_flags),
            "filter_reason": self.filter_reason or "",
            "fetched_at": self.fetched_at.isoformat(),
        }
