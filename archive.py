"""The Archive — where memory physically lives.

The Archive is pure storage: it knows nothing about *what* a turn means or *how*
to decide where it goes (that is the Librarian's job). It only keeps books and
hands them back. Each book is stored in the three tiers the design is built
around:

  * SPINE (hot)  — DDC class + one-line title + key terms. Browsing a shelf
                   reads only spines; it never opens a book.
  * CARD  (warm) — the condensed summary. Stored here for display, and
                   *separately* embedded in the MemFTL store (memory.py) by the
                   Librarian so semantic recall and the amplification metric
                   cover it.
  * BOOK  (cold) — the full raw turn, stored compressed and opened only on
                   demand.

Compression stores whichever of gzip/raw is smaller: gzip wins big on real turns
(10x+) but its header would *grow* a very short turn, which would defeat the
whole space goal. open_book tells the two apart by the gzip magic bytes, so
there is no schema flag and no migration.
"""
import gzip
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

import config
import ddc


@contextmanager
def _db():
    conn = sqlite3.connect(config.ARCHIVE_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS books(
            id TEXT PRIMARY KEY,
            logical_key TEXT,        -- the MemFTL card key this book backs
            ddc TEXT,                -- '500' etc — the shelf
            ddc_label TEXT,
            title TEXT,              -- one-line spine title
            terms TEXT,              -- JSON list — catalog subject headings
            summary TEXT,            -- the catalog card (condensed text)
            raw_gz BLOB,             -- gzipped (or raw) full turn — the cold book
            raw_tokens INTEGER,      -- size of the original turn
            card_tokens INTEGER,     -- size of the condensed card
            routes TEXT,             -- JSON: which routes fired for this turn
            created REAL,
            access_count INTEGER)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_books_ddc ON books(ddc)")


init()


def _approx_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _pack(raw: str) -> bytes:
    """Store whichever is smaller — gzip'd or plain (told apart on read)."""
    raw_bytes = raw.encode("utf-8")
    gz = gzip.compress(raw_bytes)
    return gz if len(gz) < len(raw_bytes) else raw_bytes


def _read_raw(blob: bytes) -> str:
    """Decode a stored book, gzip'd or not (gzip magic bytes = 0x1f 0x8b)."""
    blob = bytes(blob)
    if blob[:2] == b"\x1f\x8b":
        return gzip.decompress(blob).decode("utf-8")
    return blob.decode("utf-8")


def store_book(logical_key: str, ddc_code: str, title: str, terms: list[str],
               summary: str, raw: str, routes: list[str]) -> dict:
    """Shelve one book across the three tiers. Returns its spine + compression."""
    raw_tok, card_tok = _approx_tokens(raw), _approx_tokens(summary)
    book_id = uuid.uuid4().hex
    with _db() as c:
        c.execute("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (book_id, logical_key, ddc_code, ddc.label(ddc_code), title,
                   json.dumps(terms), summary, _pack(raw),
                   raw_tok, card_tok, json.dumps(sorted(routes)),
                   time.time(), 0))
    return {
        "book_id": book_id, "logical_key": logical_key,
        "ddc": ddc_code, "ddc_label": ddc.label(ddc_code),
        "title": title, "terms": terms,
        "raw_tokens": raw_tok, "card_tokens": card_tok,
        "compression": round(raw_tok / card_tok, 2) if card_tok else 1.0,
    }


def _spine(r: sqlite3.Row) -> dict:
    return {"book_id": r["id"], "ddc": r["ddc"], "ddc_label": r["ddc_label"],
            "title": r["title"], "terms": json.loads(r["terms"] or "[]"),
            "summary": r["summary"], "logical_key": r["logical_key"],
            "routes": json.loads(r["routes"] or "[]"),
            "compression": round(r["raw_tokens"] / r["card_tokens"], 2)
            if r["card_tokens"] else 1.0}


def browse_shelf(ddc_code: str | None = None) -> dict:
    """List spine cards, optionally for one DDC class. Never opens a book."""
    with _db() as c:
        if ddc_code:
            hundred = ddc.to_hundred(ddc_code)
            rows = c.execute("SELECT * FROM books WHERE ddc=? ORDER BY created DESC",
                             (hundred,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM books ORDER BY created DESC").fetchall()
    return {"shelf": ddc_code, "books": [_spine(r) for r in rows]}


def shelves() -> list[dict]:
    """The ten genres with how many books sit on each — the library directory."""
    with _db() as c:
        counts = dict(c.execute(
            "SELECT ddc, COUNT(*) FROM books GROUP BY ddc").fetchall())
    return [{"ddc": code, "label": lbl, "books": counts.get(code, 0)}
            for code, (lbl, _) in sorted(ddc.DDC.items())]


def open_book(book_id: str) -> dict | None:
    """Pull one cold book off the shelf and decompress its full raw text."""
    with _db() as c:
        r = c.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not r:
            return None
        c.execute("UPDATE books SET access_count=access_count+1 WHERE id=?",
                  (book_id,))
    out = _spine(r)
    out["raw"] = _read_raw(r["raw_gz"])
    out["access_count"] = r["access_count"] + 1
    return out


def books_by_key() -> dict[str, dict]:
    """Map logical_key -> its book's shelf info, for enriching recall hits."""
    out: dict[str, dict] = {}
    with _db() as c:
        for r in c.execute("SELECT id, logical_key, ddc, ddc_label, title "
                           "FROM books").fetchall():
            out[r["logical_key"]] = {"book_id": r["id"], "ddc": r["ddc"],
                                     "ddc_label": r["ddc_label"], "title": r["title"]}
    return out
