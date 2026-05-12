from __future__ import annotations

"""爬虫运行时配置中心。"""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_START_DATE = date(2026, 1, 1)
DEFAULT_SOURCE_NAMES = [
    "wikipedia",
    "gdelt",
    "ifes",
    "fomc",
    "bls",
    "fred",
    "tradingeconomics",
    "reliefweb",
]

# 这些占位值会被视作“未配置”。
_PLACEHOLDER_VALUES = {
    "",
    "your_api_key",
    "YOUR_API_KEY",
    "replace-with-app-name",
    "REPLACE-WITH-APP-NAME",
    "changeme",
}


def _clean_secret(value: str | None) -> str | None:
    """清洗凭据字符串，并过滤占位值。"""
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned in _PLACEHOLDER_VALUES:
        return None
    return cleaned or None


def _int_env(name: str, default: int) -> int:
    """读取整型环境变量，失败时回退默认值。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """在各模块间共享的不可变运行配置。"""

    workspace_root: Path
    event_output_dir: Path
    request_timeout_seconds: int
    max_retries: int
    user_agent: str
    trading_economics_key: str | None
    fred_api_key: str | None
    reliefweb_appname: str | None
    gdelt_query: str


def load_settings(workspace_root: Path | None = None) -> Settings:
    """
    从环境变量和工作目录默认值构建配置对象。

    返回值统一管理路径、凭据、超时和默认查询参数。
    """
    load_dotenv()
    root = (workspace_root or Path.cwd()).resolve()
    event_dir = root / "data" / "event"
    return Settings(
        workspace_root=root,
        event_output_dir=event_dir,
        request_timeout_seconds=_int_env("REQUEST_TIMEOUT_SECONDS", 30),
        max_retries=_int_env("MAX_RETRIES", 3),
        user_agent=os.getenv(
            "USER_AGENT", "HistoryEventCrawler/0.1 (+research project)"
        ).strip(),
        trading_economics_key=_clean_secret(os.getenv("TRADING_ECONOMICS_KEY")),
        fred_api_key=_clean_secret(os.getenv("FRED_API_KEY")),
        reliefweb_appname=_clean_secret(os.getenv("RELIEFWEB_APPNAME")),
        gdelt_query=os.getenv(
            "GDELT_QUERY",
            (
                "(election OR referendum OR parliament OR ceasefire OR sanctions "
                "OR inflation OR unemployment OR interest rate OR flood OR "
                "earthquake OR hurricane OR humanitarian)"
            ),
        ).strip(),
    )
