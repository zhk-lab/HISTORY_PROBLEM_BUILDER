from __future__ import annotations

"""各来源爬虫共享的抽象基类与上下文。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from requests import Session

from ..config import Settings
from ..models import CandidateEvent


@dataclass(frozen=True)
class CrawlContext:
    """传给每个来源爬虫的不可变运行上下文。"""

    start_date: date
    end_date: date
    settings: Settings


class BaseSourceCrawler(ABC):
    """每个来源爬虫必须实现的统一协议。"""

    source_name: str
    required_credential: str | None = None

    def can_run(self, context: CrawlContext) -> tuple[bool, str | None]:
        """检查当前来源是否满足运行前置条件（如凭据）。"""
        if self.required_credential is None:
            return True, None
        value = getattr(context.settings, self.required_credential, None)
        if value:
            return True, None
        return False, f"missing credential: {self.required_credential}"

    @abstractmethod
    def fetch(
        self, context: CrawlContext, session: Session
    ) -> tuple[list[CandidateEvent], list[dict]]:
        """
        返回二元组：
        - candidate events：标准化候选事件列表
        - diagnostic records：仅运行时用于统计和排查，不写入 data/raw
        """

