import aiosqlite
from pathlib import Path
from datetime import datetime, date
from typing import List, Optional, Dict, Any


class Database:
    def __init__(self, db_path: str = "./database.db"):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.db_path)
        await self._create_tables()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def _create_tables(self):
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT UNIQUE,
                session_file TEXT,
                json_file TEXT,
                proxy TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_used DATETIME,
                reactions_today INTEGER DEFAULT 0,
                reactions_date DATE,
                is_active BOOLEAN DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS reactions_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                post_link TEXT,
                channel_id INTEGER,
                message_id INTEGER,
                reaction TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts(id),
                UNIQUE(account_id, channel_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS channel_subscriptions (
                account_id INTEGER,
                channel_id INTEGER,
                is_subscribed BOOLEAN,
                checked_at DATETIME,
                PRIMARY KEY (account_id, channel_id),
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            CREATE INDEX IF NOT EXISTS idx_reactions_post ON reactions_log(channel_id, message_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_active ON accounts(is_active);
        """)
        await self._conn.commit()

    async def add_account(self, phone: str, session_file: str, json_file: str, proxy: Optional[str] = None) -> int:
        cursor = await self._conn.execute(
            """INSERT OR REPLACE INTO accounts (phone, session_file, json_file, proxy, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (phone, session_file, json_file, proxy, datetime.now())
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_account(self, phone: str) -> Optional[Dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT * FROM accounts WHERE phone = ?", (phone,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(zip([d[0] for d in cursor.description], row))
        return None

    async def get_all_accounts(self, active_only: bool = True) -> List[Dict[str, Any]]:
        query = "SELECT * FROM accounts"
        if active_only:
            query += " WHERE is_active = 1"
        cursor = await self._conn.execute(query)
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def get_available_accounts(
        self,
        channel_id: int,
        message_id: int,
        count: int,
        max_reactions_per_day: int
    ) -> List[Dict[str, Any]]:
        today = date.today().isoformat()

        await self._conn.execute(
            """UPDATE accounts SET reactions_today = 0
               WHERE reactions_date != ? OR reactions_date IS NULL""",
            (today,)
        )
        await self._conn.commit()

        cursor = await self._conn.execute(
            """SELECT a.* FROM accounts a
               WHERE a.is_active = 1
               AND (a.reactions_today < ? OR a.reactions_date != ?)
               AND a.id NOT IN (
                   SELECT account_id FROM reactions_log
                   WHERE channel_id = ? AND message_id = ?
               )
               ORDER BY a.reactions_today ASC, a.last_used ASC
               LIMIT ?""",
            (max_reactions_per_day, today, channel_id, message_id, count)
        )
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def log_reaction(
        self,
        account_id: int,
        post_link: str,
        channel_id: int,
        message_id: int,
        reaction: str
    ):
        today = date.today().isoformat()

        await self._conn.execute(
            """INSERT OR IGNORE INTO reactions_log
               (account_id, post_link, channel_id, message_id, reaction)
               VALUES (?, ?, ?, ?, ?)""",
            (account_id, post_link, channel_id, message_id, reaction)
        )

        await self._conn.execute(
            """UPDATE accounts
               SET reactions_today = reactions_today + 1,
                   reactions_date = ?,
                   last_used = ?
               WHERE id = ?""",
            (today, datetime.now(), account_id)
        )
        await self._conn.commit()

    async def has_reacted(self, account_id: int, channel_id: int, message_id: int) -> bool:
        cursor = await self._conn.execute(
            """SELECT 1 FROM reactions_log
               WHERE account_id = ? AND channel_id = ? AND message_id = ?""",
            (account_id, channel_id, message_id)
        )
        return await cursor.fetchone() is not None

    async def set_account_active(self, account_id: int, active: bool):
        await self._conn.execute(
            "UPDATE accounts SET is_active = ? WHERE id = ?",
            (active, account_id)
        )
        await self._conn.commit()

    async def update_subscription(self, account_id: int, channel_id: int, is_subscribed: bool):
        await self._conn.execute(
            """INSERT OR REPLACE INTO channel_subscriptions
               (account_id, channel_id, is_subscribed, checked_at)
               VALUES (?, ?, ?, ?)""",
            (account_id, channel_id, is_subscribed, datetime.now())
        )
        await self._conn.commit()

    async def get_subscription(self, account_id: int, channel_id: int) -> Optional[bool]:
        cursor = await self._conn.execute(
            """SELECT is_subscribed FROM channel_subscriptions
               WHERE account_id = ? AND channel_id = ?""",
            (account_id, channel_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_reaction_count(self, channel_id: int, message_id: int) -> int:
        cursor = await self._conn.execute(
            """SELECT COUNT(*) FROM reactions_log
               WHERE channel_id = ? AND message_id = ?""",
            (channel_id, message_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def clear_reactions(self, channel_id: int, message_id: int):
        await self._conn.execute(
            "DELETE FROM reactions_log WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id)
        )
        await self._conn.commit()
