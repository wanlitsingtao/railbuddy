"""抓取器基类 - 定义统一接口和公共逻辑"""

import time
import logging
from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ..models import BidItem
from ..utils.text import generate_item_id, extract_date, clean_url, truncate_text

logger = logging.getLogger(__name__)


class BaseFetcher(ABC):
    """抓取器抽象基类

    所有数据源抓取器（网站、公众号等）都继承此类
    """

    def __init__(self, config: dict):
        self.config = config
        self.name: str = config.get("name", "未知源")
        self.keywords: List[str] = config.get("keywords", [])
        self.timeout: int = config.get("timeout", 15)
        self.request_interval: float = config.get("request_interval", 2)
        self.enabled: bool = config.get("enabled", True)
        self.verify_ssl: bool = config.get("verify_ssl", True)

        # HTTP 会话
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    @abstractmethod
    def fetch(self, since_time: Optional[str] = None) -> List[BidItem]:
        """抓取新信息

        Args:
            since_time: 只抓取此时间之后发布的信息（ISO 格式），None 表示全量抓取

        Returns:
            抓取到的条目列表
        """
        ...

    def _filter_by_keywords(self, title: str, content: str = "") -> bool:
        """关键词过滤：标题或内容命中任一关键词即保留"""
        if not self.keywords:
            return True
        text = (title or "") + " " + (content or "")
        for kw in self.keywords:
            if kw in text:
                return True
        return False

    def _filter_by_date(self, publish_date: Optional[str],
                        since_time: Optional[str]) -> bool:
        """日期过滤：跳过早于 since_time 的条目

        如果无法解析日期则保留（宁可多抓不可遗漏）
        """
        if not since_time or not publish_date:
            return True  # 无法判断日期时保留

        try:
            pub = datetime.strptime(publish_date, "%Y-%m-%d")
            since = datetime.fromisoformat(since_time)
            return pub >= since
        except (ValueError, TypeError):
            return True  # 日期解析失败时保留

    def _create_item(self, title: str, url: str, publish_date: Optional[str] = None,
                     description: str = "", category: str = "") -> Optional[BidItem]:
        """创建 BidItem 对象，自动生成 ID"""
        if not title or not url:
            return None

        item_id = generate_item_id(url, title)
        return BidItem(
            item_id=item_id,
            title=title.strip(),
            url=url.strip(),
            source=self.name,
            publish_date=publish_date,
            description=truncate_text(description, 500),
            category=category,
        )

    def _rate_limit(self):
        """速率限制：在请求之间等待"""
        if self.request_interval > 0:
            time.sleep(self.request_interval)

    def _get_html(self, url: str, retry: int = 2) -> Optional[str]:
        """发起 HTTP GET 请求并返回 HTML 文本

        Args:
            url: 请求 URL
            retry: 失败重试次数
        """
        for attempt in range(retry + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout, verify=self.verify_ssl)
                resp.encoding = resp.apparent_encoding or "utf-8"
                if resp.status_code == 200:
                    return resp.text
                logger.warning(
                    f"[{self.name}] HTTP {resp.status_code} 访问 {url} "
                    f"(尝试 {attempt + 1}/{retry + 1})"
                )
            except requests.RequestException as e:
                logger.warning(
                    f"[{self.name}] 请求异常: {e} (尝试 {attempt + 1}/{retry + 1})"
                )
            if attempt < retry:
                self._rate_limit()
        return None

    def _parse_html(self, html: str) -> BeautifulSoup:
        """解析 HTML"""
        return BeautifulSoup(html, "lxml")
