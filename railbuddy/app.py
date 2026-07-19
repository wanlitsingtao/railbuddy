"""主应用模块 - 核心业务逻辑编排"""

import os
import sys
import time
import signal
import logging
from datetime import datetime
from typing import Optional, List

from .config import Config
from .database import Database
from .fetchers.registry import FetcherManager
from .mailer import MailSender
from .scheduler import TaskScheduler
from .utils.logger import setup_logging

logger = logging.getLogger(__name__)


class RailBuddyApp:
    """RailBuddy 主应用

    职责：
    1. 加载配置和初始化各模块
    2. 管理三种爬虫的顺序执行：
       - 行业信息爬虫（抓取行业新闻、招标信息等 → items表）
       - 里程数据爬虫（抓取里程数据 → mileage_pool表）
       - 中标数据爬虫（抓取中标数据 → bid_raw表）
    3. 数据库备份与清理
    4. 管理定时调度
    5. 处理启动时补偿抓取（空挡修复）
    6. 优雅退出
    """

    def __init__(self, config_path: str = "config.yaml"):
        # 配置文件路径（支持相对路径和绝对路径）
        self.config_path = os.path.abspath(config_path)

        # 加载配置
        self.config = Config(self.config_path)
        # 基准目录：配置文件所在目录，用于解析所有相对路径
        self.base_dir = os.path.dirname(self.config_path)

        # 初始化日志
        log_cfg = self.config.logging_config
        log_file = self._resolve_path(log_cfg.get("file", "logs/railbuddy.log"))
        setup_logging(
            level=log_cfg.get("level", "INFO"),
            log_file=log_file,
            backup_count=log_cfg.get("backup_count", 30)
        )

        logger.info("=" * 60)
        logger.info("RailBuddy 城市轨道交通招标监控服务")
        logger.info(f"版本: 1.0.0")
        logger.info(f"配置文件: {self.config_path}")
        logger.info(f"工作目录: {self.base_dir}")
        logger.info("=" * 60)

        # 初始化数据库
        db_path = self._resolve_path(self.config.db_path)
        self.db = Database(db_path, self.config.retention_days)

        # 初始化抓取器管理器
        self.fetcher_manager = FetcherManager(
            sources_config=self.config.sources,
            wechat_sources_config=self.config.wechat_sources,
            weibo_sources_config=self.config.weibo_sources,
            max_items_per_source=self.config.max_items_per_source,
            max_age_days=self.config.max_age_days
        )

        # 初始化邮件发送器
        self.mailer = MailSender(self.config.email_config)

        # 初始化调度器
        sched_cfg = self.config.schedule_config
        self.scheduler = TaskScheduler(timezone=sched_cfg.get("timezone", "Asia/Shanghai"))
        self.fetch_on_start = sched_cfg.get("fetch_on_start", True)
        self.auto_send = sched_cfg.get("auto_send", True)

        # 退出标志
        self._running = True

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _resolve_path(self, path: str) -> str:
        """将相对路径解析为相对于配置文件所在目录的绝对路径"""
        if os.path.isabs(path):
            return path
        return os.path.join(self.base_dir, path)

    def _signal_handler(self, signum, frame):
        """处理退出信号"""
        sig_name = signal.Signals(signum).name
        logger.info(f"收到信号 {sig_name}，准备优雅退出...")
        self._running = False
        self.scheduler.shutdown()

    def _backup_database(self):
        """执行数据库备份，并清理30天前的备份"""
        logger.info(">>> 数据库备份")
        backup_dir = os.path.join(self.base_dir, "data", "backups")
        backup_file = self.db.backup_database(backup_dir)
        if backup_file:
            logger.info(f"  备份文件: {backup_file}")
        self.db.cleanup_old_backups(backup_dir, keep_days=30)
        logger.info("  旧备份清理完成（保留30天内）")

    def run_crawler_industry(self) -> List:
        """爬虫1：行业信息爬虫 - 抓取行业新闻、招标信息等

        从所有已配置的网站/公众号/微博数据源抓取信息，
        写入 items 表（用于行业信息浏览和邮件发送）。

        Returns:
            新发现的条目列表
        """
        logger.info("=" * 40)
        logger.info("【爬虫1】行业信息爬虫 - 开始")
        start_time = time.time()

        try:
            # 获取遗留的未发送条目
            pending_items = self.db.get_unsent_items()
            if pending_items:
                logger.info(f"  发现 {len(pending_items)} 条遗留未发送条目")

            # 执行增量抓取
            new_items = self.fetcher_manager.fetch_all(self.db)
            all_items = pending_items + new_items

            # 自动发送邮件（如果启用）
            if all_items and self.auto_send:
                logger.info(f"  发送邮件: {len(all_items)} 条")
                success = self.mailer.send(all_items)
                if success:
                    item_ids = [item.item_id for item in all_items]
                    self.db.mark_items_sent(item_ids)
                    sources_involved = set(item.source for item in all_items)
                    for sn in sources_involved:
                        self.db.update_send_time(sn)
                    self.db.log_send(
                        item_count=len(all_items),
                        recipients=self.config.email_config.get("receiver", ""),
                        status="success"
                    )
                    logger.info(f"  邮件发送成功")
                else:
                    self.db.log_send(
                        item_count=len(all_items),
                        recipients=self.config.email_config.get("receiver", ""),
                        status="failed",
                        error_msg="SMTP 发送失败"
                    )

            elapsed = time.time() - start_time
            logger.info(f"【爬虫1】行业信息爬虫完成: {len(new_items)} 新增, {elapsed:.2f}秒")
            return all_items

        except Exception as e:
            logger.error(f"【爬虫1】异常: {e}", exc_info=True)
            return []

    def run_crawler_mileage(self) -> int:
        """爬虫2：里程数据爬虫 - 抓取轨道交通里程数据

        从交通运输部(MOT)、Wikipedia等数据源抓取里程数据，
        写入 mileage_pool 表（里程原始数据池）。

        Returns:
            抓取到的里程记录条数
        """
        logger.info("=" * 40)
        logger.info("【爬虫2】里程数据爬虫 - 开始")
        start_time = time.time()

        try:
            # 筛选出里程相关的抓取器（MOT、Wikipedia等）
            mileage_fetchers = []
            for fetcher in self.fetcher_manager.fetchers:
                if hasattr(fetcher, 'mileage_records'):
                    mileage_fetchers.append(fetcher)

            if not mileage_fetchers:
                logger.info("  未发现里程数据源，跳过")
                return 0

            total_records = 0
            for fetcher in mileage_fetchers:
                if not fetcher.enabled:
                    continue
                try:
                    logger.info(f"  执行: [{fetcher.name}]")
                    items = fetcher.fetch(since_time=None)

                    # 收集里程数据
                    if hasattr(fetcher, 'mileage_records') and fetcher.mileage_records:
                        pool_records = []
                        for m in fetcher.mileage_records:
                            pool_records.append({
                                "city": m.city,
                                "system_name": m.system_name,
                                "line_name": m.line_name,
                                "system_type": m.system_type,
                                "length_km": m.length_km,
                                "stations": m.stations,
                                "opening_date": m.opening_date,
                                "status": "operational",
                                "data_source": m.data_source,
                                "data_month": m.data_month,
                                "raw_data": "",
                                "remark": ""
                            })
                        saved = self.db.save_mileage_pool_batch(pool_records)
                        total_records += saved
                        logger.info(f"  [{fetcher.name}] 里程数据: {saved} 条")

                    # 更新抓取状态
                    self.db.update_fetch_time(fetcher.name, len(items), "success")

                except Exception as e:
                    logger.error(f"  [{fetcher.name}] 里程抓取异常: {e}", exc_info=True)
                    self.db.update_fetch_time(fetcher.name, 0, "failed", str(e))

            elapsed = time.time() - start_time
            logger.info(f"【爬虫2】里程数据爬虫完成: {total_records} 条, {elapsed:.2f}秒")
            return total_records

        except Exception as e:
            logger.error(f"【爬虫2】异常: {e}", exc_info=True)
            return 0

    def run_crawler_bid(self) -> int:
        """爬虫3：中标数据爬虫 - 抓取中标数据

        从所有已配置的数据源抓取信息，
        筛选中标类条目并提取结构化字段，写入 bid_raw 表（中标动态数据）。
        注意：中标记录（bid_records）只能通过手工提取写入。

        Returns:
            写入 bid_raw 的条数
        """
        logger.info("=" * 40)
        logger.info("【爬虫3】中标数据爬虫 - 开始")
        start_time = time.time()

        try:
            # 执行全量抓取，但不限制历史天数
            new_items = self.fetcher_manager.fetch_all(self.db)

            # fetch_all 内部已自动提取中标数据到 bid_raw（_extract_to_bid_raw）
            # 统计 bid_raw 中本次新增的条数
            bid_raw_count = 0
            if new_items:
                # 统计新抓取条目中被识别为中标类的数量
                BID_KEYWORDS = [
                    "中标", "成交结果", "结果公示", "中标候选人",
                    "中标结果", "成交公示",
                ]
                for item in new_items:
                    title = getattr(item, "title", "") or ""
                    category = getattr(item, "category", "") or ""
                    if category == "中标" or any(kw in title for kw in BID_KEYWORDS):
                        bid_raw_count += 1

            elapsed = time.time() - start_time
            logger.info(f"【爬虫3】中标数据爬虫完成: 抓取 {len(new_items)} 条, "
                        f"中标识别 {bid_raw_count} 条, {elapsed:.2f}秒")
            return bid_raw_count

        except Exception as e:
            logger.error(f"【爬虫3】异常: {e}", exc_info=True)
            return 0

    def run_all_crawlers(self):
        """顺序执行所有爬虫任务

        1. 数据库备份
        2. 行业信息爬虫
        3. 里程数据爬虫
        4. 中标数据爬虫
        5. 清理过期数据
        """
        logger.info("=" * 60)
        logger.info("开始执行全量抓取任务（3种爬虫顺序执行）")
        task_start = time.time()

        try:
            # 0. 数据库备份
            self._backup_database()

            # 1. 行业信息爬虫
            self.run_crawler_industry()

            # 2. 里程数据爬虫
            self.run_crawler_mileage()

            # 3. 中标数据爬虫
            self.run_crawler_bid()

            # 4. 清理过期数据
            self.db.cleanup_expired()

        except Exception as e:
            logger.error(f"全量抓取任务异常: {e}", exc_info=True)

        elapsed = time.time() - task_start
        stats = self.db.get_stats()
        logger.info("=" * 60)
        logger.info(f"全量抓取任务完成，耗时 {elapsed:.2f} 秒")
        logger.info(f"  行业信息: {stats['total_items']} 条 (items表)")
        logger.info(f"  里程池: {stats.get('mileage_pool_records', 0)} 条 (mileage_pool表)")
        logger.info(f"  中标动态: {stats.get('bid_raw_total', 0)} 条 (bid_raw表)")
        logger.info(f"  中标记录: {stats['bid_records']} 条 (bid_records表)")
        logger.info("=" * 60)

    def run(self):
        """启动服务

        1. 如果配置了 fetch_on_start，启动时立即执行一次全量抓取
        2. 注册定时任务
        3. 启动调度器（阻塞运行）
        """
        logger.info("服务启动中...")

        # 打印监控源信息
        for fetcher in self.fetcher_manager.fetchers:
            status = "启用" if fetcher.enabled else "禁用"
            logger.info(f"  数据源 [{status}]: {fetcher.name}")

        # 打印数据库统计
        stats = self.db.get_stats()
        logger.info(f"数据库状态: 行业信息 {stats['total_items']} 条, "
                     f"中标记录 {stats['bid_records']} 条, "
                     f"里程池 {stats.get('mileage_pool_records', 0)} 条")

        # 打印上次抓取状态
        source_states = self.db.get_all_source_states()
        if source_states:
            logger.info("各源上次抓取状态:")
            for ss in source_states:
                logger.info(f"  {ss['name']}: 上次抓取 {ss.get('last_fetch_time', '无')}, "
                             f"状态 {ss.get('last_fetch_status', '未知')}, "
                             f"条目 {ss.get('last_fetch_count', 0)}")

        # 启动时全量抓取
        if self.fetch_on_start:
            logger.info(">>> 启动全量抓取（检查程序停止期间是否有遗漏）")
            self.run_all_crawlers()
        else:
            logger.info("fetch_on_start=False，跳过启动抓取")

        # 注册定时任务（使用 run_all_crawlers 代替原来的 run_task）
        logger.info("注册定时任务:")
        times = self.config.schedule_config.get("times", ["08:00", "12:00"])
        for time_str in times:
            try:
                hour, minute = map(int, time_str.strip().split(":"))
                self.scheduler.add_cron_job(
                    self.run_all_crawlers,
                    job_id=f"daily_{hour:02d}_{minute:02d}",
                    hour=hour, minute=minute
                )
            except ValueError:
                logger.warning(f"无效的时间格式: {time_str}，应为 HH:MM")

        logger.info("所有定时任务已注册，服务进入调度模式")
        logger.info("=" * 60)

        # 启动调度器（阻塞）
        self.scheduler.start()
        logger.info("服务已停止")

    # 保留 run_task 兼容旧版调用
    def run_task(self):
        """兼容旧版：执行一次完整的抓取任务（等同于 run_all_crawlers）"""
        self.run_all_crawlers()

    def _compensate_gap(self):
        """兼容旧版：启动补偿抓取"""
        self.run_all_crawlers()
