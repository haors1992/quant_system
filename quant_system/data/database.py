"""
数据库模块
==========
使用 SQLite 持久化存储：持仓、交易记录、每日快照、组合状态
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Optional

# 默认数据库路径（相对于项目根目录）
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "quant.db",
)


class Database:
    """SQLite 数据库管理"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """建表"""
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                initial_capital REAL NOT NULL,
                cash REAL NOT NULL,
                peak_value REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                buy_date TEXT NOT NULL,
                buy_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                amount REAL NOT NULL,
                current_price REAL NOT NULL,
                highest_price REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                amount REAL NOT NULL,
                commission REAL NOT NULL DEFAULT 0,
                stamp_tax REAL NOT NULL DEFAULT 0,
                reason TEXT DEFAULT ''
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_snapshots (
                snapshot_date TEXT PRIMARY KEY,
                total_value REAL NOT NULL,
                cash REAL NOT NULL,
                position_count INTEGER NOT NULL,
                drawdown_pct REAL NOT NULL,
                profit_pct REAL NOT NULL,
                daily_profit_pct REAL NOT NULL DEFAULT 0,
                initial_capital REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_date
            ON trades(trade_date)
        """)

        self.conn.commit()

    # ===== 组合状态 =====

    def save_portfolio_state(self, initial_capital: float,
                             cash: float, peak_value: float):
        """保存组合状态（覆盖更新）"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO portfolio_state
                (id, initial_capital, cash, peak_value, updated_at)
            VALUES (1, ?, ?, ?, ?)
        """, (initial_capital, cash, peak_value,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self.conn.commit()

    def load_portfolio_state(self) -> Optional[dict]:
        """加载组合状态"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM portfolio_state WHERE id = 1")
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    # ===== 持仓 =====

    def save_position(self, code: str, name: str, buy_date: str,
                      buy_price: float, quantity: int, amount: float,
                      current_price: float, highest_price: float):
        """保存/更新持仓"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO positions
                (code, name, buy_date, buy_price, quantity, amount,
                 current_price, highest_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, name, buy_date, buy_price, quantity, amount,
              current_price, highest_price))
        self.conn.commit()

    def load_positions(self) -> list[dict]:
        """加载所有持仓"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM positions")
        return [dict(row) for row in cursor.fetchall()]

    def delete_position(self, code: str):
        """删除持仓"""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM positions WHERE code = ?", (code,))
        self.conn.commit()

    # ===== 交易记录 =====

    def save_trade(self, trade_date: str, code: str, name: str,
                   action: str, price: float, quantity: int,
                   amount: float, commission: float = 0,
                   stamp_tax: float = 0, reason: str = ""):
        """记录一笔交易"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO trades
                (trade_date, code, name, action, price, quantity,
                 amount, commission, stamp_tax, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade_date, code, name, action, price, quantity,
              amount, commission, stamp_tax, reason))
        self.conn.commit()

    def load_trades(self, date: Optional[str] = None) -> list[dict]:
        """加载交易记录，可按日期筛选"""
        cursor = self.conn.cursor()
        if date:
            cursor.execute(
                "SELECT * FROM trades WHERE trade_date = ? ORDER BY id",
                (date,),
            )
        else:
            cursor.execute("SELECT * FROM trades ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]

    # ===== 每日快照 =====

    def save_snapshot(self, snapshot_date: str, total_value: float,
                      cash: float, position_count: int,
                      drawdown_pct: float, profit_pct: float,
                      daily_profit_pct: float, initial_capital: float):
        """保存每日快照"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO daily_snapshots
                (snapshot_date, total_value, cash, position_count,
                 drawdown_pct, profit_pct, daily_profit_pct, initial_capital)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (snapshot_date, total_value, cash, position_count,
              drawdown_pct, profit_pct, daily_profit_pct, initial_capital))
        self.conn.commit()

    def load_snapshots(self, limit: int = 0) -> list[dict]:
        """加载每日快照，limit=0 表示全部"""
        cursor = self.conn.cursor()
        if limit > 0:
            cursor.execute(
                "SELECT * FROM daily_snapshots ORDER BY snapshot_date DESC LIMIT ?",
                (limit,),
            )
        else:
            cursor.execute(
                "SELECT * FROM daily_snapshots ORDER BY snapshot_date"
            )
        return [dict(row) for row in cursor.fetchall()]

    def get_last_snapshot(self) -> Optional[dict]:
        """获取最近一次快照"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def has_snapshot(self, date: str) -> bool:
        """某日是否已有快照"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM daily_snapshots WHERE snapshot_date = ?",
            (date,),
        )
        return cursor.fetchone() is not None

    # ===== 工具方法 =====

    def get_trade_count(self, date: Optional[str] = None) -> int:
        """获取交易笔数"""
        cursor = self.conn.cursor()
        if date:
            cursor.execute(
                "SELECT COUNT(*) FROM trades WHERE trade_date = ?",
                (date,),
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM trades")
        return cursor.fetchone()[0]

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
