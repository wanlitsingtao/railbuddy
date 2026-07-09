"""网站抓取器 - 抓取招标信息网站列表页和详情页"""

import logging
from typing import List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from .base import BaseFetcher
from ..models import BidItem
from ..utils.text import extract_date, clean_url, html_to_plain_text, truncate_text, clean_title

logger = logging.getLogger(__name__)


class WebsiteFetcher(BaseFetcher):
    """网站数据源抓取器

    工作流程：
    1. 请求列表页 URL
    2. 用 CSS 选择器定位每条招标信息
    3. 提取标题、链接、发布日期
    4. 关键词过滤 + 日期过滤（增量抓取）
    5. 可选：抓取详情页获取完整内容
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.url: str = config.get("url", "")
        self.list_selector: str = config.get("list_selector", "li")
        self.title_selector: str = config.get("title_selector", "a")
        self.link_selector: str = config.get("link_selector", "a")
        self.date_selector: str = config.get("date_selector", "")
        self.link_prefix: str = config.get("link_prefix", "")
        self.fetch_detail: bool = config.get("fetch_detail", False)
        self.detail_content_selector: str = config.get("detail_content_selector", ".content")

    def fetch(self, since_time: Optional[str] = None) -> List[BidItem]:
        """抓取网站列表页"""
        if not self.url:
            logger.warning(f"[{self.name}] 未配置 URL，跳过")
            return []

        results: List[BidItem] = []
        logger.info(f"[{self.name}] 开始抓取列表页: {self.url}")

        html = self._get_html(self.url)
        if not html:
            logger.error(f"[{self.name}] 列表页获取失败")
            return []

        soup = self._parse_html(html)
        items = soup.select(self.list_selector)
        logger.info(f"[{self.name}] 列表页找到 {len(items)} 个条目")

        for item_elem in items:
            bid_item = self._parse_list_item(item_elem, since_time)
            if bid_item:
                results.append(bid_item)

                # 可选：抓取详情页
                if self.fetch_detail and bid_item.url:
                    self._rate_limit()
                    detail = self._fetch_detail_page(bid_item.url)
                    if detail:
                        bid_item.description = truncate_text(detail, 1000)

        logger.info(f"[{self.name}] 抓取完成，有效条目 {len(results)} 个")
        return results

    def _parse_list_item(self, item_elem: Tag,
                         since_time: Optional[str]) -> Optional[BidItem]:
        """解析列表页中的单个条目"""
        # 提取标题
        title_elem = item_elem.select_one(self.title_selector) or item_elem
        title = title_elem.get_text(strip=True) if title_elem else ""
        # 清理标题（去除前缀标签、项目符号等）
        title = clean_title(title)

        # 提取链接
        link_elem = item_elem.select_one(self.link_selector) or title_elem
        href = ""
        if link_elem and link_elem.name == "a":
            href = link_elem.get("href", "")
        elif item_elem.name == "a":
            href = item_elem.get("href", "")

        if not href:
            return None

        url = clean_url(href, self.link_prefix)

        # 提取发布日期
        publish_date = None
        if self.date_selector:
            date_elem = item_elem.select_one(self.date_selector)
            if date_elem:
                publish_date = extract_date(date_elem.get_text(strip=True))
        if not publish_date:
            # 尝试从条目文本中提取日期
            publish_date = extract_date(item_elem.get_text(strip=True))

        # 关键词过滤
        if not self._filter_by_keywords(title):
            return None

        # 日期过滤（增量抓取）
        if not self._filter_by_date(publish_date, since_time):
            return None

        return self._create_item(
            title=title, url=url, publish_date=publish_date,
            description="", category=self._guess_category(title)
        )

    def _fetch_detail_page(self, url: str) -> str:
        """抓取详情页内容"""
        html = self._get_html(url)
        if not html:
            return ""

        soup = self._parse_html(html)
        content_elem = soup.select_one(self.detail_content_selector)
        if content_elem:
            return html_to_plain_text(str(content_elem))

        # 回退：尝试常见的正文选择器
        for selector in [".article-content", ".news-content", "#content",
                         ".content-body", ".detail-content", "article"]:
            elem = soup.select_one(selector)
            if elem:
                return html_to_plain_text(str(elem))

        return ""

    @staticmethod
    def _guess_category(title: str) -> str:
        """根据标题猜测招标类别"""
        title = title or ""
        if "中标" in title or "成交" in title:
            return "中标"
        if "招标计划" in title or "计划" in title:
            return "招标计划"
        # 开通运营：仅匹配"开通""通车""试运营"等明确事件词
        # 不匹配单独的"运营"（避免公司名"xx运营集团"误判）
        if any(kw in title for kw in ["开通", "通车", "试运营", "投入运营", "正式运营", "开通试运营", "初期运营"]):
            return "开通运营"
        if "招标" in title or "采购" in title:
            return "招标"
        if "变更" in title or "澄清" in title:
            return "变更"
        return ""
