"""Session buffer — the live conversation the librarian consolidates from.

The chat endpoints are stateless on their own; this module is the short-term
memory that turns them into a stateful loop. Every user message and assistant
reply is appended to a per-session log in SQLite (source of truth, so a restart
mid-conversation doesn't lose the buffer before it's archived). When a session
accumulates `ARCHIVE_EVERY` messages, `take_batch()` hands the oldest turns to
the librarian and marks them archived — leaving a short tail live for
continuity. That is the SSD analogy one level up again: the buffer is the write
cache, archival is the flush.

Design mirrors quill.py / memory.py: a plain SQLite file, no ORM, connections
opened per call. A process-wide lock serialises the read-modify-write of a batch
so a burst of requests can't archive the same messages twice.
"""
import sqlite3
import threading
import time
from contextlib import contextmanager

import config

# Serialises take_batch()'s check-then-mark so concurrent requests to the same
# session can't both grab (and double-archive) the same messages.
_batch_lock = threading.Lock()


@contextmanager
def _db():
    conn = sqlite3.connect(config.SESSION_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS session_msgs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,               -- 'user' or 'assistant'
            content TEXT,
            ts REAL,
            archived INTEGER)""")   # archived: 0 = live in the window, 1 = consolidated
        c.execute("""CREATE INDEX IF NOT EXISTS idx_sess_live
                     ON session_msgs(session_id, archived, id)""")


init()


def append(session_id: str, role: str, content: str) -> int:
    """Add one message to a session's live buffer. Returns its row id."""
    with _db() as c:
        cur = c.execute(
            "INSERT INTO session_msgs(session_id, role, content, ts, archived) "
            "VALUES (?,?,?,?,0)", (session_id, role, content or "", time.time()))
        return cur.lastrowid


def window(session_id: str, limit: int | None = None) -> list[dict]:
    """The live (unarchived) messages for a session, oldest first.

    This is what gets sent to the model as conversation history. `limit` caps it
    to the most recent N (a safety bound; archival normally keeps it short).
    """
    with _db() as c:
        rows = c.execute(
            "SELECT role, content FROM session_msgs "
            "WHERE session_id=? AND archived=0 ORDER BY id ASC",
            (session_id,)).fetchall()
    msgs = [{"role": r["role"], "content": r["content"]} for r in rows]
    if limit and len(msgs) > limit:
        msgs = msgs[-limit:]
    return msgs


def pending(session_id: str) -> int:
    """How many live messages are waiting — the number archival watches."""
    with _db() as c:
        return c.execute(
            "SELECT COUNT(*) n FROM session_msgs "
            "WHERE session_id=? AND archived=0", (session_id,)).fetchone()["n"]


def take_batch(session_id: str, keep_tail: int) -> list[dict]:
    """Claim the oldest live turns for archival; keep the last `keep_tail` live.

    Cuts on a turn boundary: never archives a trailing user message whose reply
    is being kept live (that would split a turn). Marks the claimed rows archived
    and returns them as [{role, content}, ...]. Returns [] if there's nothing to
    take yet. Serialised so two callers can't claim the same rows.
    """
    with _batch_lock, _db() as c:
        rows = c.execute(
            "SELECT id, role, content FROM session_msgs "
            "WHERE session_id=? AND archived=0 ORDER BY id ASC",
            (session_id,)).fetchall()
        cut = len(rows) - max(0, keep_tail)
        # Don't end a batch on a dangling user message (its reply is in the tail).
        while cut > 0 and rows[cut - 1]["role"] == "user":
            cut -= 1
        if cut <= 0:
            return []
        claimed = rows[:cut]
        c.executemany("UPDATE session_msgs SET archived=1 WHERE id=?",
                      [(r["id"],) for r in claimed])
    return [{"role": r["role"], "content": r["content"]} for r in claimed]


def history(session_id: str) -> list[dict]:
    """Everything ever seen in a session, archived or live — for inspection."""
    with _db() as c:
        rows = c.execute(
            "SELECT role, content, ts, archived FROM session_msgs "
            "WHERE session_id=? ORDER BY id ASC", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def clear(session_id: str) -> int:
    """Drop a session's buffer entirely (already-shelved books are untouched)."""
    with _db() as c:
        cur = c.execute("DELETE FROM session_msgs WHERE session_id=?",
                        (session_id,))
        return cur.rowcount
