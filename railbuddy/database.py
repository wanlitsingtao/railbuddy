"""SQLite 状态管理模块

负责：
1. 存储已抓取的条目记录（用于去重和历史查询）
2. 记录每个数据源的抓取/发送时间戳（用于断点续抓）
3. 记录发送日志
4. 过期数据清理
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from contextlib import contextmanager

from .models import BidItem
from .utils.text import generate_item_id

logger = logging.getLogger(__name__)


class Database:
    """SQLite 状态数据库"""

    def __init__(self, db_path: str = "data/railbuddy.db", retention_days: int = 90):
        self.db_path = db_path
        self.retention_days = retention_days

        # 确保目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._init_db()

    @contextmanager
    def _get_conn(self):
        """获取数据库连接（上下文管理器，自动提交/回滚）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # WAL 模式，提升并发性能
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """初始化数据库表结构"""
        with self._get_conn() as conn:
            conn.executescript("""
                -- 条目表：记录所有已抓取的条目（用于去重）
                CREATE TABLE IF NOT EXISTS items (
                    item_id         TEXT PRIMARY KEY,
                    title           TEXT NOT NULL,
                    url             TEXT NOT NULL,
                    source          TEXT NOT NULL,
                    publish_date    TEXT,
                    description     TEXT DEFAULT '',
                    category        TEXT DEFAULT '',
                    fetched_at      TEXT NOT NULL,
                    sent_at         TEXT,
                    status          TEXT DEFAULT 'new'  -- new / sent / failed
                );

                -- 数据源状态表：记录每个源的抓取/发送时间
                CREATE TABLE IF NOT EXISTS source_states (
                    name                TEXT PRIMARY KEY,
                    last_fetch_time     TEXT,
                    last_send_time      TEXT,
                    last_fetch_count    INTEGER DEFAULT 0,
                    last_fetch_status   TEXT DEFAULT 'pending',
                    last_error          TEXT DEFAULT '',
                    updated_at          TEXT
                );

                -- 发送日志表
                CREATE TABLE IF NOT EXISTS send_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at     TEXT NOT NULL,
                    item_count  INTEGER NOT NULL,
                    recipients  TEXT,
                    status      TEXT NOT NULL,  -- success / failed
                    error_msg   TEXT DEFAULT ''
                );

                -- 索引：加速查询
                CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
                CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
                CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);
            """)
        logger.debug(f"数据库初始化完成: {self.db_path}")

    # ============ 条目操作 ============

    def is_item_exists(self, item_id: str) -> bool:
        """检查条目是否已存在（去重判断）"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM items WHERE item_id = ?", (item_id,)
            ).fetchone()
            return row is not None

    def save_item(self, item: BidItem):
        """保存条目到数据库（如果已存在则跳过）"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO items
                    (item_id, title, url, source, publish_date, description,
                     category, fetched_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """, (
                item.item_id, item.title, item.url, item.source,
                item.publish_date or "", item.description,
                item.category, item.fetched_at
            ))

    def get_unsent_items(self) -> List[BidItem]:
        """获取所有未发送的条目"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM items WHERE status = 'new'
                ORDER BY fetched_at ASC
            """).fetchall()
            return [self._row_to_item(row) for row in rows]

    def mark_items_sent(self, item_ids: List[str]):
        """将条目标记为已发送"""
        if not item_ids:
            return
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            for item_id in item_ids:
                conn.execute(
                    "UPDATE items SET status = 'sent', sent_at = ? WHERE item_id = ?",
                    (now, item_id)
                )
        logger.info(f"已标记 {len(item_ids)} 条为已发送")

    def get_sent_item_count(self) -> int:
        """获取已发送条目总数"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM items WHERE status = 'sent'"
            ).fetchone()
            return row[0] if row else 0

    # ============ 数据源状态操作 ============

    def get_last_fetch_time(self, source_name: str) -> Optional[str]:
        """获取某数据源的上次抓取时间"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT last_fetch_time FROM source_states WHERE name = ?",
                (source_name,)
            ).fetchone()
            return row["last_fetch_time"] if row else None

    def update_fetch_time(self, source_name: str, count: int, status: str = "success",
                          error: str = ""):
        """更新数据源的抓取状态"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO source_states (name, last_fetch_time, last_fetch_count,
                                           last_fetch_status, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    last_fetch_time = excluded.last_fetch_time,
                    last_fetch_count = excluded.last_fetch_count,
                    last_fetch_status = excluded.last_fetch_status,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
            """, (source_name, now, count, status, error, now))

    def update_send_time(self, source_name: str):
        """更新数据源的发送时间"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE source_states SET last_send_time = ?, updated_at = ? WHERE name = ?",
                (now, now, source_name)
            )

    def get_all_source_states(self) -> List[Dict]:
        """获取所有数据源的状态"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM source_states ORDER BY name"
            ).fetchall()
            return [dict(row) for row in rows]

    # ============ 发送日志 ============

    def log_send(self, item_count: int, recipients: str, status: str, error_msg: str = ""):
        """记录发送日志"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO send_logs (sent_at, item_count, recipients, status, error_msg)
                VALUES (?, ?, ?, ?, ?)
            """, (now, item_count, recipients, status, error_msg))

    def get_last_send_log(self) -> Optional[Dict]:
        """获取最后一次发送记录"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM send_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def get_send_logs(self, limit: int = 20) -> List[Dict]:
        """获取发送日志列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM send_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_items_paginated(self, page: int = 1, per_page: int = 20,
                            source: str = None, status: str = None,
                            keyword: str = None) -> Dict:
        """分页查询条目

        Returns:
            {"items": [...], "total": N, "page": P, "per_page": PP, "total_pages": TP}
        """
        conditions = []
        params = []
        if source:
            conditions.append("source = ?")
            params.append(source)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if keyword:
            conditions.append("(title LIKE ? OR description LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        with self._get_conn() as conn:
            # 总数
            total = conn.execute(
                f"SELECT COUNT(*) FROM items{where_clause}", params
            ).fetchone()[0]

            # 分页数据
            offset = (page - 1) * per_page
            rows = conn.execute(
                f"SELECT * FROM items{where_clause} ORDER BY fetched_at DESC LIMIT ? OFFSET ?",
                params + [per_page, offset]
            ).fetchall()

            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if per_page > 0 else 0
            }

    def get_all_sources(self) -> List[str]:
        """获取所有已抓取过的来源名称列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source FROM items ORDER BY source"
            ).fetchall()
            return [row["source"] for row in rows]

    # ============ 维护操作 ============

    def cleanup_expired(self):
        """清理过期的已发送记录"""
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).isoformat()
        with self._get_conn() as conn:
            result = conn.execute(
                "DELETE FROM items WHERE status = 'sent' AND fetched_at < ?",
                (cutoff,)
            )
            deleted = result.rowcount
        if deleted > 0:
            logger.info(f"清理过期记录: {deleted} 条")

    def get_stats(self) -> Dict:
        """获取数据库统计信息"""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            sent = conn.execute("SELECT COUNT(*) FROM items WHERE status='sent'").fetchone()[0]
            new = conn.execute("SELECT COUNT(*) FROM items WHERE status='new'").fetchone()[0]
            sources = conn.execute("SELECT COUNT(*) FROM source_states").fetchone()[0]
            return {
                "total_items": total,
                "sent_items": sent,
                "unsent_items": new,
                "tracked_sources": sources,
            }

    def _row_to_item(self, row: sqlite3.Row) -> BidItem:
        """将数据库行转为 BidItem 对象"""
        return BidItem(
            item_id=row["item_id"],
            title=row["title"],
            url=row["url"],
            source=row["source"],
            publish_date=row["publish_date"],
            description=row["description"],
            category=row["category"],
            fetched_at=row["fetched_at"],
        )
