from __future__ import annotations

"""文本清洗、日期解析等可复用小工具。"""

import re
from datetime import date, timedelta
from urllib.parse import urljoin

from dateutil import parser as date_parser


def iter_dates(start_date: date, end_date: date):
    """按天迭代闭区间 [start_date, end_date]。"""
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def normalize_whitespace(value: str) -> str:
    """将连续空白折叠为单个空格。"""
    return re.sub(r"\s+", " ", value or "").strip()


def parse_date_guess(raw: str, *, default_year: int | None = None) -> date | None:
    """
    面向半结构化网页的“尽力而为”日期解析器。

    解析失败或存在歧义时返回 None。
    """
    cleaned = normalize_whitespace(raw)
    if not cleaned:
        return None
    default = None
    if default_year is not None:
        default = date(default_year, 1, 1)
    try:
        parsed = date_parser.parse(cleaned, fuzzy=True, default=default)
        return parsed.date()
    except (ValueError, OverflowError, TypeError):
        return None


def clip_text(value: str, limit: int = 1200) -> str:
    """清洗文本并裁剪到最大长度限制。"""
    normalized = normalize_whitespace(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def absolutize(base_url: str, href: str | None) -> str | None:
    """将相对链接转换为绝对 URL。"""
    if not href:
        return None
    return urljoin(base_url, href)
