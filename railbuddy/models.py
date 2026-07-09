"""数据模型定义"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class BidItem:
    """招标信息条目"""
    title: str                          # 标题
    url: str                            # 链接地址
    source: str                         # 来源名称
    item_id: str = ""                   # 唯一ID（MD5 哈希）
    publish_date: Optional[str] = None  # 发布日期 YYYY-MM-DD
    description: str = ""               # 摘要/详情
    fetched_at: str = ""                # 抓取时间 ISO 格式
    category: str = ""                  # 分类（招标计划/招标/中标/开通运营/变更）

    def __post_init__(self):
        if not self.fetched_at:
            self.fetched_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "publish_date": self.publish_date or "",
            "description": self.description,
            "fetched_at": self.fetched_at,
            "category": self.category,
        }


@dataclass
class SourceState:
    """数据源状态"""
    name: str                           # 源名称
    last_fetch_time: Optional[str] = None  # 上次抓取时间
    last_send_time: Optional[str] = None   # 上次发送时间
    last_fetch_count: int = 0           # 上次抓取条数
    last_fetch_status: str = "pending"  # pending/success/failed
    last_error: str = ""                # 最后错误信息
