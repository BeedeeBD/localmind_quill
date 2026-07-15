"""The Desk — the librarian's desk: notes, calendar, and reference lists.

These are the user-facing "apps" Quill files things into on your behalf: a
reminder becomes a calendar event, a "note to self" becomes a note, a generated
reading list becomes a set of references. It is deliberately NOT wired into
Outlook or Gmail — everything lives in a plain SQLite file on disk (the source
of truth), and notes are mirrored into the search index (via rag.py) so they can
be found again by meaning.

Getting an event into a real calendar goes through a safe, human-approved
bridge: export_ics() writes a standard .ics file the user double-clicks to
import. No account access, no stored passwords, nothing that can act on the
user's behalf without an explicit click.

(This module was formerly `quill.py`; the Quill name now belongs to the
assistant himself.)
"""
import datetime
import json
import sqlite3
import uuid
from contextlib import contextmanager

import config
import rag


@contextmanager
def _db():
    conn = sqlite3.connect(config.DESK_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS notes(
            id TEXT PRIMARY KEY, title TEXT, body TEXT, tags TEXT,
            created TEXT, updated TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS events(
            id TEXT PRIMARY KEY, title TEXT, start TEXT, "end" TEXT,
            description TEXT, created TEXT)""")


init()  # make sure the tables exist the moment this module is imported


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _note_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "title": r["title"], "body": r["body"],
        "tags": json.loads(r["tags"] or "[]"),
        "created": r["created"], "updated": r["updated"],
    }


# --- Notes ------------------------------------------------------------------

def list_notes() -> list[dict]:
    with _db() as c:
        rows = c.execute("SELECT * FROM notes ORDER BY updated DESC").fetchall()
    return [_note_row(r) for r in rows]


def get_note(note_id: str) -> dict | None:
    with _db() as c:
        r = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    return _note_row(r) if r else None


def create_note(title: str = "", body: str = "", tags: list[str] | None = None) -> dict:
    note_id = uuid.uuid4().hex
    now = _now()
    with _db() as c:
        c.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)",
                  (note_id, title, body, json.dumps(tags or []), now, now))
    rag.index_note(note_id, f"{title}\n{body}", title)   # keep search in sync
    return get_note(note_id)


def update_note(note_id: str, title: str, body: str, tags: list[str] | None = None) -> dict | None:
    if not get_note(note_id):
        return None
    with _db() as c:
        c.execute("UPDATE notes SET title=?, body=?, tags=?, updated=? WHERE id=?",
                  (title, body, json.dumps(tags or []), _now(), note_id))
    rag.index_note(note_id, f"{title}\n{body}", title)
    return get_note(note_id)


def delete_note(note_id: str) -> bool:
    with _db() as c:
        cur = c.execute("DELETE FROM notes WHERE id=?", (note_id,))
        deleted = cur.rowcount > 0
    rag.remove_note(note_id)
    return deleted


def search_notes(query: str, k: int = 5):
    """Semantic search over notes, for the librarian. Returns (text, meta) pairs."""
    return rag.search_notes(query, k)


# --- Events -----------------------------------------------------------------

def _event_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "title": r["title"], "start": r["start"],
        "end": r["end"], "description": r["description"], "created": r["created"],
    }


def list_events() -> list[dict]:
    with _db() as c:
        rows = c.execute('SELECT * FROM events ORDER BY start ASC').fetchall()
    return [_event_row(r) for r in rows]


def get_event(event_id: str) -> dict | None:
    with _db() as c:
        r = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    return _event_row(r) if r else None


def create_event(title: str = "", start: str = "", end: str = "",
                 description: str = "") -> dict:
    event_id = uuid.uuid4().hex
    with _db() as c:
        c.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                  (event_id, title, start, end, description, _now()))
    return get_event(event_id)


def update_event(event_id: str, title: str, start: str, end: str,
                 description: str = "") -> dict | None:
    if not get_event(event_id):
        return None
    with _db() as c:
        c.execute('UPDATE events SET title=?, start=?, "end"=?, description=? WHERE id=?',
                  (title, start, end, description, event_id))
    return get_event(event_id)


def delete_event(event_id: str) -> bool:
    with _db() as c:
        cur = c.execute("DELETE FROM events WHERE id=?", (event_id,))
        return cur.rowcount > 0


# --- The safe Outlook bridge: export one event as a standard .ics file -------

def _ics_dt(s: str) -> str:
    """ISO datetime -> iCalendar stamp (local time, no timezone suffix)."""
    try:
        return datetime.datetime.fromisoformat(s).strftime("%Y%m%dT%H%M%S")
    except Exception:
        return datetime.datetime.now().strftime("%Y%m%dT%H%M%S")


def _ics_escape(s: str) -> str:
    return ((s or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def export_ics(event: dict) -> str:
    """Build a minimal valid .ics for one event; double-clicking it imports it."""
    now = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//localmind//Quill//EN",
        "BEGIN:VEVENT",
        f"UID:{event['id']}@desk.local",
        f"DTSTAMP:{now}",
        f"DTSTART:{_ics_dt(event.get('start', ''))}",
        f"DTEND:{_ics_dt(event.get('end') or event.get('start', ''))}",
        f"SUMMARY:{_ics_escape(event.get('title', ''))}",
        f"DESCRIPTION:{_ics_escape(event.get('description', ''))}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
