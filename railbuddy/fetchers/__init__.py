"""抓取器模块"""
from .base import BaseFetcher
from .website import WebsiteFetcher
from .wechat import WechatFetcher
from .weibo import WeiboFetcher
from .api import ApiFetcher
from .wikipedia import WikipediaFetcher
from .mot import MOTFetcher
from .playwright_detail import PlaywrightDetailFetcher

__all__ = [
    'BaseFetcher', 'WebsiteFetcher', 'WechatFetcher',
    'WeiboFetcher', 'ApiFetcher', 'WikipediaFetcher',
    'MOTFetcher', 'PlaywrightDetailFetcher',
]
