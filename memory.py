"""MemFTL — append-only semantic memory with an indirection map.

This is a first, deliberately small implementation of the hypothesis in the
README: treat conversational memory the way a flash translation layer treats an
SSD. It is a working substrate to *measure* the idea, not a finished system.

What is implemented here:

  * Append-only chunks. A chunk is a span of text plus its embedding. Its text
    and embedding are immutable once written — exactly like an SSD page. Revising
    a fact never edits in place; it writes a NEW chunk (version + 1) and flips
    the previous chunk's validity bit. The old text is retained, so every key
    carries an auditable version history and nothing is silently overwritten.
    (Flipping a validity bit is a metadata operation — the data page itself is
    never rewritten, which is precisely how an FTL invalidates a page.)

  * A logical -> physical mapping table (`mem_map`). Callers reference a logical
    key (an entity, a topic, a thread); the map resolves it to a physical chunk.
    Because references go through this indirection, chunks can be invalidated,
    compacted, or garbage-collected underneath without breaking a caller.

  * Chunk loading by meaning. recall() embeds the query and loads only the most
    relevant *valid* chunks — the whole point is to not load tokens you don't
    need. Loaded chunks have their access count incremented (the raw signal for
    future heat-based tiering).

  * Context-amplification instrumentation. Every recall records how many chunks
    and tokens were loaded, so the design can be measured. Honest caveat: this
    is only the *numerator* (tokens loaded). The denominator — "tokens that
    actually contributed to the answer" — is not yet instrumented.

Not done yet (see the README status table): re-summarising GC (only dead-chunk
reclamation is implemented), heat tiering across KV/vector/quantised stores, and
KV-cache paging. Those are the next layers.
"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

import numpy as np

import config
import llm

# Indirection for the embedder so the structural logic can be unit-tested
# without a running model (tests replace memory.embed_fn with a stub).
embed_fn = llm.embed


@contextmanager
def _db():
    conn = sqlite3.connect(config.MEMORY_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS mem_chunks(
            id TEXT PRIMARY KEY,
            logical_key TEXT,
            text TEXT,
            embedding TEXT,          -- JSON list; immutable once written
            tokens INTEGER,
            version INTEGER,
            valid INTEGER,           -- 1 = current, 0 = superseded (kept as history)
            created REAL,
            access_count INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS mem_map(
            logical_key TEXT PRIMARY KEY,
            chunk_id TEXT,
            updated REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS mem_metrics(
            id INTEGER PRIMARY KEY CHECK (id = 1),
            recalls INTEGER, chunks_loaded INTEGER, tokens_loaded INTEGER)""")
        c.execute("INSERT OR IGNORE INTO mem_metrics VALUES (1, 0, 0, 0)")


init()


def _approx_tokens(text: str) -> int:
    # Rough, honest heuristic (~4 chars/token). Good enough to trend amplification.
    return max(1, len(text) // 4)


def get_chunk(chunk_id: str) -> dict | None:
    with _db() as c:
        r = c.execute("""SELECT id, logical_key, text, tokens, version, valid,
                         created, access_count FROM mem_chunks WHERE id=?""",
                      (chunk_id,)).fetchone()
    return dict(r) if r else None


def remember(logical_key: str, text: str) -> dict | None:
    """Append a new memory chunk for a logical key (append-only revise).

    Writes a new immutable chunk, invalidates the previous valid chunk for this
    key (retained as history), and repoints the indirection map at the new one.
    """
    text = (text or "").strip()
    if not text:
        return None
    emb = embed_fn(text)
    now = time.time()
    with _db() as c:
        prev = c.execute("""SELECT id, version FROM mem_chunks
                            WHERE logical_key=? AND valid=1
                            ORDER BY version DESC LIMIT 1""", (logical_key,)).fetchone()
        version = (prev["version"] + 1) if prev else 1
        if prev:
            # Metadata-only invalidation; the old page's text/embedding stay put.
            c.execute("UPDATE mem_chunks SET valid=0 WHERE id=?", (prev["id"],))
        chunk_id = uuid.uuid4().hex
        c.execute("INSERT INTO mem_chunks VALUES (?,?,?,?,?,?,?,?,?)",
                  (chunk_id, logical_key, text, json.dumps(emb),
                   _approx_tokens(text), version, 1, now, 0))
        c.execute("""INSERT INTO mem_map(logical_key, chunk_id, updated)
                     VALUES (?,?,?)
                     ON CONFLICT(logical_key) DO UPDATE SET
                        chunk_id=excluded.chunk_id, updated=excluded.updated""",
                  (logical_key, chunk_id, now))
    return get_chunk(chunk_id)


def recall(query: str, k: int | None = None) -> dict:
    """Load only the most relevant valid chunks for a query (chunk loading)."""
    k = k or config.MEM_TOP_K
    with _db() as c:
        rows = c.execute("""SELECT id, logical_key, text, embedding, tokens,
                            version, access_count FROM mem_chunks
                            WHERE valid=1""").fetchall()
    if not rows:
        _bump_metrics(0, 0)
        return {"chunks": [], "tokens_loaded": 0, "amplification": _amp()}

    q = np.asarray(embed_fn(query), dtype=float)
    qn = q / (np.linalg.norm(q) or 1.0)
    scored = []
    for r in rows:
        e = np.asarray(json.loads(r["embedding"]), dtype=float)
        score = float(np.dot(qn, e / (np.linalg.norm(e) or 1.0)))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]

    tokens_loaded = 0
    with _db() as c:
        for _, r in top:
            c.execute("UPDATE mem_chunks SET access_count=access_count+1 WHERE id=?",
                      (r["id"],))
            tokens_loaded += r["tokens"]
    _bump_metrics(len(top), tokens_loaded)

    chunks = [{
        "id": r["id"], "logical_key": r["logical_key"], "text": r["text"],
        "version": r["version"], "score": round(score, 4),
        "access_count": r["access_count"] + 1,
    } for score, r in top]
    return {"chunks": chunks, "tokens_loaded": tokens_loaded, "amplification": _amp()}


