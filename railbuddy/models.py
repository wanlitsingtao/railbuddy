"""数据模型定义"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from .utils.text import generate_item_id


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
class TransitMileage:
    """城市轨道交通里程数据（按线路粒度）

    制式分类（system_type）：
    - 地铁：城市地下铁路系统
    - 轻轨：轻型城市轨道交通
    - 单轨：跨座式或悬挂式单轨（如重庆）
    - 市域铁路：市域快速轨道交通（如上海机场联络线、温州S1）
    - 城际铁路：城市群城际轨道交通
    - 有轨电车：地面有轨电车系统
    - 磁浮：磁悬浮轨道交通（如上海磁浮）
    """
    city: str                              # 城市（如"北京"）
    system_name: str = ""                 # 系统名称（如"北京地铁"）
    line_name: str = ""                   # 线路名称（如"1号线"）
    system_type: str = "地铁"             # 制式：地铁/轻轨/单轨/市域铁路/城际铁路/有轨电车/磁浮
    length_km: Optional[float] = None     # 里程（公里）
    stations: Optional[int] = None       # 车站数
    opening_date: Optional[str] = None   # 开通日期 YYYY-MM-DD
    status: str = "operational"           # operational/under_construction/planned
    data_source: str = ""                 # 数据来源（如"Wikipedia"）
    data_month: str = ""                 # 数据月份 YYYY-MM
    fetched_at: str = ""                 # 抓取时间 ISO 格式
    line_id: str = ""                    # 唯一标识（city + line_name 的 MD5）

    def __post_init__(self):
        if not self.fetched_at:
            self.fetched_at = datetime.now().isoformat()
        if not self.data_month:
            self.data_month = datetime.now().strftime("%Y-%m")
        if not self.line_id:
            self.line_id = generate_item_id(self.city, self.line_name)

    def to_dict(self) -> dict:
        return {
            "city": self.city,
            "system_name": self.system_name,
            "line_name": self.line_name,
            "system_type": self.system_type,
            "length_km": self.length_km,
            "stations": self.stations,
            "opening_date": self.opening_date or "",
            "status": self.status,
            "data_source": self.data_source,
            "data_month": self.data_month,
            "fetched_at": self.fetched_at,
            "line_id": self.line_id,
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
