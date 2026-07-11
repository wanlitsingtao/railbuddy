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

    @staticmethod
    def _guess_category(title: str) -> str:
        """根据标题猜测信息类别

        分类优先级（高 -> 低）：
        1. 中标     - 中标/成交结果
        2. 招标计划 - 招标计划/意向
        3. 开通运营 - 开通/通车/试运营
        4. 变更     - 变更/澄清/更正/补充
        5. 招标     - 招标/采购及各类变体（比价/比选/询价等）
        6. 新闻     - 非招标中标的行业动态/事件类信息
        """
        title = title or ""
        # 中标类
        if "中标" in title or "成交" in title:
            return "中标"
        # 招标计划
        if "招标计划" in title:
            return "招标计划"
        # 开通运营：仅匹配"开通""通车""试运营"等明确事件词
        # 不匹配单独的"运营"（避免公司名"xx运营集团"误判）
        # "通车"排除"交通车辆"等误匹配
        if any(kw in title for kw in [
            "开通", "试运营", "投入运营", "正式运营",
            "开通试运营", "初期运营"
        ]) or ("通车" in title and "交通车辆" not in title):
            return "开通运营"
        # 变更类（扩展：澄清/更正/补充）
        if any(kw in title for kw in ["变更", "澄清", "更正", "补充"]):
            return "变更"
        # 招标类（扩展变体：比价/比选/询价/询比/竞争性谈判等）
        # 公共资源交易中心常见工程类标题（施工/监理/设计/勘察等）归为招标
        if any(kw in title for kw in [
            "招标", "采购", "比价", "比选", "询价", "询比",
            "竞争性谈判", "竞争性磋商", "单一来源", "框架协议",
            "资格预审", "意向公开", "需求公示", "标前公示",
            "招租", "截标",
            "施工", "监理", "勘察", "设计", "总承包", "EPC",
            "检测", "评估", "审计", "测绘", "勘察设计",
            "改造", "维修", "整治", "新建", "扩建", "迁建"
        ]):
            return "招标"
        # 新闻类：非招标中标的行业动态/事件信息
        if any(kw in title for kw in [
            "活动", "培训", "会议", "考察", "调研", "签约", "揭牌",
            "开工", "复工", "封顶", "贯通", "进展", "批复", "获批",
            "座谈", "表彰", "慰问", "检查", "督导", "验收", "启用",
            "落成", "交付", "接入", "联调", "获奖", "战略合作",
            "签署", "备忘录", "列车抵"
        ]):
            return "新闻"
        return ""
