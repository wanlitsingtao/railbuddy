"""抓取器注册与管理器 - 统一管理所有数据源抓取器"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta

from .base import BaseFetcher
from .website import WebsiteFetcher
from .wechat import WechatFetcher
from .api import ApiFetcher
from .wikipedia import WikipediaFetcher
from .mot import MOTFetcher
from ..models import BidItem, TransitMileage
from ..database import Database

logger = logging.getLogger(__name__)

# 抓取器类型注册表
FETCHER_TYPES = {
    "website": WebsiteFetcher,
    "wechat": WechatFetcher,
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

    def fetch_all(self, db: Database) -> List[BidItem]:
        """执行全量抓取

        工作流程：
        1. 遍历每个抓取器
        2. 从数据库获取该源的上次抓取时间（用于增量抓取）
        3. 执行抓取
        4. 去重：过滤掉数据库中已存在的条目
        5. 保存新条目到数据库
        6. 收集里程数据（如果抓取器支持）
        7. 更新抓取状态

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

                # 限制单源最大条目数
                if len(items) > self.max_items_per_source:
                    logger.warning(
                        f"[{fetcher.name}] 条目数 {len(items)} 超过上限 "
                        f"{self.max_items_per_source}，截断"
                    )
                    items = items[:self.max_items_per_source]

                # 去重 + 保存
                new_count = 0
                for item in items:
                    if not db.is_item_exists(item.item_id):
                        db.save_item(item)
                        all_new_items.append(item)
                        new_count += 1

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

        logger.info(f"全部源抓取完成，共新增 {len(all_new_items)} 条未发送条目")
        return all_new_items
