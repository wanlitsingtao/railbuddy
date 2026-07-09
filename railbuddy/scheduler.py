"""定时调度器 - 基于 APScheduler 实现定时抓取任务"""

import logging
from typing import List, Callable
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

logger = logging.getLogger(__name__)


class TaskScheduler:
    """定时任务调度器

    职责：
    1. 管理定时任务的注册和调度
    2. 每天在指定时间点触发抓取任务
    3. 处理任务异常和漏执行
    """

    def __init__(self, timezone: str = "Asia/Shanghai"):
        self.scheduler = BlockingScheduler(timezone=timezone)
        self._setup_listeners()

    def _setup_listeners(self):
        """注册事件监听器"""
        def on_error(event):
            if event.exception:
                logger.error(
                    f"定时任务异常: {event.job_id}, 错误: {event.exception}",
                    exc_info=True
                )
            if event.code == EVENT_JOB_MISSED:
                logger.warning(f"定时任务漏执行: {event.job_id}")

        self.scheduler.add_listener(on_error, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    def add_cron_job(self, func: Callable, job_id: str, hour: int, minute: int):
        """添加定时任务

        Args:
            func: 要执行的函数
            job_id: 任务 ID
            hour: 小时（24小时制）
            minute: 分钟
        """
        self.scheduler.add_job(
            func,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600,  # 漏执行宽限时间（1小时）
            coalesce=True,            # 合并多次漏执行为一次
        )
        logger.info(f"  定时任务已注册: {job_id} -> 每天 {hour:02d}:{minute:02d}")

    def add_interval_job(self, func: Callable, job_id: str, seconds: int):
        """添加间隔任务（用于测试）"""
        self.scheduler.add_job(
            func,
            trigger="interval",
            seconds=seconds,
            id=job_id,
            replace_existing=True,
        )
        logger.info(f"  间隔任务已注册: {job_id} -> 每 {seconds} 秒")

    def start(self):
        """启动调度器（阻塞运行）"""
        logger.info("定时调度器启动")
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("调度器收到退出信号，正在关闭...")
            self.scheduler.shutdown(wait=True)

    def shutdown(self):
        """关闭调度器"""
        self.scheduler.shutdown(wait=True)
