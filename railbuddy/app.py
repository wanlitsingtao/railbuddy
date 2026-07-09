"""主应用模块 - 核心业务逻辑编排"""

import os
import sys
import time
import signal
import logging
from datetime import datetime
from typing import Optional

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
    2. 编排抓取 → 去重 → 邮件发送的完整流程
    3. 管理定时调度
    4. 处理启动时补偿抓取（空挡修复）
    5. 优雅退出
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
            max_items_per_source=self.config.max_items_per_source
        )

        # 初始化邮件发送器
        self.mailer = MailSender(self.config.email_config)

        # 初始化调度器
        sched_cfg = self.config.schedule_config
        self.scheduler = TaskScheduler(timezone=sched_cfg.get("timezone", "Asia/Shanghai"))
        self.fetch_on_start = sched_cfg.get("fetch_on_start", True)

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

    def run_task(self):
        """执行一次完整的抓取 → 发送任务

        这是定时任务调用的核心方法，包含完整的业务流程：
        1. 执行全量抓取（自动增量 + 去重）
        2. 如果有新条目，发送邮件
        3. 更新发送状态
        4. 清理过期数据
        """
        logger.info("=" * 50)
        logger.info("开始执行定时抓取任务")
        task_start = time.time()

        try:
            # 0. 先获取遗留的未发送条目（上次发送失败留下的）
            pending_items = self.db.get_unsent_items()
            if pending_items:
                logger.info(f"发现 {len(pending_items)} 条遗留未发送条目，将合并发送")

            # 1. 抓取所有数据源
            logger.info(">>> 第1步: 抓取数据源")
            new_items = self.fetcher_manager.fetch_all(self.db)

            # 合并遗留未发送 + 新抓取
            all_items = pending_items + new_items

            if not all_items:
                logger.info("未发现新项目，本次任务结束")
                self._log_task_summary(0, task_start)
                return

            logger.info(f">>> 第2步: 发送邮件 ({len(all_items)} 个项目)")

            # 2. 发送邮件
            success = self.mailer.send(all_items)

            if success:
                # 3. 标记为已发送
                item_ids = [item.item_id for item in all_items]
                self.db.mark_items_sent(item_ids)

                # 更新各源的发送时间
                sources_involved = set(item.source for item in all_items)
                for source_name in sources_involved:
                    self.db.update_send_time(source_name)

                # 记录发送日志
                self.db.log_send(
                    item_count=len(all_items),
                    recipients=self.config.email_config.get("receiver", ""),
                    status="success"
                )
                logger.info(f"邮件发送成功，{len(all_items)} 个项目已标记为已发送")
            else:
                # 发送失败：不标记为已发送，下次会重新尝试
                self.db.log_send(
                    item_count=len(all_items),
                    recipients=self.config.email_config.get("receiver", ""),
                    status="failed",
                    error_msg="SMTP 发送失败"
                )
                logger.warning("邮件发送失败，条目保持未发送状态，下次将重试")

            # 4. 清理过期数据
            self.db.cleanup_expired()

        except Exception as e:
            logger.error(f"任务执行异常: {e}", exc_info=True)

        sent_count = len(all_items) if "all_items" in dir() else 0
        self._log_task_summary(sent_count, task_start)

    def _log_task_summary(self, item_count: int, start_time: float):
        """记录任务摘要"""
        elapsed = time.time() - start_time
        stats = self.db.get_stats()
        logger.info("-" * 50)
        logger.info(f"任务摘要:")
        logger.info(f"  新增项目: {item_count}")
        logger.info(f"  数据库总计: {stats['total_items']} 条 "
                     f"(已发送 {stats['sent_items']}, 待发送 {stats['unsent_items']})")
        logger.info(f"  监控源数: {stats['tracked_sources']}")
        logger.info(f"  耗时: {elapsed:.2f} 秒")
        logger.info("=" * 50)

    def run(self):
        """启动服务

        1. 如果配置了 fetch_on_start，启动时立即执行一次抓取（补偿空挡）
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
        logger.info(f"数据库状态: 总计 {stats['total_items']} 条, "
                     f"已发送 {stats['sent_items']} 条, 待发送 {stats['unsent_items']} 条")

        # 打印上次抓取状态
        source_states = self.db.get_all_source_states()
        if source_states:
            logger.info("各源上次抓取状态:")
            for ss in source_states:
                logger.info(f"  {ss['name']}: 上次抓取 {ss.get('last_fetch_time', '无')}, "
                             f"状态 {ss.get('last_fetch_status', '未知')}, "
                             f"条目 {ss.get('last_fetch_count', 0)}")

        # 启动时补偿抓取
        if self.fetch_on_start:
            logger.info(">>> 启动补偿抓取（检查程序停止期间是否有遗漏）")
            self._compensate_gap()
        else:
            logger.info("fetch_on_start=False，跳过启动补偿抓取")

        # 注册定时任务
        logger.info("注册定时任务:")
        times = self.config.schedule_config.get("times", ["08:00", "12:00"])
        for time_str in times:
            try:
                hour, minute = map(int, time_str.strip().split(":"))
                self.scheduler.add_cron_job(
                    self.run_task,
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

    def _compensate_gap(self):
        """补偿抓取：检查程序停止期间是否有遗漏的条目

        逻辑：
        1. 查询数据库中是否有未发送的条目（之前抓取但未发送的）
        2. 执行一次新的抓取（会自动从上次抓取时间增量抓取）
        3. 合并后如果有未发送条目，立即发送
        """
        # 先检查是否有之前遗留的未发送条目
        pending = self.db.get_unsent_items()
        if pending:
            logger.info(f"发现 {len(pending)} 条遗留未发送条目，将合并发送")

        # 执行增量抓取
        new_items = self.fetcher_manager.fetch_all(self.db)

        # 合并遗留 + 新抓取
        all_items = pending + new_items

        if all_items:
            logger.info(f"补偿抓取: 共 {len(all_items)} 条待发送 "
                         f"(遗留 {len(pending)} + 新增 {len(new_items)})")
            success = self.mailer.send(all_items)
            if success:
                item_ids = [item.item_id for item in all_items]
                self.db.mark_items_sent(item_ids)
                self.db.log_send(
                    item_count=len(all_items),
                    recipients=self.config.email_config.get("receiver", ""),
                    status="success"
                )
                logger.info(f"补偿发送成功: {len(all_items)} 个项目")
            else:
                logger.warning("补偿发送失败，条目将在下次定时任务中重试")
        else:
            logger.info("补偿抓取完成，无新项目")
