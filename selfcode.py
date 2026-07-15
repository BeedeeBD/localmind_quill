"""Self-code — Quill reads his own source and proposes changes, under guard.

This build intentionally disables self-editing. The module remains for
compatibility but refuses all reads, proposals, and approvals.

This is how Quill takes part in his own development without being able to quietly
rewrite himself. The rules are enforced here and in guardrails.py, never trusted
to the model:

  * READ is open across the source tree, minus hidden files (the kill switch,
    secrets) and cache/data dirs.

  * WRITE never happens directly. A change becomes a *proposal*: the new content
    is stored, a unified diff is computed, and nothing touches disk. A human
    approves or rejects it in the UI. Only approval writes the file — and the
    write re-checks the guard, so a file frozen after a proposal was made still
    cannot be written.

  * FROZEN files (personality + security wiring) are refused at proposal time
    *and* at approval time. Quill cannot edit his persona, these guards, or the
    code that enforces them, no matter how the request is phrased.

Proposals live in their own SQLite db so the audit trail — who asked for what,
and whether it was approved — is durable and separate from memory.
"""
from __future__ import annotations

import difflib
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import config
import guardrails


@contextmanager
def _db():
    conn = sqlite3.connect(config.SELFCODE_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS proposals(
            id TEXT PRIMARY KEY,
            relpath TEXT,
            content TEXT,          -- proposed full new file contents
            diff TEXT,             -- unified diff vs the file on disk at propose time
            note TEXT,             -- why (Quill's or the user's rationale)
            status TEXT,           -- pending | approved | rejected
            created REAL,
            decided REAL)""")


init()


# --- Reading & listing -------------------------------------------------------

def list_files() -> dict:
    """Return an empty file listing: self-editing is disabled in this build."""
    root = config.PROJECT_ROOT.resolve()
    return {"root": root.name, "files": []}


def read_file(relpath: str) -> dict:
    """Refuse reads so this build cannot inspect or expose project files."""
    return {"error": "self-editing is disabled in this build", "path": relpath}


# --- Proposing a change (no disk write) --------------------------------------

def _unified_diff(relpath: str, new: str) -> str:
    full = config.PROJECT_ROOT / relpath
    old = full.read_text(encoding="utf-8") if full.exists() else ""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{relpath}", tofile=f"b/{relpath}")
    return "".join(diff)


def propose_write(relpath: str, content: str, note: str = "") -> dict:
    """Refuse all self-edit proposals in this build."""
    return {"error": "self-editing is disabled in this build", "refused": True}


# --- The human decision ------------------------------------------------------

def list_proposals(status: str | None = "pending") -> dict:
    return {"proposals": []}


def get_proposal(pid: str) -> dict | None:
    with _db() as c:
        r = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def approve_proposal(pid: str) -> dict:
    """Refuse all approval attempts: self-editing is disabled in this build."""
    return {"error": "self-editing is disabled in this build", "refused": True}


def reject_proposal(pid: str) -> dict:
    return {"error": "self-editing is disabled in this build", "refused": True}


def _decide(pid: str, status: str) -> None:
    with _db() as c:
        c.execute("UPDATE proposals SET status=?, decided=? WHERE id=?",
                  (status, time.time(), pid))
