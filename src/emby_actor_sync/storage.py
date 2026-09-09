"""SQLite 存储：记录已成功下载（original 尺寸）的演员图片状态。

首次运行时数据库为空，因此 people 目录下已有的旧图（h632 链接下载的）
会全部被重新下载替换；之后每次运行只下载新增或有变化的演员。
"""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actors (
    tmdbid       INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,   -- 下载时的目录名（清洗后）
    thumb_path   TEXT NOT NULL,   -- 下载时的图片路径（original）
    file_size    INTEGER,
    downloaded_at TEXT NOT NULL
);
"""


@dataclass
class Record:
    tmdbid: int
    name: str
    thumb_path: str


class Storage:
    def __init__(self, db_path: Path):
        # 下载线程会并发写入，需要 check_same_thread=False + 互斥锁
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self.conn.execute(_SCHEMA)

    def get(self, tmdbid: int) -> Record | None:
        row = self.conn.execute(
            "SELECT tmdbid, name, thumb_path FROM actors WHERE tmdbid = ?", (tmdbid,)
        ).fetchone()
        return Record(row[0], row[1], row[2]) if row else None

    def upsert(self, tmdbid: int, name: str, thumb_path: str, file_size: int) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO actors (tmdbid, name, thumb_path, file_size, downloaded_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tmdbid) DO UPDATE SET
                    name = excluded.name,
                    thumb_path = excluded.thumb_path,
                    file_size = excluded.file_size,
                    downloaded_at = excluded.downloaded_at
                """,
                (
                    tmdbid,
                    name,
                    thumb_path,
                    file_size,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            self.conn.commit()  # 每张成功即提交：中断后可断点续传，也便于外部查看进度

    def prune(self, keep_tmdbids: set[int]) -> int:
        """删除媒体库中已不存在演员的记录，返回删除条数。"""
        with self._lock:
            rows = self.conn.execute("SELECT tmdbid FROM actors").fetchall()
            stale = [tmdbid for (tmdbid,) in rows if tmdbid not in keep_tmdbids]
            self.conn.executemany("DELETE FROM actors WHERE tmdbid = ?", [(t,) for t in stale])
            self.conn.commit()
        return len(stale)

    def close(self) -> None:
        with self._lock:
            self.conn.commit()
            self.conn.close()
