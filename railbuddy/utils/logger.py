"""日志配置模块 - 支持控制台输出和文件轮转"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler


def setup_logging(level: str = "INFO", log_file: str = "logs/railbuddy.log",
                  backup_count: int = 30):
    """配置全局日志系统

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_file: 日志文件路径
        backup_count: 日志文件保留天数
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # 确保日志目录存在
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有 handler，防止重复
    root_logger.handlers.clear()

    # 日志格式
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

    # 文件输出（按天轮转）
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1,
        backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(fmt)
    file_handler.suffix = "%Y-%m-%d.log"
    root_logger.addHandler(file_handler)

    # 降低第三方库的日志级别
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return root_logger