def vectors_by_key() -> dict[str, list[float]]:
    """The current (valid) embedding for each logical key.

    The knowledge graph (graph.py) uses this to measure how close two archived
    memories are, without re-embedding anything — the vectors were already
    computed when each card was remembered.
    """
    with _db() as c:
        rows = c.execute("SELECT logical_key, embedding FROM mem_chunks "
                         "WHERE valid=1").fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        out[r["logical_key"]] = json.loads(r["embedding"])
    return out


def history(logical_key: str) -> list[dict]:
    """Every version ever written for a key, valid or superseded — the audit log."""
    with _db() as c:
        rows = c.execute("""SELECT id, version, valid, text, tokens, created,
                            access_count FROM mem_chunks WHERE logical_key=?
                            ORDER BY version ASC""", (logical_key,)).fetchall()
    return [dict(r) for r in rows]


def forget(logical_key: str) -> dict:
    """Invalidate a key's current chunk (kept as history) and drop the mapping."""
    with _db() as c:
        cur = c.execute("UPDATE mem_chunks SET valid=0 WHERE logical_key=? AND valid=1",
                        (logical_key,))
        c.execute("DELETE FROM mem_map WHERE logical_key=?", (logical_key,))
        return {"invalidated": cur.rowcount}


def garbage_collect() -> dict:
    """v1 consolidation: reclaim space held by superseded (dead) chunks.

    This is only the space-reclamation half of consolidation. Re-summarising the
    surviving chunks of a hot region into one denser chunk is the next step and
    is not done here yet (see the README status table).
    """
    with _db() as c:
        dead = c.execute("SELECT COUNT(*) n FROM mem_chunks WHERE valid=0").fetchone()["n"]
        c.execute("DELETE FROM mem_chunks WHERE valid=0")
    return {"freed_chunks": dead}


def stats() -> dict:
    with _db() as c:
        total = c.execute("SELECT COUNT(*) n FROM mem_chunks").fetchone()["n"]
        valid = c.execute("SELECT COUNT(*) n FROM mem_chunks WHERE valid=1").fetchone()["n"]
        keys = c.execute("SELECT COUNT(*) n FROM mem_map").fetchone()["n"]
    return {"chunks_total": total, "chunks_valid": valid, "chunks_dead": total - valid,
            "logical_keys": keys, "amplification": _amp()}


def _bump_metrics(chunks: int, tokens: int) -> None:
    with _db() as c:
        c.execute("""UPDATE mem_metrics SET recalls=recalls+1,
                     chunks_loaded=chunks_loaded+?, tokens_loaded=tokens_loaded+?
                     WHERE id=1""", (chunks, tokens))


def _amp() -> dict:
    """Context-amplification instrumentation (numerator only, honestly labelled)."""
    with _db() as c:
        m = c.execute("SELECT recalls, chunks_loaded, tokens_loaded "
                      "FROM mem_metrics WHERE id=1").fetchone()
    recalls = m["recalls"]
    return {
        "recalls": recalls,
        "chunks_loaded": m["chunks_loaded"],
        "tokens_loaded": m["tokens_loaded"],
        "avg_tokens_per_recall": round(m["tokens_loaded"] / recalls, 1) if recalls else 0,
        "note": ("tokens_loaded is the numerator of context amplification; the "
                 "'tokens that actually contributed' denominator is not yet "
                 "instrumented."),
    }
