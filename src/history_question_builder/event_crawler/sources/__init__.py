from __future__ import annotations

"""来源爬虫注册表与对外导出。"""

from .base import BaseSourceCrawler
from .bls_release_calendar import BLSReleaseCalendarCrawler
from .fomc import FOMCCalendarCrawler
from .fred_release_calendar import FREDReleaseCalendarCrawler
from .gdelt import GDELTDocCrawler
from .ifes_electionguide import IFESElectionGuideCrawler
from .reliefweb import ReliefWebCrawler
from .trading_economics import TradingEconomicsCalendarCrawler
from .wikipedia_current_events import WikipediaCurrentEventsCrawler


def build_source_registry() -> dict[str, BaseSourceCrawler]:
    """实例化全部来源爬虫，并建立 CLI key 到实例的映射。"""
    crawlers: list[BaseSourceCrawler] = [
        WikipediaCurrentEventsCrawler(),
        GDELTDocCrawler(),
        IFESElectionGuideCrawler(),
        FOMCCalendarCrawler(),
        BLSReleaseCalendarCrawler(),
        FREDReleaseCalendarCrawler(),
        TradingEconomicsCalendarCrawler(),
        ReliefWebCrawler(),
    ]
    return {
        "wikipedia": crawlers[0],
        "gdelt": crawlers[1],
        "ifes": crawlers[2],
        "fomc": crawlers[3],
        "bls": crawlers[4],
        "fred": crawlers[5],
        "tradingeconomics": crawlers[6],
        "reliefweb": crawlers[7],
    }


__all__ = [
    "BaseSourceCrawler",
    "build_source_registry",
]
