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
class BidRecord:
    """项目中标记录

    来源：轨道交通通信/综合监控/信号/安防/PPP 中标信息汇总表
    去重主键：record_id = MD5(project_name + bid_date)
    """
    record_id: str = ""                    # 唯一ID（MD5(project_name + bid_date)）
    province: str = ""                     # 省份
    city: str = ""                         # 城市
    category: str = ""                     # 工程分类（通信/ISCS/信号/安防/PPP/BOT等）
    winner: str = ""                       # 中标单位
    consortium: str = ""                   # 联合体（是/否）
    project_name: str = ""                 # 项目名称
    project_overview: str = ""             # 项目概况
    bid_scope: str = ""                    # 招标范围
    subsystems: str = ""                   # 包含子系统
    bid_threshold: str = ""                # 招标门槛
    bidder: str = ""                       # 招标人
    funding_source: str = ""               # 资金来源
    evaluation_method: str = ""            # 评标方法
    total_stations: Optional[int] = None   # 招标站点总数（座）
    underground_stations: Optional[int] = None  # 地下站（座）
    elevated_stations: Optional[int] = None     # 高架站（座）
    ground_stations: Optional[int] = None       # 地面站（座）
    opened_stations: Optional[int] = None       # 开通站点数（座）
    line_type: str = ""                    # 线路类型（CBTC/UTO/FAO等）
    length_km: Optional[float] = None      # 里程（公里）
    goa_level: str = ""                    # GoA设计等级
    system_mode: str = ""                  # 系统制式
    is_opened: str = ""                    # 是否开通（是/否）
    opening_date: Optional[str] = None     # 开通时间 YYYY-MM-DD
    bid_date: Optional[str] = None         # 中标时间 YYYY-MM-DD
    bid_amount: Optional[float] = None     # 中标金额（万元）
    control_price: Optional[float] = None  # 控制价（万元）
    bid_link: str = ""                     # 中标链接
    tender_link: str = ""                  # 招标链接
    design_unit: str = ""                  # 设计单位
    platform_software_pis: str = ""        # 采用的平台软件（PIS）
    notes: str = ""                        # 备注
    data_source: str = "excel_import"      # 数据来源
    created_at: str = ""                   # 创建时间
    updated_at: str = ""                   # 更新时间

    def __post_init__(self):
        if not self.record_id:
            self.record_id = generate_item_id(self.project_name, self.bid_date or "")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "province": self.province,
            "city": self.city,
            "category": self.category,
            "winner": self.winner,
            "consortium": self.consortium,
            "project_name": self.project_name,
            "project_overview": self.project_overview,
            "bid_scope": self.bid_scope,
            "subsystems": self.subsystems,
            "bid_threshold": self.bid_threshold,
            "bidder": self.bidder,
            "funding_source": self.funding_source,
            "evaluation_method": self.evaluation_method,
            "total_stations": self.total_stations,
            "underground_stations": self.underground_stations,
            "elevated_stations": self.elevated_stations,
            "ground_stations": self.ground_stations,
            "opened_stations": self.opened_stations,
            "line_type": self.line_type,
            "length_km": self.length_km,
            "goa_level": self.goa_level,
            "system_mode": self.system_mode,
            "is_opened": self.is_opened,
            "opening_date": self.opening_date or "",
            "bid_date": self.bid_date or "",
            "bid_amount": self.bid_amount,
            "control_price": self.control_price,
            "bid_link": self.bid_link,
            "tender_link": self.tender_link,
            "design_unit": self.design_unit,
            "platform_software_pis": self.platform_software_pis,
            "notes": self.notes,
            "data_source": self.data_source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
