"""网站抓取器 - 抓取招标信息网站列表页和详情页"""

import logging
import re
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

    支持 link_format 配置：
    - 默认（空）：标准 <a href="..."> 链接
    - "javascript_urlopen"：解析 onclick="javascript:urlOpen('uuid')" 格式
      （如中国招标投标公共服务平台），配合 link_prefix 拼接完整 URL
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
        self.link_format: str = config.get("link_format", "")
        # 多页配置（可选）：URL 中用 {page} 占位符实现翻页
        self.url_template: str = config.get("url_template", "")
        self.max_pages: int = config.get("max_pages", 1)

    def fetch(self, since_time: Optional[str] = None) -> List[BidItem]:
        """抓取网站列表页（支持 URL 模板翻页）"""
        if not self.url and not self.url_template:
            logger.warning(f"[{self.name}] 未配置 URL，跳过")
            return []

        results: List[BidItem] = []

        # 支持 URL 模板翻页（{page} 占位符）
        if self.url_template:
            urls_to_fetch = [
                self.url_template.format(page=p)
                for p in range(1, self.max_pages + 1)
            ]
        elif self.max_pages > 1:
            # 标准 URL 翻页：在 URL 后追加 &page=N 或 ?page=N
            urls_to_fetch = []
            sep = "&" if "?" in self.url else "?"
            for p in range(1, self.max_pages + 1):
                urls_to_fetch.append(f"{self.url}{sep}page={p}")
        else:
            urls_to_fetch = [self.url]

        for page_url in urls_to_fetch:
            logger.info(f"[{self.name}] 开始抓取: {page_url}")

            html = self._get_html(page_url)
            if not html:
                logger.error(f"[{self.name}] 列表页获取失败: {page_url}")
                continue

            soup = self._parse_html(html)
            items = soup.select(self.list_selector)
            logger.info(f"[{self.name}] 列表页找到 {len(items)} 个条目")

            page_count = 0
            for item_elem in items:
                bid_item = self._parse_list_item(item_elem, since_time)
                if bid_item:
                    results.append(bid_item)
                    page_count += 1

                    # 可选：抓取详情页
                    if self.fetch_detail and bid_item.url:
                        self._rate_limit()
                        detail = self._fetch_detail_page(bid_item.url)
                        if detail:
                            bid_item.description = truncate_text(detail, 1000)

            logger.info(f"[{self.name}] 本页有效条目 {page_count} 个")

            # 如果本页条目为0，停止翻页
            if page_count == 0 and len(urls_to_fetch) > 1:
                logger.info(f"[{self.name}] 本页无有效条目，停止翻页")
                break

            if page_url != urls_to_fetch[-1]:
                self._rate_limit()

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
        # data_link 格式：链接在列表项容器自身的 data-link 属性上
        if self.link_format == "data_link":
            href = self._extract_link(item_elem, item_elem)
        else:
            link_elem = item_elem.select_one(self.link_selector) or title_elem
            href = self._extract_link(link_elem, item_elem)

        if not href:
            return None

        # 拼接完整 URL（javascript_urlopen 格式直接用字符串拼接，
        # 因为 uuid 拼到带 # 的 fragment URL 后不能用 urljoin）
        if self.link_format and self.link_prefix:
            url = self.link_prefix + href
        else:
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

    def _extract_link(self, link_elem, item_elem) -> str:
        """从元素中提取链接，支持多种格式

        支持的 link_format：
        - "" (默认)：标准 <a href="..."> 链接
        - "javascript_urlopen"：解析 href="javascript:urlOpen('uuid')" 格式
          （中国招标投标公共服务平台），提取 uuid
        - "data_link"：从 data-link 属性提取完整URL
          （中国城轨协会 camet.org.cn 等）
        """
        href = ""

        # data_link 格式：从 data-link 属性提取（优先级最高，因为不需拼接）
        if self.link_format == "data_link":
            elem = link_elem if link_elem and link_elem.name else item_elem
            href = elem.get("data-link", "") if hasattr(elem, "get") else ""
            return href

        # 默认：从 href 属性提取
        if not self.link_format:
            if link_elem and link_elem.name == "a":
                href = link_elem.get("href", "")
            elif item_elem.name == "a":
                href = item_elem.get("href", "")
            return href

        # javascript:urlOpen 格式（uuid 在 href 中，也可能在 onclick 中）
        if self.link_format == "javascript_urlopen":
            if link_elem and link_elem.name == "a":
                raw_href = link_elem.get("href", "")
                match = re.search(r"urlOpen\('([^']+)'\)", raw_href)
                if match:
                    return match.group(1)
                # 回退：检查 onclick
                onclick = link_elem.get("onclick", "")
                match = re.search(r"urlOpen\('([^']+)'\)", onclick)
                if match:
                    return match.group(1)
            return ""

        return href

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
