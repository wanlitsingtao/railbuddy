"""抓取器注册与管理器 - 统一管理所有数据源抓取器"""

import logging
import re
import hashlib
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from collections import defaultdict

from .base import BaseFetcher
from .website import WebsiteFetcher
from .wechat import WechatFetcher
from .weibo import WeiboFetcher
from .api import ApiFetcher
from .wikipedia import WikipediaFetcher
from .mot import MOTFetcher
from ..models import BidItem, TransitMileage, BidRecord
from ..database import Database
from ..utils.filters import filter_rail_transit_items

logger = logging.getLogger(__name__)

# 抓取器类型注册表
FETCHER_TYPES = {
    "website": WebsiteFetcher,
    "wechat": WechatFetcher,
    "weibo": WeiboFetcher,
    "api": ApiFetcher,
    "wikipedia": WikipediaFetcher,
    "mot": MOTFetcher,
}


class FetcherManager:
    """抓取器管理器

    职责：
    1. 根据配置初始化所有抓取器
    2. 统一执行抓取任务
    3. 与数据库交互实现去重和增量抓取
    """

    def __init__(self, sources_config: List[Dict],
                 wechat_sources_config: List[Dict],
                 weibo_sources_config: Optional[List[Dict]] = None,
                 max_items_per_source: int = 100,
                 max_age_days: int = 0):
        self.fetchers: List[BaseFetcher] = []
        self.max_items_per_source = max_items_per_source
        self.max_age_days = max_age_days

        # 初始化网站抓取器
        for cfg in sources_config:
            self._add_fetcher(cfg)

        # 初始化公众号抓取器
        for cfg in wechat_sources_config:
            self._add_fetcher(cfg)

        # 初始化微博抓取器
        if weibo_sources_config:
            for cfg in weibo_sources_config:
                self._add_fetcher(cfg)

        logger.info(f"抓取器管理器初始化完成，共 {len(self.fetchers)} 个数据源")

    def _add_fetcher(self, config: Dict):
        """根据配置创建并添加抓取器"""
        fetcher_type = config.get("type", "website")
        fetcher_class = FETCHER_TYPES.get(fetcher_type)
        if fetcher_class:
            fetcher = fetcher_class(config)
            self.fetchers.append(fetcher)
            logger.debug(f"  注册抓取器: [{fetcher.name}] (type={fetcher_type})")
        else:
            logger.warning(f"未知的抓取器类型: {fetcher_type}，跳过: {config.get('name')}")

    def fetch_all(self, db: Database, date_from: str = None, date_to: str = None) -> List[BidItem]:
        """执行全量抓取

        工作流程：
        1. 遍历每个抓取器
        2. 从数据库获取该源的上次抓取时间（用于增量抓取）
        3. 执行抓取
        4. 去重：过滤掉数据库中已存在的条目
        5. 保存新条目到数据库
        6. 收集里程数据（如果抓取器支持）
        7. 更新抓取状态

        Args:
            db: Database 实例
            date_from: 可选，信息发布时间范围起始（如 "2024-07-01"）
            date_to: 可选，信息发布时间范围结束

        Returns:
            新发现的、未发送的条目列表
        """
        all_new_items: List[BidItem] = []
        all_mileage: List[TransitMileage] = []

        for fetcher in self.fetchers:
            if not fetcher.enabled:
                logger.info(f"[{fetcher.name}] 已禁用，跳过")
                continue

            try:
                end_time = None
                # 如果指定了时间范围，优先使用用户指定的时间范围
                if date_from:
                    since_time = date_from
                    if date_to:
                        # 增加一天，使 end_time 包含 date_to 当天
                        try:
                            dt_end = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
                            end_time = dt_end.isoformat()
                        except ValueError:
                            end_time = None
                    else:
                        end_time = None
                    logger.info(f"[{fetcher.name}] 按时间区间抓取: {date_from} ~ {date_to or '不限'}")
                else:
                    # 获取上次抓取时间（增量抓取的关键）
                    since_time = db.get_last_fetch_time(fetcher.name)
                    if since_time:
                        logger.info(f"[{fetcher.name}] 增量抓取（自 {since_time} 起）")
                    elif self.max_age_days > 0:
                        # 首次抓取但配置了 max_age_days：只取最近 N 天
                        since_time = (datetime.now() - timedelta(days=self.max_age_days)).isoformat()
                        logger.info(f"[{fetcher.name}] 首次抓取（限最近 {self.max_age_days} 天，自 {since_time[:10]} 起）")
                    else:
                        logger.info(f"[{fetcher.name}] 首次抓取（全量）")

                # 执行抓取
                items = fetcher.fetch(since_time=since_time)

                # 如果指定了结束时间，按 publish_date 过滤
                if end_time:
                    before_count = len(items)
                    filtered_items = []
                    for item in items:
                        pub_date = getattr(item, "publish_date", None)
                        if pub_date:
                            try:
                                dt = datetime.strptime(pub_date, "%Y-%m-%d")
                                # end_time 已经是 date_to + 1天，所以用 < 比较
                                if dt < datetime.fromisoformat(end_time):
                                    filtered_items.append(item)
                            except (ValueError, TypeError):
                                filtered_items.append(item)  # 日期无法解析则保留
                        else:
                            filtered_items.append(item)  # 无日期则保留
                    items = filtered_items
                    if len(items) < before_count:
                        logger.info(f"[{fetcher.name}] 按结束时间过滤: {before_count} → {len(items)} 条")

                # 限制单源最大条目数
                if len(items) > self.max_items_per_source:
                    logger.warning(
                        f"[{fetcher.name}] 条目数 {len(items)} 超过上限 "
                        f"{self.max_items_per_source}，截断"
                    )
                    items = items[:self.max_items_per_source]

                # 轨交内容过滤（丢弃非城市轨道交通相关条目）
                items, filtered_out = filter_rail_transit_items(items)
                if filtered_out:
                    logger.info(
                        f"[{fetcher.name}] 轨交过滤: 保留 {len(items)} 条, "
                        f"丢弃 {len(filtered_out)} 条非轨交内容"
                    )

                # 去重 + 保存
                # 当指定时间范围时，对 items 表中已存在的记录也进行覆盖保存（确保 bid_raw 可以提取到）
                new_count = 0
                for item in items:
                    if not db.is_item_exists(item.item_id):
                        db.save_item(item)
                        if item not in all_new_items:
                            all_new_items.append(item)
                        new_count += 1
                    elif date_from:
                        # 使用时间范围抓取时，即使 item 已存在也重新保存（更新内容）
                        db.save_item(item)
                        if item not in all_new_items:
                            all_new_items.append(item)

                # 收集里程数据（如果抓取器支持）
                if hasattr(fetcher, 'mileage_records') and fetcher.mileage_records:
                    all_mileage.extend(fetcher.mileage_records)
                    logger.info(
                        f"[{fetcher.name}] 里程数据: {len(fetcher.mileage_records)} 条"
                    )

                # 更新抓取状态
                db.update_fetch_time(fetcher.name, len(items), "success")

                logger.info(
                    f"[{fetcher.name}] 抓取 {len(items)} 条，新增 {new_count} 条"
                )

            except Exception as e:
                logger.error(f"[{fetcher.name}] 抓取异常: {e}", exc_info=True)
                db.update_fetch_time(fetcher.name, 0, "failed", str(e))

        # 批量保存里程数据
        if all_mileage:
            saved = db.save_mileage_batch(all_mileage)
            logger.info(f"里程数据批量保存: {saved}/{len(all_mileage)} 条")

        # ---- 新增：独立抓取中标数据，写入 bid_raw 表 ----
        # 从本次抓取的所有 items（包括已存在去重前的）中，筛选中标类条目，
        # 解析结构化字段后写入 bid_raw 表（供"中标动态"模块审核使用）
        try:
            self._extract_to_bid_raw(db, all_new_items)
        except Exception as e:
            logger.error(f"中标数据提取到 bid_raw 异常（不影响主流程）: {e}", exc_info=True)

        logger.info(f"全部源抓取完成，共新增 {len(all_new_items)} 条未发送条目")
        return all_new_items

    def _extract_to_bid_raw(self, db: Database, new_items: List[BidItem]):
        """从抓取结果中提取中标类数据，写入 bid_raw 表

        流程：
        1. 从 new_items 中筛选出中标类条目（category='中标' 或标题含中标关键词）
        2. 解析标题/描述，提取：项目名称、中标单位、金额、城市、分类等
        3. 去重后写入 bid_raw 表

        分类映射使用数据库 bid_categories 表中可配置的关键词。
        """
        if not new_items:
            return

        # 中标关键词
        BID_KEYWORDS = [
            "中标", "成交结果", "结果公示", "中标候选人",
            "中标结果", "成交公示",
        ]
        EXCLUDE_KEYWORDS = [
            "招标计划", "变更公告", "澄清", "更正", "补充公告", "终止公告",
        ]

        # 从数据库获取可配置的分类→关键词映射
        CATEGORY_KEYWORDS = db.get_bid_category_keywords_map()

        # 金额提取正则
        AMOUNT_PATTERNS = [
            re.compile(r"中标金额[：:]\s*([\d,]+\.?\d*)\s*万", re.IGNORECASE),
            re.compile(r"中标价[：:]\s*([\d,]+\.?\d*)\s*万", re.IGNORECASE),
            re.compile(r"金额[：:]\s*([\d,]+\.?\d*)\s*万", re.IGNORECASE),
            re.compile(r"([\d,]+\.?\d*)\s*万元", re.IGNORECASE),
            re.compile(r"￥\s*([\d,]+\.?\d*)", re.IGNORECASE),
            re.compile(r"¥\s*([\d,]+\.?\d*)", re.IGNORECASE),
        ]

        # 中标单位提取正则
        WINNER_PATTERNS = [
            re.compile(r"中标单位[：:]\s*(.+?)(?:[；;，,。]|$)", re.IGNORECASE),
            re.compile(r"中标人[：:]\s*(.+?)(?:[；;，,。]|$)", re.IGNORECASE),
            re.compile(r"供应商[：:]\s*(.+?)(?:[；;，,。]|$)", re.IGNORECASE),
            re.compile(r"第一中标候选人[：:]\s*(.+?)(?:[；;，,。]|$)", re.IGNORECASE),
        ]

        # 常见城市列表
        COMMON_CITIES = [
            "北京", "上海", "广州", "深圳", "成都", "武汉", "南京", "重庆",
            "杭州", "天津", "苏州", "西安", "郑州", "长沙", "沈阳", "青岛",
            "大连", "宁波", "合肥", "昆明", "南宁", "厦门", "无锡", "贵阳",
            "南昌", "福州", "济南", "兰州", "长春", "哈尔滨", "石家庄",
            "太原", "东莞", "佛山", "常州", "徐州", "温州", "绍兴", "芜湖",
            "洛阳", "嘉兴", "呼和浩特", "乌鲁木齐",
        ]

        # 1. 筛选中标类条目
        candidates = []
        for item in new_items:
            title = getattr(item, "title", "") or ""
            # 排除非中标类
            if any(kw in title for kw in EXCLUDE_KEYWORDS):
                continue
            # 通过 category 或标题关键词判断
            if getattr(item, "category", "") == "中标":
                candidates.append(item)
            elif any(kw in title for kw in BID_KEYWORDS):
                candidates.append(item)

        if not candidates:
            return

        logger.info(f"中标数据提取: {len(candidates)} 条候选条目")

        # 2. 逐条解析为结构化记录
        raw_records = []
        now = datetime.now().isoformat()

        for item in candidates:
            try:
                title = getattr(item, "title", "") or ""
                desc = getattr(item, "description", "") or ""
                text = f"{title} {desc}"
                source = getattr(item, "source", "") or ""

                # 映射分类
                category = self._map_bid_category(title, desc, CATEGORY_KEYWORDS)

                # 提取项目名称
                project_name = self._extract_bid_project_name(title)

                # 提取中标单位
                winner = ""
                for pattern in WINNER_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        winner = match.group(1).strip()
                        winner = re.sub(r"[；;，,。]$", "", winner)
                        if len(winner) > 50:
                            winner = winner[:50]
                        break

                # 提取金额
                bid_amount = None
                for pattern in AMOUNT_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        try:
                            amount_str = match.group(1).replace(",", "")
                            amount = float(amount_str)
                            if amount > 0:
                                bid_amount = amount
                                break
                        except (ValueError, TypeError):
                            continue

                # 提取城市
                city = ""
                for c in COMMON_CITIES:
                    if c in text:
                        city = c
                        break

                # 提取日期
                bid_date = getattr(item, "publish_date", None) or ""

                # 生成 record_id
                record_id_str = f"{project_name}{bid_date}".encode("utf-8")
                record_id = hashlib.md5(record_id_str).hexdigest()

                record = BidRecord(
                    record_id=record_id,
                    project_name=project_name,
                    category=category,
                    winner=winner,
                    city=city,
                    bid_date=bid_date,
                    bid_amount=bid_amount,
                    project_overview=desc[:500] if desc else "",
                    bid_link=getattr(item, "url", "") or "",
                    data_source=source,
                    created_at=now,
                    updated_at=now,
                )
                raw_records.append(record)
            except Exception as e:
                logger.debug(f"解析中标条目异常: {e}")
                continue

        if not raw_records:
            return

        # 3. 去重（同一 record_id 只保留一条）
        seen_ids = set()
        unique_records = []
        for r in raw_records:
            if r.record_id not in seen_ids:
                seen_ids.add(r.record_id)
                unique_records.append(r)

        # 4. 批量写入 bid_raw 表
        result = db.save_bid_raw_batch(unique_records)
        if result["inserted"] > 0 or result["updated"] > 0:
            logger.info(
                f"中标数据写入 bid_raw: 共 {len(unique_records)} 条 "
                f"(新增 {result['inserted']}, 更新 {result['updated']})"
            )

    def _map_bid_category(self, title: str, desc: str,
                          category_keywords: Dict) -> str:
        """根据标题+描述关键词映射到工程分类

        使用从数据库 bid_categories 表加载的可配置分类关键词映射。
        如果未匹配到任何分类，则返回"其他"。
        """
        text = f"{title} {desc}"
        for cat, keywords in category_keywords.items():
            for kw in keywords:
                if kw in text:
                    return cat
        # 通用后备匹配规则（仅当数据库配置中无匹配时使用）
        if "监控" in text and "监控系统" not in [k for kw in category_keywords.values() for k in kw]:
            return "综合监控系统(ISCS)"
        if "施工" in text or "土建" in text:
            return "施工总包"
        return "其他"

    def _extract_bid_project_name(self, title: str) -> str:
        """从标题中提取项目名称"""
        if not title:
            return ""
        cleaned = re.sub(r"^\[.{1,8}\]\s*", "", title)
        cleaned = re.sub(r"^【.{1,20}】\s*", "", cleaned)
        cleaned = re.sub(r"^（.{1,20}）\s*", "", cleaned)
        cleaned = re.sub(r"[\s\-—]+(中标|成交|结果).*$", "", cleaned)
        if len(cleaned) > 80:
            cleaned = cleaned[:80]
        return cleaned.strip()
