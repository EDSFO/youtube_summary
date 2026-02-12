import aiosqlite
import os
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "resumos.db")


class Database:
    def __init__(self):
        self.db_path = DB_PATH

    async def init_db(self):
        """Initialize database tables."""
        async with aiosqlite.connect(self.db_path) as db:
            # Channels table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT UNIQUE NOT NULL,
                    name TEXT,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Videos table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT UNIQUE NOT NULL,
                    channel_id TEXT,
                    title TEXT,
                    duration INTEGER,
                    view_count INTEGER,
                    transcript TEXT,
                    published_at DATETIME,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
                )
            """)

            # Summaries table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT UNIQUE NOT NULL,
                    summary TEXT,
                    model_used TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                )
            """)

            # Telegram logs table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS telegram_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    success INTEGER DEFAULT 1
                )
            """)

            await db.commit()
            logger.info("Banco de dados inicializado com sucesso")

    async def is_video_processed(self, video_id: str) -> bool:
        """Check if video already has a summary."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM summaries WHERE video_id = ?",
                (video_id,)
            )
            result = await cursor.fetchone()
            return result is not None

    async def mark_video_processed(
        self,
        video_id: str,
        title: str = "",
        channel_id: str = "",
        channel_title: str = "",
        summary: str = "",
        model_used: str = "",
    ):
        """Store video metadata and summary."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO videos (video_id, channel_id, title)
                VALUES (?, ?, ?)
                """,
                (video_id, channel_id or None, title or None),
            )
            await db.execute(
                """
                UPDATE videos
                SET
                    title = CASE WHEN title IS NULL OR title = '' THEN ? ELSE title END,
                    channel_id = CASE WHEN channel_id IS NULL OR channel_id = '' THEN ? ELSE channel_id END
                WHERE video_id = ?
                """,
                (title or None, channel_id or None, video_id),
            )

            if channel_id and channel_title:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO channels (channel_id, name)
                    VALUES (?, ?)
                    """,
                    (channel_id, channel_title),
                )

            if summary:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO summaries (video_id, summary, model_used)
                    VALUES (?, ?, ?)
                    """,
                    (video_id, summary, model_used or None),
                )

            await db.commit()

    async def get_processed_videos(self, limit: int = 50):
        """Fetch processed videos with summaries."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    v.video_id,
                    v.title,
                    v.channel_id,
                    v.published_at,
                    v.processed_at,
                    s.summary,
                    s.model_used,
                    s.created_at
                FROM videos v
                LEFT JOIN summaries s ON s.video_id = v.video_id
                ORDER BY v.processed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return rows

    async def get_summaries_by_video_ids(self, video_ids: List[str]) -> Dict[str, dict]:
        """Fetch summaries for a list of video IDs."""
        if not video_ids:
            return {}

        placeholders = ",".join("?" for _ in video_ids)
        query = f"""
            SELECT
                s.video_id,
                s.summary,
                s.model_used,
                s.created_at
            FROM summaries s
            WHERE s.video_id IN ({placeholders})
        """

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, tuple(video_ids))
            rows = await cursor.fetchall()

        return {
            row[0]: {
                "summary": row[1],
                "model_used": row[2],
                "created_at": row[3],
            }
            for row in rows
        }

    async def log_telegram_sent(self, video_id: str, success: bool = True):
        """Register telegram send result."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO telegram_logs (video_id, success) VALUES (?, ?)",
                (video_id, 1 if success else 0),
            )
            await db.commit()

    async def get_all_channels(self):
        """Fetch all channels."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM channels")
            rows = await cursor.fetchall()
            return rows

    async def add_channel(self, channel_id: str, name: str):
        """Add a channel."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO channels (channel_id, name) VALUES (?, ?)",
                (channel_id, name),
            )
            await db.commit()


db = Database()
