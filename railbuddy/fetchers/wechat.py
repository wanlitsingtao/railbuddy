"""公众号抓取器 - 通过搜狗微信搜索或 RSS 抓取公众号最新文章"""

import logging
from typing import List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from .base import BaseFetcher
from ..models import BidItem
from ..utils.text import extract_date, truncate_text, html_to_plain_text

logger = logging.getLogger(__name__)


class WechatFetcher(BaseFetcher):
    """微信公众号文章抓取器

    支持两种抓取方式：
    1. sogou: 通过搜狗微信搜索 (weixin.sogou.com) 搜索公众号最新文章
       - 优点：无需额外服务
       - 缺点：可能被反爬限制，不稳定
    2. rss: 通过 RSS 订阅源抓取（推荐使用 WeRSS 等服务将公众号转为 RSS）
       - 优点：稳定可靠
       - 缺点：需要配置第三方 RSS 服务
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.fetch_method: str = config.get("fetch_method", "sogou")
        self.official_id: str = config.get("official_id", "")
        self.search_keyword: str = config.get("search_keyword", "")
        self.rss_url: str = config.get("rss_url", "")

    def fetch(self, since_time: Optional[str] = None) -> List[BidItem]:
        """根据配置的抓取方式执行抓取"""
        if self.fetch_method == "rss":
            return self._fetch_via_rss(since_time)
        else:
            return self._fetch_via_sogou(since_time)

    def _fetch_via_sogou(self, since_time: Optional[str]) -> List[BidItem]:
        """通过搜狗微信搜索抓取"""
        results: List[BidItem] = []

        # 构造搜索关键词
        query = self.search_keyword or self.official_id or self.name
        search_url = (
            f"https://weixin.sogou.com/weixin?"
            f"type=2&query={quote(query)}&ie=utf8"
        )

        logger.info(f"[{self.name}] 搜狗微信搜索: {search_url}")
        html = self._get_html(search_url)
        if not html:
            logger.warning(f"[{self.name}] 搜狗搜索页面获取失败，可能被反爬限制")
            return []

        soup = self._parse_html(html)

        # 搜狗微信搜索结果页面结构可能变化，尝试多种选择器
        article_selectors = [
            ".news-list li",
            ".news-box .txt-box",
            ".wx-rb",
            "div[class*='news'] li",
            "ul.news_list li",
        ]

        articles = []
        for sel in article_selectors:
            articles = soup.select(sel)
            if articles:
                break

        if not articles:
            logger.warning(f"[{self.name}] 搜狗搜索未匹配到文章，页面结构可能已变化")
            return []

        logger.info(f"[{self.name}] 搜狗搜索找到 {len(articles)} 篇文章")

        for article in articles:
            item = self._parse_sogou_article(article, since_time)
            if item:
                results.append(item)

        return results

    def _parse_sogou_article(self, article_elem, since_time) -> Optional[BidItem]:
        """解析搜狗搜索结果中的单篇文章"""
        # 提取标题和链接 - 标题在 h3 > a 中，不是第一个 a（图片链接）
        title_elem = article_elem.select_one("h3 a") or article_elem.find("a")
        if not title_elem:
            return None

        title = title_elem.get_text(strip=True)
        url = title_elem.get("href", "")

        if not title or not url:
            return None

        # 搜狗的链接是临时跳转链接
        if url.startswith("/"):
            url = "https://weixin.sogou.com" + url

        # 提取摘要
        desc = ""
        desc_selectors = [".txt-info", "p.txt-info", ".article-desc"]
        for sel in desc_selectors:
            desc_elem = article_elem.select_one(sel)
            if desc_elem:
                desc = desc_elem.get_text(strip=True)
                break

        # 提取发布时间 - 搜狗用 script 标签嵌入 Unix 时间戳
        publish_date = None
        # 方式1: 从 script 标签中提取时间戳
        import re
        from datetime import datetime
        scripts = article_elem.find_all("script")
        for script in scripts:
            script_text = script.string or ""
            # timeConvert('1669711998') 格式
            ts_match = re.search(r"timeConvert\(['\"](\d+)['\"]\)", script_text)
            if not ts_match:
                ts_match = re.search(r"timeConvert\((\d+)\)", script_text)
            if ts_match:
                try:
                    ts = int(ts_match.group(1))
                    publish_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    pass
                break

        # 方式2: 从 s-p 中的 span 提取
        if not publish_date:
            time_selectors = [".s-p .s2", ".s-p span", ".account", ".info span"]
            for sel in time_selectors:
                time_elem = article_elem.select_one(sel)
                if time_elem:
                    publish_date = extract_date(time_elem.get_text(strip=True))
                    if publish_date:
                        break
        # 方式3: 从条目文本中提取
        if not publish_date:
            publish_date = extract_date(article_elem.get_text(strip=True))

        # 关键词过滤
        if not self._filter_by_keywords(title, desc):
            return None

        # 日期过滤
        if not self._filter_by_date(publish_date, since_time):
            return None

        return self._create_item(
            title=title, url=url, publish_date=publish_date,
            description=desc, category=self._guess_category(title)
        )

    def _fetch_via_rss(self, since_time: Optional[str]) -> List[BidItem]:
        """通过 RSS 源抓取公众号文章

        需要配置第三方 RSS 服务（如 WeRSS、feeddd 等）将公众号转为 RSS 订阅源
        """
        if not self.rss_url:
            logger.warning(f"[{self.name}] 未配置 rss_url，跳过 RSS 抓取")
            return []

        results: List[BidItem] = []

        html = self._get_html(self.rss_url)
        if not html:
            logger.error(f"[{self.name}] RSS 源获取失败: {self.rss_url}")
            return []

        soup = self._parse_html(html)

        # RSS 标准: <item> 标签
        items = soup.find_all("item")
        if not items:
            # 尝试 Atom 格式: <entry> 标签
            items = soup.find_all("entry")

        logger.info(f"[{self.name}] RSS 源找到 {len(items)} 篇文章")

        for item in items:
            bid_item = self._parse_rss_item(item, since_time)
            if bid_item:
                results.append(bid_item)

        return results

    def _parse_rss_item(self, item_elem, since_time) -> Optional[BidItem]:
        """解析 RSS/Atom 条目"""
        # 标题
        title_elem = item_elem.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""
        if not title:
            return None

        # 链接
        link_elem = item_elem.find("link")
        url = ""
        if link_elem:
            url = link_elem.get_text(strip=True) or link_elem.get("href", "")
        if not url:
            return None

        # 摘要
        desc = ""
        desc_elem = item_elem.find("description") or item_elem.find("summary") \
            or item_elem.find("content")
        if desc_elem:
            desc = html_to_plain_text(desc_elem.get_text(strip=True))

        # 发布日期
        publish_date = None
        date_elem = item_elem.find("pubDate") or item_elem.find("published") \
            or item_elem.find("updated")
        if date_elem:
            publish_date = extract_date(date_elem.get_text(strip=True))

        # 关键词过滤
        if not self._filter_by_keywords(title, desc):
            return None

        # 日期过滤
        if not self._filter_by_date(publish_date, since_time):
            return None

        return self._create_item(
            title=title, url=url, publish_date=publish_date,
            description=desc, category=self._guess_category(title)
        )
