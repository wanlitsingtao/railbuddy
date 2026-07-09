"""RailBuddy Web 管理服务器

基于 Flask 的轻量级 Web 管理界面，提供：
- 仪表盘：系统状态总览
- 数据源管理：网站/公众号的增删改查
- 邮箱配置：SMTP 设置和测试
- 调度配置：定时任务管理
- 抓取记录：分页浏览、搜索过滤
- 发送日志：历史发送记录
- 手动操作：立即抓取、测试邮件
"""

import os
import logging
import threading
import yaml
from datetime import datetime
from typing import Optional

from flask import Flask, request, jsonify, send_from_directory, render_template

from ..config import Config, ConfigError
from ..database import Database
from ..fetchers.registry import FetcherManager
from ..mailer import MailSender
from ..models import BidItem

logger = logging.getLogger(__name__)


class WebServer:
    """RailBuddy Web 管理服务器"""

    def __init__(self, config_path: str, host: str = "0.0.0.0", port: int = 5210):
        self.config_path = os.path.abspath(config_path)
        self.base_dir = os.path.dirname(self.config_path)
        self.host = host
        self.port = port

        self.app = Flask(
            __name__,
            template_folder=os.path.join(os.path.dirname(__file__), "templates"),
            static_folder=os.path.join(os.path.dirname(__file__), "static"),
        )
        self._register_routes()

    def _resolve_path(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(self.base_dir, path)

    def _load_config(self) -> Config:
        """每次请求时重新加载配置，确保拿到最新值"""
        return Config(self.config_path)

    def _get_db(self, config: Config) -> Database:
        db_path = self._resolve_path(config.db_path)
        return Database(db_path, config.retention_days)

    def _register_routes(self):
        """注册所有路由"""
        app = self.app

        # ---- 页面路由 ----
        @app.route("/")
        def index():
            return render_template("index.html")

        # ---- 仪表盘 ----
        @app.route("/api/dashboard")
        def api_dashboard():
            try:
                config = self._load_config()
                db = self._get_db(config)
                stats = db.get_stats()
                source_states = db.get_all_source_states()
                last_send = db.get_last_send_log()
                send_logs = db.get_send_logs(10)

                # 配置摘要
                enabled_sources = len(config.sources)
                enabled_wechat = len(config.wechat_sources)
                total_sources = len(config.raw_sources) + len(config.raw_wechat_sources)
                schedule_times = config.schedule_config.get("times", [])

                return jsonify({
                    "success": True,
                    "data": {
                        "stats": stats,
                        "source_states": source_states,
                        "last_send": last_send,
                        "send_logs": send_logs,
                        "config_summary": {
                            "total_sources": total_sources,
                            "enabled_sources": enabled_sources + enabled_wechat,
                            "disabled_sources": total_sources - enabled_sources - enabled_wechat,
                            "schedule_times": schedule_times,
                            "timezone": config.schedule_config.get("timezone", "Asia/Shanghai"),
                            "email_configured": bool(config.email_config.get("sender") and config.email_config.get("password")),
                            "receiver": config.email_config.get("receiver", ""),
                        }
                    }
                })
            except Exception as e:
                logger.error(f"Dashboard API error: {e}", exc_info=True)
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 网站数据源 CRUD ----
        @app.route("/api/sources", methods=["GET"])
        def api_list_sources():
            try:
                config = self._load_config()
                return jsonify({
                    "success": True,
                    "data": config.raw_sources
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/sources", methods=["POST"])
        def api_add_source():
            try:
                source = request.json
                if not source or not source.get("name") or not source.get("url"):
                    return jsonify({"success": False, "error": "name 和 url 不能为空"}), 400

                config = self._load_config()
                sources = config.raw_sources
                # 检查重名
                for s in sources:
                    if s["name"] == source["name"]:
                        return jsonify({"success": False, "error": f"数据源 '{source['name']}' 已存在"}), 400

                # 确保必要字段
                source.setdefault("type", "website")
                source.setdefault("enabled", True)
                source.setdefault("keywords", ["招标", "采购", "中标", "成交", "公告"])
                source.setdefault("fetch_detail", False)
                source.setdefault("timeout", 15)
                source.setdefault("request_interval", 2)

                sources.append(source)
                config.update_sources(sources)
                config.save_to_file()

                return jsonify({"success": True, "message": f"数据源 '{source['name']}' 已添加"})
            except Exception as e:
                logger.error(f"Add source error: {e}", exc_info=True)
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/sources/<int:index>", methods=["PUT"])
        def api_update_source(index):
            try:
                config = self._load_config()
                sources = config.raw_sources
                if index < 0 or index >= len(sources):
                    return jsonify({"success": False, "error": "索引超出范围"}), 400

                source = request.json
                if not source or not source.get("name") or not source.get("url"):
                    return jsonify({"success": False, "error": "name 和 url 不能为空"}), 400

                # 检查重名（排除自身）
                for i, s in enumerate(sources):
                    if i != index and s["name"] == source["name"]:
                        return jsonify({"success": False, "error": f"数据源 '{source['name']}' 已存在"}), 400

                sources[index] = source
                config.update_sources(sources)
                config.save_to_file()

                return jsonify({"success": True, "message": f"数据源 '{source['name']}' 已更新"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/sources/<int:index>", methods=["DELETE"])
        def api_delete_source(index):
            try:
                config = self._load_config()
                sources = config.raw_sources
                if index < 0 or index >= len(sources):
                    return jsonify({"success": False, "error": "索引超出范围"}), 400

                name = sources[index].get("name", "")
                sources.pop(index)
                config.update_sources(sources)
                config.save_to_file()

                return jsonify({"success": True, "message": f"数据源 '{name}' 已删除"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 公众号数据源 CRUD ----
        @app.route("/api/wechat-sources", methods=["GET"])
        def api_list_wechat():
            try:
                config = self._load_config()
                return jsonify({
                    "success": True,
                    "data": config.raw_wechat_sources
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/wechat-sources", methods=["POST"])
        def api_add_wechat():
            try:
                source = request.json
                if not source or not source.get("name"):
                    return jsonify({"success": False, "error": "name 不能为空"}), 400

                config = self._load_config()
                sources = config.raw_wechat_sources
                for s in sources:
                    if s["name"] == source["name"]:
                        return jsonify({"success": False, "error": f"公众号 '{source['name']}' 已存在"}), 400

                source.setdefault("type", "wechat")
                source.setdefault("enabled", False)
                source.setdefault("fetch_method", "sogou")
                source.setdefault("keywords", ["招标", "采购", "中标"])
                source.setdefault("timeout", 15)
                source.setdefault("request_interval", 3)

                sources.append(source)
                config.update_wechat_sources(sources)
                config.save_to_file()

                return jsonify({"success": True, "message": f"公众号 '{source['name']}' 已添加"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/wechat-sources/<int:index>", methods=["PUT"])
        def api_update_wechat(index):
            try:
                config = self._load_config()
                sources = config.raw_wechat_sources
                if index < 0 or index >= len(sources):
                    return jsonify({"success": False, "error": "索引超出范围"}), 400

                source = request.json
                if not source or not source.get("name"):
                    return jsonify({"success": False, "error": "name 不能为空"}), 400

                for i, s in enumerate(sources):
                    if i != index and s["name"] == source["name"]:
                        return jsonify({"success": False, "error": f"公众号 '{source['name']}' 已存在"}), 400

                sources[index] = source
                config.update_wechat_sources(sources)
                config.save_to_file()

                return jsonify({"success": True, "message": f"公众号 '{source['name']}' 已更新"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/wechat-sources/<int:index>", methods=["DELETE"])
        def api_delete_wechat(index):
            try:
                config = self._load_config()
                sources = config.raw_wechat_sources
                if index < 0 or index >= len(sources):
                    return jsonify({"success": False, "error": "索引超出范围"}), 400

                name = sources[index].get("name", "")
                sources.pop(index)
                config.update_wechat_sources(sources)
                config.save_to_file()

                return jsonify({"success": True, "message": f"公众号 '{name}' 已删除"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 邮箱配置 ----
        @app.route("/api/email", methods=["GET"])
        def api_get_email():
            try:
                config = self._load_config()
                email_cfg = dict(config.email_config)
                # 脱敏：不返回密码
                if email_cfg.get("password"):
                    email_cfg["password"] = "******"
                    email_cfg["password_set"] = True
                else:
                    email_cfg["password_set"] = False
                return jsonify({"success": True, "data": email_cfg})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/email", methods=["PUT"])
        def api_update_email():
            try:
                config = self._load_config()
                email_cfg = request.json

                # 如果密码是占位符，保留原密码
                if email_cfg.get("password") == "******" or not email_cfg.get("password"):
                    old_config = config.email_config
                    email_cfg["password"] = old_config.get("password", "")

                config.update_email(email_cfg)
                config.save_to_file()

                return jsonify({"success": True, "message": "邮箱配置已更新"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 调度配置 ----
        @app.route("/api/schedule", methods=["GET"])
        def api_get_schedule():
            try:
                config = self._load_config()
                return jsonify({"success": True, "data": config.schedule_config})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/schedule", methods=["PUT"])
        def api_update_schedule():
            try:
                config = self._load_config()
                sched_cfg = request.json
                if not sched_cfg.get("times"):
                    return jsonify({"success": False, "error": "times 不能为空"}), 400

                config.update_schedule(sched_cfg)
                config.save_to_file()

                return jsonify({"success": True, "message": "调度配置已更新"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 去重配置 ----
        @app.route("/api/dedup", methods=["GET"])
        def api_get_dedup():
            try:
                config = self._load_config()
                return jsonify({"success": True, "data": config.dedup_config})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/api/dedup", methods=["PUT"])
        def api_update_dedup():
            try:
                config = self._load_config()
                dedup_cfg = request.json
                config.update_dedup(dedup_cfg)
                config.save_to_file()
                return jsonify({"success": True, "message": "去重配置已更新"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 抓取记录 ----
        @app.route("/api/items")
        def api_items():
            try:
                config = self._load_config()
                db = self._get_db(config)

                page = int(request.args.get("page", 1))
                per_page = int(request.args.get("per_page", 20))
                source = request.args.get("source") or None
                status = request.args.get("status") or None
                keyword = request.args.get("keyword") or None

                result = db.get_items_paginated(
                    page=page, per_page=per_page,
                    source=source, status=status, keyword=keyword
                )
                sources_list = db.get_all_sources()

                return jsonify({
                    "success": True,
                    "data": result,
                    "sources": sources_list
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 发送日志 ----
        @app.route("/api/send-logs")
        def api_send_logs():
            try:
                config = self._load_config()
                db = self._get_db(config)
                limit = int(request.args.get("limit", 20))
                logs = db.get_send_logs(limit)
                return jsonify({"success": True, "data": logs})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 数据源状态 ----
        @app.route("/api/source-states")
        def api_source_states():
            try:
                config = self._load_config()
                db = self._get_db(config)
                states = db.get_all_source_states()
                return jsonify({"success": True, "data": states})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 手动操作：立即抓取 ----
        @app.route("/api/fetch-now", methods=["POST"])
        def api_fetch_now():
            try:
                config = self._load_config()
                db = self._get_db(config)
                fetcher_mgr = FetcherManager(
                    sources_config=config.sources,
                    wechat_sources_config=config.wechat_sources,
                    max_items_per_source=config.max_items_per_source
                )
                new_items = fetcher_mgr.fetch_all(db)

                # 如果有新条目，立即发送邮件
                sent_count = 0
                send_status = "skipped"
                if new_items:
                    mailer = MailSender(config.email_config)
                    success = mailer.send(new_items)
                    if success:
                        item_ids = [item.item_id for item in new_items]
                        db.mark_items_sent(item_ids)
                        sources_involved = set(item.source for item in new_items)
                        for sn in sources_involved:
                            db.update_send_time(sn)
                        sent_count = len(new_items)
                        send_status = "success"
                        db.log_send(sent_count, config.email_config.get("receiver", ""), "success")
                    else:
                        send_status = "failed"
                        db.log_send(len(new_items), config.email_config.get("receiver", ""), "failed", "SMTP 发送失败")
                else:
                    # 检查是否有遗留未发送的
                    pending = db.get_unsent_items()
                    if pending:
                        mailer = MailSender(config.email_config)
                        success = mailer.send(pending)
                        if success:
                            item_ids = [item.item_id for item in pending]
                            db.mark_items_sent(item_ids)
                            sent_count = len(pending)
                            send_status = "success_retry"
                            db.log_send(sent_count, config.email_config.get("receiver", ""), "success")

                return jsonify({
                    "success": True,
                    "data": {
                        "new_items": len(new_items),
                        "sent": sent_count,
                        "send_status": send_status
                    }
                })
            except Exception as e:
                logger.error(f"Fetch now error: {e}", exc_info=True)
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- 手动操作：测试邮件 ----
        @app.route("/api/test-email", methods=["POST"])
        def api_test_email():
            try:
                config = self._load_config()
                mailer = MailSender(config.email_config)
                test_item = BidItem(
                    title="[测试] RailBuddy 邮件配置验证",
                    url="https://example.com",
                    source="RailBuddy 测试",
                    publish_date=datetime.now().strftime("%Y-%m-%d"),
                    description="如果您收到了这封邮件，说明邮箱配置正确。",
                    category="测试"
                )
                success = mailer.send([test_item])
                if success:
                    return jsonify({"success": True, "message": "测试邮件已发送，请检查收件箱"})
                else:
                    return jsonify({"success": False, "error": "邮件发送失败，请检查 SMTP 配置"}), 500
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

    def run(self, debug: bool = False):
        """启动 Web 服务器"""
        logger.info(f"RailBuddy Web 管理界面启动: http://localhost:{self.port}")
        self.app.run(
            host=self.host,
            port=self.port,
            debug=debug,
            use_reloader=False
        )
