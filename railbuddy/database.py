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

from .models import BidItem, TransitMileage, BidRecord
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

                -- 项目中标记录表：通信/ISCS/信号/安防/PPP 中标数据
                CREATE TABLE IF NOT EXISTS bid_records (
                    record_id           TEXT PRIMARY KEY,      -- MD5(project_name + bid_date)
                    province            TEXT DEFAULT '',
                    city                TEXT DEFAULT '',
                    category            TEXT DEFAULT '',        -- 通信/ISCS/信号/安防/PPP/BOT等
                    winner              TEXT DEFAULT '',
                    consortium          TEXT DEFAULT '',
                    project_name        TEXT NOT NULL,
                    project_overview    TEXT DEFAULT '',
                    bid_scope           TEXT DEFAULT '',
                    subsystems          TEXT DEFAULT '',
                    bid_threshold       TEXT DEFAULT '',
                    bidder              TEXT DEFAULT '',
                    funding_source      TEXT DEFAULT '',
                    evaluation_method   TEXT DEFAULT '',
                    total_stations      INTEGER,
                    underground_stations INTEGER,
                    elevated_stations   INTEGER,
                    ground_stations     INTEGER,
                    opened_stations     INTEGER,
                    line_type           TEXT DEFAULT '',
                    length_km           REAL,
                    goa_level           TEXT DEFAULT '',
                    system_mode         TEXT DEFAULT '',
                    is_opened           TEXT DEFAULT '',
                    opening_date        TEXT,
                    bid_date            TEXT,
                    bid_amount          REAL,
                    control_price       REAL,
                    bid_link            TEXT DEFAULT '',
                    tender_link         TEXT DEFAULT '',
                    design_unit         TEXT DEFAULT '',
                    platform_software_pis TEXT DEFAULT '',
                    notes               TEXT DEFAULT '',
                    data_source         TEXT DEFAULT 'excel_import',
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_bid_category ON bid_records(category);
                CREATE INDEX IF NOT EXISTS idx_bid_city ON bid_records(city);
                CREATE INDEX IF NOT EXISTS idx_bid_winner ON bid_records(winner);
                CREATE INDEX IF NOT EXISTS idx_bid_date ON bid_records(bid_date);
                CREATE INDEX IF NOT EXISTS idx_bid_province ON bid_records(province);

                -- 中标原始数据表：结构同 bid_records，用于存放抓取到的未审核中标数据
                CREATE TABLE IF NOT EXISTS bid_raw (
                    record_id           TEXT PRIMARY KEY,      -- MD5(project_name + bid_date)
                    province            TEXT DEFAULT '',
                    city                TEXT DEFAULT '',
                    category            TEXT DEFAULT '',
                    winner              TEXT DEFAULT '',
                    consortium          TEXT DEFAULT '',
                    project_name        TEXT NOT NULL,
                    project_overview    TEXT DEFAULT '',
                    bid_scope           TEXT DEFAULT '',
                    subsystems          TEXT DEFAULT '',
                    bid_threshold       TEXT DEFAULT '',
                    bidder              TEXT DEFAULT '',
                    funding_source      TEXT DEFAULT '',
                    evaluation_method   TEXT DEFAULT '',
                    total_stations      INTEGER,
                    underground_stations INTEGER,
                    elevated_stations   INTEGER,
                    ground_stations     INTEGER,
                    opened_stations     INTEGER,
                    line_type           TEXT DEFAULT '',
                    length_km           REAL,
                    goa_level           TEXT DEFAULT '',
                    system_mode         TEXT DEFAULT '',
                    is_opened           TEXT DEFAULT '',
                    opening_date        TEXT,
                    bid_date            TEXT,
                    bid_amount          REAL,
                    control_price       REAL,
                    bid_link            TEXT DEFAULT '',
                    tender_link         TEXT DEFAULT '',
                    design_unit         TEXT DEFAULT '',
                    platform_software_pis TEXT DEFAULT '',
                    notes               TEXT DEFAULT '',
                    data_source         TEXT DEFAULT 'auto_fetch',
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_bid_raw_category ON bid_raw(category);
                CREATE INDEX IF NOT EXISTS idx_bid_raw_city ON bid_raw(city);
                CREATE INDEX IF NOT EXISTS idx_bid_raw_winner ON bid_raw(winner);
                CREATE INDEX IF NOT EXISTS idx_bid_raw_date ON bid_raw(bid_date);
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
            # 中标记录统计
            bid_total = conn.execute("SELECT COUNT(*) FROM bid_records").fetchone()[0]
            bid_categories = conn.execute(
                "SELECT COUNT(DISTINCT category) FROM bid_records WHERE category IS NOT NULL AND category != ''"
            ).fetchone()[0]
            bid_cities = conn.execute(
                "SELECT COUNT(DISTINCT city) FROM bid_records WHERE city IS NOT NULL AND city != ''"
            ).fetchone()[0]
            return {
                "total_items": total,
                "sent_items": sent,
                "unsent_items": new,
                "tracked_sources": sources,
                "mileage_records": mileage_total,
                "mileage_latest_month": mileage_latest or "",
                "mileage_cities": mileage_cities,
                "bid_records": bid_total,
                "bid_categories": bid_categories,
                "bid_cities": bid_cities,
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

    # ============ 项目中标记录 CRUD ============

    BID_RECORD_FIELDS = [
        "record_id", "province", "city", "category", "winner", "consortium",
        "project_name", "project_overview", "bid_scope", "subsystems",
        "bid_threshold", "bidder", "funding_source", "evaluation_method",
        "total_stations", "underground_stations", "elevated_stations",
        "ground_stations", "opened_stations", "line_type", "length_km",
        "goa_level", "system_mode", "is_opened", "opening_date", "bid_date",
        "bid_amount", "control_price", "bid_link", "tender_link",
        "design_unit", "platform_software_pis", "notes", "data_source",
        "created_at", "updated_at"
    ]

    def save_bid_record(self, record: BidRecord) -> bool:
        """保存中标记录（INSERT OR REPLACE，以 record_id 去重）

        Returns:
            True 表示保存成功（新增或更新），False 表示参数异常
        """
        values = (
            record.record_id, record.province, record.city, record.category,
            record.winner, record.consortium, record.project_name,
            record.project_overview, record.bid_scope, record.subsystems,
            record.bid_threshold, record.bidder, record.funding_source,
            record.evaluation_method, record.total_stations,
            record.underground_stations, record.elevated_stations,
            record.ground_stations, record.opened_stations, record.line_type,
            record.length_km, record.goa_level, record.system_mode,
            record.is_opened, record.opening_date or "", record.bid_date or "",
            record.bid_amount, record.control_price, record.bid_link,
            record.tender_link, record.design_unit,
            record.platform_software_pis, record.notes, record.data_source,
            record.created_at, record.updated_at
        )
        placeholders = ",".join("?" * len(self.BID_RECORD_FIELDS))
        fields_str = ",".join(self.BID_RECORD_FIELDS)
        with self._get_conn() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO bid_records ({fields_str}) VALUES ({placeholders})",
                values
            )
        return True

    def save_bid_records_batch(self, records: List[BidRecord]) -> Dict:
        """批量保存中标记录，返回导入统计

        Returns:
            {"total": N, "inserted": N, "updated": N}
        """
        if not records:
            return {"total": 0, "inserted": 0, "updated": 0}

        inserted = 0
        updated = 0
        with self._get_conn() as conn:
            existing_ids = set(
                row[0] for row in conn.execute("SELECT record_id FROM bid_records").fetchall()
            )
            placeholders = ",".join("?" * len(self.BID_RECORD_FIELDS))
            fields_str = ",".join(self.BID_RECORD_FIELDS)
            for r in records:
                values = (
                    r.record_id, r.province, r.city, r.category,
                    r.winner, r.consortium, r.project_name,
                    r.project_overview, r.bid_scope, r.subsystems,
                    r.bid_threshold, r.bidder, r.funding_source,
                    r.evaluation_method, r.total_stations,
                    r.underground_stations, r.elevated_stations,
                    r.ground_stations, r.opened_stations, r.line_type,
                    r.length_km, r.goa_level, r.system_mode,
                    r.is_opened, r.opening_date or "", r.bid_date or "",
                    r.bid_amount, r.control_price, r.bid_link,
                    r.tender_link, r.design_unit,
                    r.platform_software_pis, r.notes, r.data_source,
                    r.created_at, r.updated_at
                )
                if r.record_id in existing_ids:
                    updated += 1
                else:
                    inserted += 1
                conn.execute(
                    f"INSERT OR REPLACE INTO bid_records ({fields_str}) VALUES ({placeholders})",
                    values
                )
        logger.info(f"中标记录批量保存: total={len(records)}, inserted={inserted}, updated={updated}")
        return {"total": len(records), "inserted": inserted, "updated": updated}

    def get_bid_record(self, record_id: str) -> Optional[Dict]:
        """根据 record_id 获取单条中标记录"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_records WHERE record_id = ?", (record_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_existing_bid_record_ids(self) -> set:
        """获取数据库中所有已存在的 record_id 集合（用于批量去重检查）"""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT record_id FROM bid_records").fetchall()
            return {row[0] for row in rows}

    def update_bid_record(self, record_id: str, fields: Dict) -> bool:
        """更新中标记录的指定字段（全字段编辑，支持修改 project_name/bid_date 后自动重算 record_id）

        Args:
            record_id: 记录唯一ID
            fields: 要更新的字段字典（key=列名, value=新值）

        Returns:
            True 表示更新成功，False 表示记录不存在
        """
        # 防止修改 record_id 自身
        fields.pop("record_id", None)
        if not fields:
            return False

        # 如果修改了 project_name 或 bid_date，需重新生成 record_id
        new_project = fields.get("project_name")
        new_bid_date = fields.get("bid_date")

        # 获取当前记录（用于重新计算 record_id 以及合并更新数据）
        current = self.get_bid_record(record_id)
        if not current:
            return False

        # 如果提交了 project_name 或 bid_date，重新计算 record_id
        if new_project is not None or new_bid_date is not None:
            effective_project = new_project if new_project is not None else current.get("project_name", "")
            effective_date = new_bid_date if new_bid_date is not None else current.get("bid_date", "")
            new_record_id = generate_item_id(
                effective_project or "",
                effective_date or ""
            )
            # 如果新 record_id 与旧的不同，需要删除旧记录并插入新记录
            if new_record_id != record_id:
                fields["record_id"] = new_record_id

        fields["updated_at"] = datetime.now().isoformat()

        # 如果 record_id 改变了，需要先删旧记录再插新记录
        if "record_id" in fields:
            new_id = fields.pop("record_id")
            with self._get_conn() as conn:
                # 检查新 ID 是否已存在
                existing = conn.execute(
                    "SELECT record_id FROM bid_records WHERE record_id = ?", (new_id,)
                ).fetchone()
                if existing:
                    # 新 ID 已存在，则合并更新
                    set_clause = ",".join(f"{k} = ?" for k in fields.keys())
                    params = list(fields.values()) + [new_id]
                    conn.execute(f"UPDATE bid_records SET {set_clause} WHERE record_id = ?", params)
                else:
                    # 新 ID 不存在，删除旧记录，创建新记录
                    conn.execute("DELETE FROM bid_records WHERE record_id = ?", (record_id,))
                    # 将当前记录的字段与新字段合并
                    merged = dict(current)
                    merged.update(fields)
                    merged["record_id"] = new_id
                    cols = ",".join(merged.keys())
                    placeholders = ",".join("?" for _ in merged)
                    conn.execute(f"INSERT INTO bid_records ({cols}) VALUES ({placeholders})", list(merged.values()))
                return True
        else:
            set_clause = ",".join(f"{k} = ?" for k in fields.keys())
            params = list(fields.values()) + [record_id]
            with self._get_conn() as conn:
                result = conn.execute(
                    f"UPDATE bid_records SET {set_clause} WHERE record_id = ?", params
                )
                return result.rowcount > 0

    def delete_bid_record(self, record_id: str) -> bool:
        """删除中标记录

        Returns:
            True 表示删除成功，False 表示记录不存在
        """
        with self._get_conn() as conn:
            result = conn.execute(
                "DELETE FROM bid_records WHERE record_id = ?", (record_id,)
            )
            return result.rowcount > 0

    def get_bid_records_paginated(self, page: int = 1, per_page: int = 20,
                                  categories: List[str] = None, category: str = None,
                                  city: str = None, province: str = None,
                                  winner: str = None, keyword: str = None,
                                  date_from: str = None, date_to: str = None,
                                  is_opened: str = None) -> Dict:
        """分页查询中标记录

        Args:
            categories: 多选分类列表（逗号分隔），与 category 互斥，优先使用 categories
            category: 单选分类（兼容旧版）
            keyword: 模糊查询关键词（搜索项目名称、中标单位、项目概况、备注）
            date_from: 中标时间起始 YYYY-MM-DD（月份级时前端已补齐为 YYYY-MM-01）
            date_to: 中标时间截止 YYYY-MM-DD（月份级时前端已补齐为 YYYY-MM-31）

        Returns:
            {"items": [...], "total": N, "page": P, "per_page": PP, "total_pages": TP}
        """
        conditions = []
        params = []

        # 分类筛选：多选优先，单选兼容
        if categories:
            placeholders = ",".join("?" * len(categories))
            conditions.append(f"category IN ({placeholders})")
            params.extend(categories)
        elif category:
            conditions.append("category = ?")
            params.append(category)
        if city:
            conditions.append("city = ?")
            params.append(city)
        if province:
            conditions.append("province = ?")
            params.append(province)
        if winner:
            conditions.append("winner LIKE ?")
            params.append(f"%{winner}%")
        if is_opened:
            conditions.append("is_opened = ?")
            params.append(is_opened)
        if keyword:
            keywords = [k.strip() for k in keyword.split() if k.strip()]
            if keywords:
                or_parts = []
                for kw in keywords:
                    or_parts.append("(project_name LIKE ? OR winner LIKE ? OR project_overview LIKE ? OR notes LIKE ?)")
                    params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"])
                conditions.append("(" + " OR ".join(or_parts) + ")")
        if date_from:
            conditions.append("bid_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("bid_date <= ?")
            params.append(date_to)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        with self._get_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM bid_records{where_clause}", params
            ).fetchone()[0]
            offset = (page - 1) * per_page
            rows = conn.execute(
                f"SELECT * FROM bid_records{where_clause} ORDER BY bid_date DESC LIMIT ? OFFSET ?",
                params + [per_page, offset]
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if per_page > 0 else 0
            }

    def get_bid_categories(self) -> List[str]:
        """获取所有中标记录的工程分类列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM bid_records WHERE category IS NOT NULL AND category != '' ORDER BY category"
            ).fetchall()
            return [row["category"] for row in rows]

    def get_bid_cities(self) -> List[str]:
        """获取所有中标记录的城市列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT city FROM bid_records WHERE city IS NOT NULL AND city != '' ORDER BY city"
            ).fetchall()
            return [row["city"] for row in rows]

    def get_bid_provinces(self) -> List[str]:
        """获取所有中标记录的省份列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT province FROM bid_records WHERE province IS NOT NULL AND province != '' ORDER BY province"
            ).fetchall()
            return [row["province"] for row in rows]

    # ============ bid_raw 表 CRUD 操作（中标动态/原始数据）============
    # bid_raw 表结构与 bid_records 完全相同，但数据来源为 auto_fetch
    # 用于存放抓取到的未审核中标数据，支持手动审核后提取到 bid_records

    def save_bid_raw(self, record: BidRecord) -> bool:
        """保存中标原始记录到 bid_raw 表"""
        values = (
            record.record_id, record.province, record.city, record.category,
            record.winner, record.consortium, record.project_name,
            record.project_overview, record.bid_scope, record.subsystems,
            record.bid_threshold, record.bidder, record.funding_source,
            record.evaluation_method, record.total_stations,
            record.underground_stations, record.elevated_stations,
            record.ground_stations, record.opened_stations, record.line_type,
            record.length_km, record.goa_level, record.system_mode,
            record.is_opened, record.opening_date or "", record.bid_date or "",
            record.bid_amount, record.control_price, record.bid_link,
            record.tender_link, record.design_unit,
            record.platform_software_pis, record.notes, record.data_source,
            record.created_at, record.updated_at
        )
        placeholders = ",".join("?" * len(self.BID_RECORD_FIELDS))
        fields_str = ",".join(self.BID_RECORD_FIELDS)
        with self._get_conn() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO bid_raw ({fields_str}) VALUES ({placeholders})",
                values
            )
        return True

    def save_bid_raw_batch(self, records: List[BidRecord]) -> Dict:
        """批量保存中标原始记录到 bid_raw 表"""
        if not records:
            return {"total": 0, "inserted": 0, "updated": 0}

        inserted = 0
        updated = 0
        with self._get_conn() as conn:
            existing_ids = set(
                row[0] for row in conn.execute("SELECT record_id FROM bid_raw").fetchall()
            )
            placeholders = ",".join("?" * len(self.BID_RECORD_FIELDS))
            fields_str = ",".join(self.BID_RECORD_FIELDS)
            for r in records:
                values = (
                    r.record_id, r.province, r.city, r.category,
                    r.winner, r.consortium, r.project_name,
                    r.project_overview, r.bid_scope, r.subsystems,
                    r.bid_threshold, r.bidder, r.funding_source,
                    r.evaluation_method, r.total_stations,
                    r.underground_stations, r.elevated_stations,
                    r.ground_stations, r.opened_stations, r.line_type,
                    r.length_km, r.goa_level, r.system_mode,
                    r.is_opened, r.opening_date or "", r.bid_date or "",
                    r.bid_amount, r.control_price, r.bid_link,
                    r.tender_link, r.design_unit,
                    r.platform_software_pis, r.notes, r.data_source,
                    r.created_at, r.updated_at
                )
                if r.record_id in existing_ids:
                    updated += 1
                else:
                    inserted += 1
                conn.execute(
                    f"INSERT OR REPLACE INTO bid_raw ({fields_str}) VALUES ({placeholders})",
                    values
                )
        logger.info(f"中标原始记录批量保存: total={len(records)}, inserted={inserted}, updated={updated}")
        return {"total": len(records), "inserted": inserted, "updated": updated}

    def get_bid_raw(self, record_id: str) -> Optional[Dict]:
        """根据 record_id 获取单条中标原始记录"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_raw WHERE record_id = ?", (record_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_bid_raw(self, record_id: str, fields: Dict) -> bool:
        """更新中标原始记录"""
        fields.pop("record_id", None)
        if not fields:
            return False

        new_project = fields.get("project_name")
        new_bid_date = fields.get("bid_date")

        current = self.get_bid_raw(record_id)
        if not current:
            return False

        if new_project is not None or new_bid_date is not None:
            effective_project = new_project if new_project is not None else current.get("project_name", "")
            effective_date = new_bid_date if new_bid_date is not None else current.get("bid_date", "")
            new_record_id = generate_item_id(
                effective_project or "",
                effective_date or ""
            )
            if new_record_id != record_id:
                fields["record_id"] = new_record_id

        fields["updated_at"] = datetime.now().isoformat()

        if "record_id" in fields:
            new_id = fields.pop("record_id")
            with self._get_conn() as conn:
                existing = conn.execute(
                    "SELECT record_id FROM bid_raw WHERE record_id = ?", (new_id,)
                ).fetchone()
                if existing:
                    set_clause = ",".join(f"{k} = ?" for k in fields.keys())
                    params = list(fields.values()) + [new_id]
                    conn.execute(f"UPDATE bid_raw SET {set_clause} WHERE record_id = ?", params)
                else:
                    conn.execute("DELETE FROM bid_raw WHERE record_id = ?", (record_id,))
                    merged = dict(current)
                    merged.update(fields)
                    merged["record_id"] = new_id
                    cols = ",".join(merged.keys())
                    placeholders = ",".join("?" for _ in merged)
                    conn.execute(f"INSERT INTO bid_raw ({cols}) VALUES ({placeholders})", list(merged.values()))
                return True
        else:
            set_clause = ",".join(f"{k} = ?" for k in fields.keys())
            params = list(fields.values()) + [record_id]
            with self._get_conn() as conn:
                result = conn.execute(
                    f"UPDATE bid_raw SET {set_clause} WHERE record_id = ?", params
                )
                return result.rowcount > 0

    def delete_bid_raw(self, record_id: str) -> bool:
        """删除中标原始记录"""
        with self._get_conn() as conn:
            result = conn.execute(
                "DELETE FROM bid_raw WHERE record_id = ?", (record_id,)
            )
            return result.rowcount > 0

    def get_bid_raw_paginated(self, page: int = 1, per_page: int = 20,
                              categories: List[str] = None, category: str = None,
                              city: str = None, province: str = None,
                              winner: str = None, keyword: str = None,
                              date_from: str = None, date_to: str = None,
                              is_opened: str = None) -> Dict:
        """分页查询中标原始记录（bid_raw）"""
        conditions = []
        params = []

        if categories:
            placeholders = ",".join("?" * len(categories))
            conditions.append(f"category IN ({placeholders})")
            params.extend(categories)
        elif category:
            conditions.append("category = ?")
            params.append(category)
        if city:
            conditions.append("city = ?")
            params.append(city)
        if province:
            conditions.append("province = ?")
            params.append(province)
        if winner:
            conditions.append("winner LIKE ?")
            params.append(f"%{winner}%")
        if is_opened:
            conditions.append("is_opened = ?")
            params.append(is_opened)
        if keyword:
            keywords = [k.strip() for k in keyword.split() if k.strip()]
            if keywords:
                or_parts = []
                for kw in keywords:
                    or_parts.append("(project_name LIKE ? OR winner LIKE ? OR project_overview LIKE ? OR notes LIKE ?)")
                    params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"])
                conditions.append("(" + " OR ".join(or_parts) + ")")
        if date_from:
            conditions.append("bid_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("bid_date <= ?")
            params.append(date_to)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        with self._get_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM bid_raw{where_clause}", params
            ).fetchone()[0]
            offset = (page - 1) * per_page
            rows = conn.execute(
                f"SELECT * FROM bid_raw{where_clause} ORDER BY bid_date DESC LIMIT ? OFFSET ?",
                params + [per_page, offset]
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if per_page > 0 else 0
            }

    def get_bid_raw_categories(self) -> List[str]:
        """获取所有中标原始记录的分类列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM bid_raw WHERE category IS NOT NULL AND category != '' ORDER BY category"
            ).fetchall()
            return [row["category"] for row in rows]

    def get_bid_raw_cities(self) -> List[str]:
        """获取所有中标原始记录的城市列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT city FROM bid_raw WHERE city IS NOT NULL AND city != '' ORDER BY city"
            ).fetchall()
            return [row["city"] for row in rows]

    def get_bid_raw_provinces(self) -> List[str]:
        """获取所有中标原始记录的省份列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT province FROM bid_raw WHERE province IS NOT NULL AND province != '' ORDER BY province"
            ).fetchall()
            return [row["province"] for row in rows]

    def get_bid_raw_stats(self) -> Dict:
        """获取中标原始记录统计概要（金额单位：亿元）"""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM bid_raw").fetchone()[0]
            categories = conn.execute(
                "SELECT category, COUNT(*) as count FROM bid_raw WHERE category != '' GROUP BY category ORDER BY count DESC"
            ).fetchall()
            cities = conn.execute(
                "SELECT COUNT(DISTINCT city) FROM bid_raw WHERE city != ''"
            ).fetchone()[0]
            total_amount = conn.execute(
                "SELECT SUM(bid_amount) FROM bid_raw WHERE bid_amount IS NOT NULL AND bid_amount > 0"
            ).fetchone()[0]
            year_dist = conn.execute(
                "SELECT substr(bid_date, 1, 4) as year, COUNT(*) as count FROM bid_raw WHERE bid_date IS NOT NULL AND bid_date != '' GROUP BY year ORDER BY year"
            ).fetchall()
            # 数据库存储单位为万元，合计转换为亿元显示
            total_amount_yi = (total_amount / 10000) if total_amount else 0
            return {
                "total": total,
                "categories": [dict(r) for r in categories],
                "cities": cities,
                "total_amount": round(total_amount_yi, 2) if total_amount else 0,
                "year_distribution": [dict(r) for r in year_dist],
            }

    def transfer_bid_raw_to_records(self, record_ids: List[str]) -> Dict:
        """将选中的 bid_raw 记录提取到 bid_records 表
        Args:
            record_ids: 要提取的 record_id 列表
        Returns:
            {"transferred": N, "skipped": N, "errors": [...]}
        """
        transferred = 0
        skipped = 0
        errors = []
        with self._get_conn() as conn:
            for rid in record_ids:
                try:
                    row = conn.execute("SELECT * FROM bid_raw WHERE record_id = ?", (rid,)).fetchone()
                    if not row:
                        skipped += 1
                        continue
                    record = dict(row)
                    # 检查 bid_records 是否已存在
                    existing = conn.execute(
                        "SELECT record_id FROM bid_records WHERE record_id = ?", (rid,)
                    ).fetchone()
                    if existing:
                        # 已存在，更新
                        record["updated_at"] = datetime.now().isoformat()
                        set_clause = ",".join(f"{k} = ?" for k in record.keys())
                        conn.execute(
                            f"UPDATE bid_records SET {set_clause} WHERE record_id = ?",
                            list(record.values()) + [rid]
                        )
                        transferred += 1
                    else:
                        # 不存在，插入
                        cols = ",".join(record.keys())
                        placeholders = ",".join("?" for _ in record)
                        conn.execute(
                            f"INSERT INTO bid_records ({cols}) VALUES ({placeholders})",
                            list(record.values())
                        )
                        transferred += 1
                except Exception as e:
                    errors.append(f"{rid}: {str(e)}")
        logger.info(f"提取中标记录: transferred={transferred}, skipped={skipped}, errors={len(errors)}")
        return {"transferred": transferred, "skipped": skipped, "errors": errors}

    def get_bid_stats(self) -> Dict:
        """获取中标记录统计概要（金额单位：亿元）"""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM bid_records").fetchone()[0]
            categories = conn.execute(
                "SELECT category, COUNT(*) as count FROM bid_records WHERE category != '' GROUP BY category ORDER BY count DESC"
            ).fetchall()
            cities = conn.execute(
                "SELECT COUNT(DISTINCT city) FROM bid_records WHERE city != ''"
            ).fetchone()[0]
            total_amount = conn.execute(
                "SELECT SUM(bid_amount) FROM bid_records WHERE bid_amount IS NOT NULL AND bid_amount > 0"
            ).fetchone()[0]
            # 年度分布
            year_dist = conn.execute(
                "SELECT substr(bid_date, 1, 4) as year, COUNT(*) as count FROM bid_records WHERE bid_date IS NOT NULL AND bid_date != '' GROUP BY year ORDER BY year"
            ).fetchall()
            # 数据库存储单位为万元，合计转换为亿元显示
            total_amount_yi = (total_amount / 10000) if total_amount else 0
            return {
                "total": total,
                "categories": [dict(r) for r in categories],
                "cities": cities,
                "total_amount": round(total_amount_yi, 2) if total_amount else 0,
                "year_distribution": [dict(r) for r in year_dist],
            }

    def get_bid_stats_filtered(self, categories: List[str] = None, category: str = None,
                               city: str = None, province: str = None,
                               keyword: str = None, date_from: str = None,
                               date_to: str = None) -> Dict:
        """获取中标记录统计概要（根据筛选条件动态计算）

        Returns:
            {"total_amount": float} - 当前筛选条件下的中标总金额
        """
        conditions = []
        params = []

        if categories:
            placeholders = ",".join("?" * len(categories))
            conditions.append(f"category IN ({placeholders})")
            params.extend(categories)
        elif category:
            conditions.append("category = ?")
            params.append(category)
        if city:
            conditions.append("city = ?")
            params.append(city)
        if province:
            conditions.append("province = ?")
            params.append(province)
        if keyword:
            keywords = [k.strip() for k in keyword.split() if k.strip()]
            if keywords:
                or_parts = []
                for kw in keywords:
                    or_parts.append("(project_name LIKE ? OR winner LIKE ? OR project_overview LIKE ? OR notes LIKE ?)")
                    params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"])
                conditions.append("(" + " OR ".join(or_parts) + ")")
        if date_from:
            conditions.append("bid_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("bid_date <= ?")
            params.append(date_to)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        with self._get_conn() as conn:
            total_amount = conn.execute(
                f"SELECT SUM(bid_amount) FROM bid_records{where_clause} WHERE bid_amount IS NOT NULL AND bid_amount > 0".replace(
                    " WHERE bid_amount", " AND bid_amount" if conditions else " WHERE bid_amount"
                ),
                params
            ).fetchone()[0]

            # 更简洁的方式：先构建完整条件（包括金额条件）
            amount_condition = "bid_amount IS NOT NULL AND bid_amount > 0"
            full_where = ""
            if conditions:
                full_where = " WHERE " + " AND ".join(conditions) + f" AND {amount_condition}"
            else:
                full_where = f" WHERE {amount_condition}"

            total_amount = conn.execute(
                f"SELECT SUM(bid_amount) FROM bid_records{full_where}", params
            ).fetchone()[0]

            # 数据库存储单位为万元，合计转换为亿元显示
            total_amount_yi = (total_amount / 10000) if total_amount else 0
            return {
                "total_amount": round(total_amount_yi, 2) if total_amount else 0,
            }
