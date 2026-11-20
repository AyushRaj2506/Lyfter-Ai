import logging
import sqlite3
from contextlib import closing, contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Iterator

from .schemas import AssistantMessage, Metadata, UserMessage

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL,
    CHECK((role = 'assistant' AND metadata IS NOT NULL) OR (role = 'user' AND metadata IS NULL))
);
CREATE INDEX IF NOT EXISTS messages_session_id_id ON messages(session_id, id);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.temp_dir: TemporaryDirectory[str] | None = None
        self.initialized = False

    def start(self) -> None:
        try:
            self.initialize()
        except (PermissionError, NotADirectoryError, FileExistsError):
            self.use_temporary_database()
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", 0) & 0xFF
            if code not in (sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY):
                raise
            self.use_temporary_database()

    def use_temporary_database(self) -> None:
        logger.error("Configured SQLite location is unwritable. Using temporary storage until restart.")
        self.temp_dir = TemporaryDirectory(prefix="lyftr-")
        self.path = Path(self.temp_dir.name) / "app.db"
        self.initialize()

    def close(self) -> None:
        if self.temp_dir:
            self.temp_dir.cleanup()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        self.initialized = True

    def ready(self) -> bool:
        if self.initialized and self.path.is_file():
            with suppress(sqlite3.Error), self.connect() as connection:
                connection.execute("SELECT 1").fetchone()
                return True
        return False

    def context(self, session_id: str, limit: int) -> tuple[str | None, list[dict[str, str]]]:
        with self.connect() as connection:
            session = connection.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            rows = connection.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return (session["system_prompt"] if session else None), [dict(row) for row in reversed(rows)]

    def save_turn(
        self, session_id: str, system_prompt: str, message: str, reply: str, metadata: Metadata
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(session_id, system_prompt, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    system_prompt = excluded.system_prompt, updated_at = excluded.updated_at
                """,
                (session_id, system_prompt, created_at, created_at),
            )
            connection.executemany(
                "INSERT INTO messages(session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    (session_id, "user", message, None, created_at),
                    (session_id, "assistant", reply, metadata.model_dump_json(), created_at),
                ),
            )

    def history(self, session_id: str) -> list[UserMessage | AssistantMessage]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [
            UserMessage(role="user", content=row["content"])
            if row["role"] == "user"
            else AssistantMessage(
                role="assistant",
                content=row["content"],
                metadata=Metadata.model_validate_json(row["metadata"]),
            )
            for row in rows
        ]


class SessionLocks:
    """Keep a lock until the last waiter for its session has finished."""

    def __init__(self) -> None:
        self.guard = Lock()
        self.active: dict[str, tuple[Lock, int]] = {}

    @contextmanager
    def hold(self, session_id: str) -> Iterator[None]:
        with self.guard:
            if session_id in self.active:
                lock, users = self.active[session_id]
            else:
                lock, users = Lock(), 0
            self.active[session_id] = lock, users + 1
        try:
            with lock:
                yield
        finally:
            with self.guard:
                _, users = self.active[session_id]
                if users == 1:
                    del self.active[session_id]
                else:
                    self.active[session_id] = lock, users - 1
