from __future__ import annotations

"""带重试能力的 HTTP 访问工具。"""

from typing import Any

import requests
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


def build_session(user_agent: str) -> requests.Session:
    """创建共享 Session，并设置更友好的抓取请求头。"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


@retry(
    retry=retry_if_exception_type((requests.RequestException, ValueError)),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
def fetch_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any:
    """发送 GET 请求获取 JSON，并自动重试与状态校验。"""
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
def fetch_html(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> str:
    """发送 GET 请求获取 HTML 文本，并自动重试。"""
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.text


def soup_from_html(html: str) -> BeautifulSoup:
    """用 lxml 解析 HTML，返回 BeautifulSoup 对象。"""
    return BeautifulSoup(html, "lxml")
