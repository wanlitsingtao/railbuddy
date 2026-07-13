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

from .models import BidItem, TransitMileage
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
        # 预迁移：在 executescript 之前检查并添加 system_type 列
        # （因为 executescript 中的 CREATE INDEX 引用了 system_type，旧表如果没有该列会报错）
        try:
            with self._get_conn() as conn:
                table_exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='transit_mileage'"
                ).fetchone()
                if table_exists:
                    columns = [row[1] for row in conn.execute("PRAGMA table_info(transit_mileage)").fetchall()]
                    if "system_type" not in columns:
                        conn.execute("ALTER TABLE transit_mileage ADD COLUMN system_type TEXT DEFAULT '地铁'")
                        logger.info("transit_mileage 表已迁移：新增 system_type 列")
        except Exception as e:
            logger.debug(f"system_type 预迁移检查跳过: {e}")

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
                CREATE INDEX IF NOT EXISTS idx_items_publish_date ON items(publish_date);

                -- 城轨里程表：按线路粒度存储里程数据（月度快照）
                CREATE TABLE IF NOT EXISTS transit_mileage (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    line_id         TEXT NOT NULL,          -- city+line_name 的 MD5
                    city            TEXT NOT NULL,
                    system_name     TEXT DEFAULT '',
                    line_name       TEXT NOT NULL,
                    system_type     TEXT DEFAULT '地铁',     -- 制式：地铁/轻轨/单轨/市域铁路/城际铁路/有轨电车/磁浮
                    length_km       REAL,
                    stations        INTEGER,
                    opening_date    TEXT,
                    status          TEXT DEFAULT 'operational',
                    data_source     TEXT NOT NULL,
                    data_month      TEXT NOT NULL,          -- YYYY-MM
                    fetched_at      TEXT NOT NULL,
                    UNIQUE(line_id, data_month)             -- 同一线路同一月份唯一
                );

                CREATE INDEX IF NOT EXISTS idx_mileage_city ON transit_mileage(city);
                CREATE INDEX IF NOT EXISTS idx_mileage_month ON transit_mileage(data_month);
                CREATE INDEX IF NOT EXISTS idx_mileage_status ON transit_mileage(status);
                CREATE INDEX IF NOT EXISTS idx_mileage_type ON transit_mileage(system_type);
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

    def get_send_logs_paginated(self, page: int = 1, per_page: int = 20) -> Dict:
        """分页获取发送日志

        Returns:
            {"items": [...], "total": N, "page": P, "per_page": PP, "total_pages": TP}
        """
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM send_logs").fetchone()[0]
            offset = (page - 1) * per_page
            rows = conn.execute(
                "SELECT * FROM send_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                (per_page, offset)
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if per_page > 0 else 0
            }

    def get_source_states_paginated(self, page: int = 1, per_page: int = 20) -> Dict:
        """分页获取数据源状态列表

        Returns:
            {"items": [...], "total": N, "page": P, "per_page": PP, "total_pages": TP}
        """
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM source_states").fetchone()[0]
            offset = (page - 1) * per_page
            rows = conn.execute(
                "SELECT * FROM source_states ORDER BY name LIMIT ? OFFSET ?",
                (per_page, offset)
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if per_page > 0 else 0
            }

    def get_items_paginated(self, page: int = 1, per_page: int = 20,
                            source: str = None, status: str = None,
                            category: str = None, keyword: str = None,
                            date_from: str = None, date_to: str = None) -> Dict:
        """分页查询条目

        Args:
            keyword: 模糊查询关键词。支持空格分隔的多关键词（OR 关系），
                     例：'深圳 17' 会匹配标题含'深圳'或'17'的条目
            date_from: 发布日期起始（含），格式 YYYY-MM-DD
            date_to: 发布日期截止（含），格式 YYYY-MM-DD

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
        if category:
            if category == "__uncategorized__":
                conditions.append("(category IS NULL OR category = '')")
            else:
                conditions.append("category = ?")
            params.append(category)
        if keyword:
            # 空格分隔的多个关键词：任一命中即匹配（OR 关系）
            # 例：'深圳 17' -> 标题含'深圳'或'17'
            keywords = [k.strip() for k in keyword.split() if k.strip()]
            if keywords:
                or_parts = []
                for kw in keywords:
                    or_parts.append("(title LIKE ? OR description LIKE ?)")
                    params.extend([f"%{kw}%", f"%{kw}%"])
                conditions.append("(" + " OR ".join(or_parts) + ")")
        if date_from:
            conditions.append("publish_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("publish_date <= ?")
            params.append(date_to)

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

    def get_all_categories(self) -> List[str]:
        """获取所有已存在的类别名称列表（排除空值）"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM items WHERE category IS NOT NULL AND category != '' ORDER BY category"
            ).fetchall()
            return [row["category"] for row in rows]

    def get_all_sources(self) -> List[str]:
        """获取所有已抓取过的来源名称列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source FROM items ORDER BY source"
            ).fetchall()
            return [row["source"] for row in rows]

    def get_items_by_ids(self, item_ids: List[str]) -> List[BidItem]:
        """根据 item_id 列表批量查询条目"""
        if not item_ids:
            return []
        placeholders = ",".join("?" * len(item_ids))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM items WHERE item_id IN ({placeholders}) "
                f"ORDER BY category, publish_date DESC",
                item_ids
            ).fetchall()
            return [self._row_to_item(row) for row in rows]

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
            # 里程统计
            mileage_total = conn.execute("SELECT COUNT(*) FROM transit_mileage").fetchone()[0]
            mileage_latest = conn.execute("SELECT MAX(data_month) FROM transit_mileage").fetchone()[0]
            mileage_cities = conn.execute(
                "SELECT COUNT(DISTINCT city) FROM transit_mileage WHERE city != '全国'"
            ).fetchone()[0]
            return {
                "total_items": total,
                "sent_items": sent,
                "unsent_items": new,
                "tracked_sources": sources,
                "mileage_records": mileage_total,
                "mileage_latest_month": mileage_latest or "",
                "mileage_cities": mileage_cities,
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

    # ============ 城轨里程数据操作 ============

    def save_mileage(self, mileage: TransitMileage):
        """保存里程数据（同一线路同一月份存在则更新）"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO transit_mileage
                    (line_id, city, system_name, line_name, system_type, length_km, stations,
                     opening_date, status, data_source, data_month, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(line_id, data_month) DO UPDATE SET
                    system_name = excluded.system_name,
                    system_type = excluded.system_type,
                    length_km = excluded.length_km,
                    stations = excluded.stations,
                    opening_date = excluded.opening_date,
                    status = excluded.status,
                    data_source = excluded.data_source,
                    fetched_at = excluded.fetched_at
            """, (
                mileage.line_id, mileage.city, mileage.system_name,
                mileage.line_name, mileage.system_type,
                mileage.length_km, mileage.stations,
                mileage.opening_date, mileage.status,
                mileage.data_source, mileage.data_month, mileage.fetched_at
            ))

    def save_mileage_batch(self, mileages: List[TransitMileage]) -> int:
        """批量保存里程数据，返回保存/更新条数"""
        if not mileages:
            return 0
        count = 0
        with self._get_conn() as conn:
            for m in mileages:
                try:
                    conn.execute("""
                        INSERT INTO transit_mileage
                            (line_id, city, system_name, line_name, system_type, length_km, stations,
                             opening_date, status, data_source, data_month, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(line_id, data_month) DO UPDATE SET
                            system_name = excluded.system_name,
                            system_type = excluded.system_type,
                            length_km = excluded.length_km,
                            stations = excluded.stations,
                            opening_date = excluded.opening_date,
                            status = excluded.status,
                            data_source = excluded.data_source,
                            fetched_at = excluded.fetched_at
                    """, (
                        m.line_id, m.city, m.system_name, m.line_name,
                        m.system_type, m.length_km, m.stations,
                        m.opening_date, m.status,
                        m.data_source, m.data_month, m.fetched_at
                    ))
                    count += 1
                except Exception as e:
                    logger.warning(f"保存里程数据失败: {m.city}/{m.line_name}: {e}")
        logger.info(f"里程数据保存完成: {count}/{len(mileages)} 条")
        return count

    def get_latest_mileage_month(self) -> Optional[str]:
        """获取最新的里程数据月份"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(data_month) FROM transit_mileage"
            ).fetchone()
            return row[0] if row and row[0] else None

    def get_mileage_by_month(self, data_month: str) -> List[Dict]:
        """获取指定月份的所有线路里程数据"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM transit_mileage WHERE data_month = ? ORDER BY city, line_name",
                (data_month,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_mileage_cities(self) -> List[str]:
        """获取所有有里程数据的城市列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT city FROM transit_mileage ORDER BY city"
            ).fetchall()
            return [row["city"] for row in rows]

    def get_mileage_months(self) -> List[str]:
        """获取所有有数据的月份列表（降序）"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT data_month FROM transit_mileage ORDER BY data_month DESC"
            ).fetchall()
            return [row["data_month"] for row in rows]

    def get_national_trend(self) -> List[Dict]:
        """全国里程月度变化趋势

        返回每个月的：运营线路总里程、总线路数、覆盖城市数
        排除 city='全国' 的聚合记录和 data_source='seed_history' 的全国合计，避免重复计算
        """
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    data_month,
                    COUNT(DISTINCT city) AS city_count,
                    COUNT(*) AS line_count,
                    SUM(length_km) AS total_km
                FROM transit_mileage
                WHERE status = 'operational'
                  AND city != '全国'
                  AND line_name != ''
                GROUP BY data_month
                ORDER BY data_month ASC
            """).fetchall()
            return [dict(row) for row in rows]

    def get_city_trend(self, city: str = None) -> List[Dict]:
        """城市维度里程变化趋势

        返回每个城市每个月的累计里程
        """
        with self._get_conn() as conn:
            if city:
                rows = conn.execute("""
                    SELECT
                        data_month,
                        city,
                        COUNT(*) AS line_count,
                        SUM(length_km) AS total_km
                    FROM transit_mileage
                    WHERE status = 'operational' AND city = ?
                    GROUP BY data_month, city
                    ORDER BY data_month ASC
                """, (city,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT
                        data_month,
                        city,
                        COUNT(*) AS line_count,
                        SUM(length_km) AS total_km
                    FROM transit_mileage
                    WHERE status = 'operational'
                    GROUP BY data_month, city
                    ORDER BY data_month ASC, total_km DESC
                """).fetchall()
            return [dict(row) for row in rows]

    def get_city_summary(self, data_month: str = None) -> List[Dict]:
        """获取城市维度汇总（某月或最新月）

        返回每个城市的：线路数、总里程、车站数、最早开通日期、制式构成
        优先查找有城市级数据（city != '全国'）的最新月份。
        """
        with self._get_conn() as conn:
            if not data_month:
                # 找到最新的包含城市级数据（非"全国"）的月份
                row = conn.execute("""
                    SELECT MAX(data_month) FROM transit_mileage
                    WHERE city != '全国'
                """).fetchone()
                data_month = row[0] if row else None
            if not data_month:
                return []

            rows = conn.execute("""
                SELECT
                    city,
                    system_name,
                    COUNT(*) AS line_count,
                    SUM(length_km) AS total_km,
                    SUM(stations) AS total_stations,
                    MIN(opening_date) AS first_opening,
                    GROUP_CONCAT(DISTINCT system_type) AS system_types,
                    GROUP_CONCAT(DISTINCT status) AS statuses
                FROM transit_mileage
                WHERE data_month = ? AND city != '全国'
                GROUP BY city, system_name
                ORDER BY total_km DESC
            """, (data_month,)).fetchall()
            return [dict(row) for row in rows]

    def get_system_type_breakdown(self, data_month: str = None) -> List[Dict]:
        """获取按制式分类的里程统计（某月或最新月）

        返回每种制式的：线路数、总里程、城市数
        优先查找有城市级数据（city != '全国'）的最新月份。
        """
        with self._get_conn() as conn:
            if not data_month:
                # 找到最新的包含城市级数据（非"全国"）的月份
                row = conn.execute("""
                    SELECT MAX(data_month) FROM transit_mileage
                    WHERE city != '全国'
                """).fetchone()
                data_month = row[0] if row else None
            if not data_month:
                return []

            rows = conn.execute("""
                SELECT
                    system_type,
                    COUNT(*) AS line_count,
                    SUM(length_km) AS total_km,
                    COUNT(DISTINCT city) AS city_count,
                    SUM(stations) AS total_stations
                FROM transit_mileage
                WHERE data_month = ? AND status = 'operational' AND city != '全国'
                GROUP BY system_type
                ORDER BY total_km DESC
            """, (data_month,)).fetchall()
            return [dict(row) for row in rows]

    def get_national_annual_trend(self) -> List[Dict]:
        """全国年度里程变化趋势

        将月度数据按年聚合，取每年最后一个月的数据作为年末值。
        排除"总计"系统类型以避免重复计算。
        返回每年的：城市数、线路数、总里程、按制式分类里程
        """
        with self._get_conn() as conn:
            # 获取所有年份
            years = conn.execute("""
                SELECT DISTINCT substr(data_month, 1, 4) AS year
                FROM transit_mileage
                WHERE data_month != '' AND city = '全国'
                ORDER BY year ASC
            """).fetchall()

            result = []
            for year_row in years:
                year = year_row["year"]
                # 取该年最大的月份
                max_month = conn.execute("""
                    SELECT MAX(data_month) FROM transit_mileage
                    WHERE substr(data_month, 1, 4) = ? AND city = '全国'
                """, (year,)).fetchone()[0]

                if not max_month:
                    continue

                # 按制式分类统计（从 city='全国' 的记录获取里程数据）
                # 优先使用 MOT 数据源（data_source='mot_monthly'），避免 seed_history 与 MOT 重复
                type_rows = conn.execute("""
                    SELECT
                        system_type,
                        SUM(length_km) AS total_km
                    FROM transit_mileage
                    WHERE data_month = ? AND city = '全国'
                      AND system_type != '总计'
                      AND data_source = (
                          SELECT data_source FROM transit_mileage
                          WHERE data_month = ? AND city = '全国' AND system_type = '地铁'
                          ORDER BY CASE data_source
                              WHEN 'mot_monthly' THEN 1
                              WHEN 'wikipedia' THEN 2
                              ELSE 3
                          END
                          LIMIT 1
                      )
                    GROUP BY system_type
                """, (max_month, max_month)).fetchall()

                # 从线路明细数据（city != '全国'）统计实际城市数和线路数
                # 优先使用当前月份的数据，若不存在则使用最新月份的明细数据按开通年份过滤
                detail_stats = conn.execute("""
                    SELECT
                        system_type,
                        COUNT(DISTINCT city) AS city_count,
                        COUNT(*) AS line_count
                    FROM transit_mileage
                    WHERE data_month = ? AND city != '全国'
                      AND status = 'operational' AND length_km > 0
                    GROUP BY system_type
                """, (max_month,)).fetchall()

                # 如果当前月份没有线路明细数据，使用最新月份数据按开通年份过滤
                if not detail_stats:
                    latest_detail_month = conn.execute("""
                        SELECT MAX(data_month) FROM transit_mileage
                        WHERE city != '全国' AND opening_date IS NOT NULL AND opening_date != ''
                    """).fetchone()[0]
                    if latest_detail_month:
                        year_str = str(year)
                        detail_stats = conn.execute("""
                            SELECT
                                system_type,
                                COUNT(DISTINCT city) AS city_count,
                                COUNT(*) AS line_count
                            FROM transit_mileage
                            WHERE data_month = ? AND city != '全国'
                              AND status = 'operational' AND length_km > 0
                              AND opening_date IS NOT NULL AND opening_date != ''
                              AND substr(opening_date, 1, 4) <= ?
                            GROUP BY system_type
                        """, (latest_detail_month, year_str)).fetchall()

                # 合并制式数据：里程用全国合计，城市数和线路数用明细统计
                type_data = {}
                all_system_types = set()
                for row in type_rows:
                    all_system_types.add(row["system_type"])
                for row in detail_stats:
                    all_system_types.add(row["system_type"])

                for st in all_system_types:
                    type_row = next((r for r in type_rows if r["system_type"] == st), None)
                    detail_row = next((r for r in detail_stats if r["system_type"] == st), None)
                    type_data[st] = {
                        "km": round(type_row["total_km"], 2) if type_row and type_row["total_km"] else 0,
                        "lines": detail_row["line_count"] if detail_row else 0,
                        "cities": detail_row["city_count"] if detail_row else 0
                    }

                total_km = sum(t["km"] for t in type_data.values())
                total_lines = sum(t["lines"] for t in type_data.values())
                total_cities = max((t["cities"] for t in type_data.values()), default=0)

                result.append({
                    "year": int(year),
                    "data_month": max_month,
                    "cities": total_cities,
                    "lines": total_lines,
                    "total_km": round(total_km, 2),
                    "by_type": type_data,
                })

            return result

    def get_city_annual_trend(self) -> List[Dict]:
        """城市维度年度里程变化趋势

        从线路明细数据（含opening_date）计算每个城市每年的累计里程。
        返回每个城市每一年的累计里程和线路数。
        """
        with self._get_conn() as conn:
            # 获取所有有线路明细的城市
            cities = conn.execute("""
                SELECT DISTINCT city FROM transit_mileage
                WHERE city != '全国' AND opening_date IS NOT NULL AND opening_date != ''
                ORDER BY city
            """).fetchall()

            result = []
            current_year = datetime.now().year

            # 获取每个城市的最新月份
            city_latest_months = conn.execute("""
                SELECT city, MAX(data_month) AS max_month FROM transit_mileage
                WHERE city != '全国' GROUP BY city
            """).fetchall()
            city_latest = {row["city"]: row["max_month"] for row in city_latest_months}

            for city_row in cities:
                city = city_row["city"]
                latest = city_latest.get(city, "")
                # 获取该城市所有线路（只取最新月份的数据，避免重复）
                lines = conn.execute("""
                    SELECT line_name, system_type, length_km, opening_date, stations
                    FROM transit_mileage
                    WHERE city = ? AND opening_date IS NOT NULL AND opening_date != ''
                      AND length_km > 0
                      AND data_month = ?
                    GROUP BY line_name
                """, (city, latest)).fetchall()

                if not lines:
                    continue

                # 找到该城市最早开通年份
                first_year = min(
                    int(line["opening_date"][:4]) for line in lines
                    if line["opening_date"] and line["opening_date"][:4].isdigit()
                )

                # 逐年计算累计里程
                for year in range(first_year, current_year + 1):
                    year_end = f"{year}-12-31"
                    cumulative_km = 0
                    cumulative_lines = 0
                    type_breakdown = {}

                    for line in lines:
                        od = line["opening_date"]
                        if od and od <= year_end:
                            km = line["length_km"] or 0
                            cumulative_km += km
                            cumulative_lines += 1
                            st = line["system_type"] or "地铁"
                            if st not in type_breakdown:
                                type_breakdown[st] = 0
                            type_breakdown[st] += km

                    if cumulative_km > 0:
                        result.append({
                            "city": city,
                            "year": year,
                            "cumulative_km": round(cumulative_km, 2),
                            "cumulative_lines": cumulative_lines,
                            "by_type": {
                                k: round(v, 2) for k, v in type_breakdown.items()
                            },
                        })

            return result

    def get_mileage_stats(self) -> Dict:
        """获取里程数据统计概要"""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM transit_mileage").fetchone()[0]
            months = conn.execute(
                "SELECT COUNT(DISTINCT data_month) FROM transit_mileage"
            ).fetchone()[0]
            cities = conn.execute(
                "SELECT COUNT(DISTINCT city) FROM transit_mileage WHERE city != '全国'"
            ).fetchone()[0]
            latest = conn.execute(
                "SELECT MAX(data_month) FROM transit_mileage"
            ).fetchone()[0]

            # 最新月份的运营总里程（排除 city='全国' 的聚合记录，避免重复计算）
            operational_km = 0
            if latest:
                row = conn.execute("""
                    SELECT SUM(length_km) FROM transit_mileage
                    WHERE data_month = ? AND status = 'operational'
                      AND city != '全国'
                """, (latest,)).fetchone()
                operational_km = row[0] if row and row[0] else 0

            return {
                "total_records": total,
                "data_months": months,
                "cities": cities,
                "latest_month": latest or "",
                "operational_km": round(operational_km, 2) if operational_km else 0,
            }
