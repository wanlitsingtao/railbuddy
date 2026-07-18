"""配置管理模块 - 加载、验证 YAML 配置文件"""

import os
from typing import Any, Dict, List
import yaml


class ConfigError(Exception):
    """配置错误"""
    pass


class Config:
    """配置管理器

    负责加载 YAML 配置文件，提供类型安全的访问方法
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self):
        """加载配置文件"""
        if not os.path.exists(self.config_path):
            raise ConfigError(f"配置文件不存在: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

        self._validate()

    def _validate(self):
        """校验必填配置项"""
        errors = []

        if not self._data.get("email"):
            errors.append("缺少 email 配置段")
        else:
            email = self._data["email"]
            for field in ["smtp_server", "sender", "password", "receiver"]:
                if not email.get(field):
                    errors.append(f"email.{field} 不能为空")

        if not self._data.get("schedule"):
            errors.append("缺少 schedule 配置段")
        else:
            times = self._data["schedule"].get("times")
            if not times or not isinstance(times, list):
                errors.append("schedule.times 必须是非空列表")

        sources = self._data.get("sources", [])
        wechat_sources = self._data.get("wechat_sources", [])
        weibo_sources = self._data.get("weibo_sources", [])
        if not sources and not wechat_sources and not weibo_sources:
            errors.append("至少需要配置一个数据源 (sources / wechat_sources / weibo_sources)")

        if errors:
            raise ConfigError("配置校验失败:\n  - " + "\n  - ".join(errors))

    # ---- 属性访问 ----

    @property
    def sources(self) -> List[Dict]:
        """网站数据源列表（仅返回 enabled 的）"""
        return [s for s in self._data.get("sources", []) if s.get("enabled", True)]

    @property
    def wechat_sources(self) -> List[Dict]:
        """公众号数据源列表（仅返回 enabled 的）"""
        return [s for s in self._data.get("wechat_sources", []) if s.get("enabled", True)]

    @property
    def weibo_sources(self) -> List[Dict]:
        """微博数据源列表（仅返回 enabled 的）"""
        return [s for s in self._data.get("weibo_sources", []) if s.get("enabled", True)]

    @property
    def raw_sources(self) -> List[Dict]:
        """全部网站数据源（含禁用的），用于 Web UI 展示"""
        return self._data.get("sources", [])

    @property
    def raw_wechat_sources(self) -> List[Dict]:
        """全部公众号数据源（含禁用的），用于 Web UI 展示"""
        return self._data.get("wechat_sources", [])

    @property
    def raw_weibo_sources(self) -> List[Dict]:
        """全部微博数据源（含禁用的），用于 Web UI 展示"""
        return self._data.get("weibo_sources", [])

    @property
    def raw_data(self) -> Dict[str, Any]:
        """完整配置字典（用于 Web UI 读取）"""
        return self._data

    def save_to_file(self, path: str = None):
        """将当前配置写回 YAML 文件

        Args:
            path: 目标路径，默认写回原始路径
        """
        target = path or self.config_path
        with open(target, "w", encoding="utf-8") as f:
            yaml.dump(
                self._data, f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                indent=2
            )

    @property
    def email_config(self) -> Dict:
        return self._data.get("email", {})

    @property
    def schedule_config(self) -> Dict:
        return self._data.get("schedule", {})

    @property
    def dedup_config(self) -> Dict:
        return self._data.get("dedup", {})

    @property
    def logging_config(self) -> Dict:
        return self._data.get("logging", {})

    @property
    def db_path(self) -> str:
        return self.dedup_config.get("db_path", "data/railbuddy.db")

    @property
    def retention_days(self) -> int:
        return self.dedup_config.get("retention_days", 90)

    @property
    def max_items_per_source(self) -> int:
        return self.dedup_config.get("max_items_per_source", 100)

    @property
    def max_age_days(self) -> int:
        """首次抓取时只取最近 N 天的数据，0 表示不限制"""
        return self.dedup_config.get("max_age_days", 0)

    def reload(self):
        """重新加载配置（支持运行时热更新）"""
        self.load()

    # ============ Web UI 辅助方法 ============

    def update_sources(self, sources: List[Dict]):
        """更新网站数据源列表（Web UI 调用）"""
        self._data["sources"] = sources

    def update_wechat_sources(self, sources: List[Dict]):
        """更新公众号数据源列表（Web UI 调用）"""
        self._data["wechat_sources"] = sources

    def update_weibo_sources(self, sources: List[Dict]):
        """更新微博数据源列表（Web UI 调用）"""
        self._data["weibo_sources"] = sources

    def update_email(self, email_config: Dict):
        """更新邮箱配置（Web UI 调用）"""
        self._data["email"] = email_config

    def update_schedule(self, schedule_config: Dict):
        """更新调度配置（Web UI 调用）"""
        self._data["schedule"] = schedule_config

    def update_dedup(self, dedup_config: Dict):
        """更新去重配置（Web UI 调用）"""
        self._data["dedup"] = dedup_config
