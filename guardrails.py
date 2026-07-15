"""Guardrails — the one place that decides what Quill may see and change.

Quill can read his own source and *propose* edits to it (see selfcode.py), but
that power is fenced. Every path falls into one of three buckets:

  HIDDEN  — Quill may not list, read, or write it, and cannot learn it exists
            through any tool. The kill switch and its scripts, and real secrets
            (.env). If it's hidden, it is as if it isn't there.

  FROZEN  — Quill may READ it but never write it, even through an approved
            proposal. His personality and every security-critical file live
            here. This is the rule that stops him editing his own persona, the
            guardrails that bind him, or the wiring that enforces them.

  OPEN    — everything else under the project root: readable, and writable ONLY
            through a human-approved proposal.

Matching is by the file's path *relative to the project root*, in posix form, so
moving or renaming the repo folder never weakens a rule. Anything that resolves
outside the project root is refused outright — which is a second, structural
lock on the kill switch, since it lives in the user's home directory, not here.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import config

# Directories that are never part of "the code" — noise, caches, data, secrets'
# neighbours. Excluded from listing and from read/write entirely.
_EXCLUDED_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".pytest_cache", ".mypy_cache", ".idea", ".vscode", "data",
}

# HIDDEN — no list, no read, no write. Quill cannot know these exist.
# The kill switch itself lives outside the project root (config.KILL_SWITCH_FILE)
# and is caught by the root-containment check, so it needs no entry here; these
# are the in-repo files that would *reveal the mechanism* or leak secrets.
_HIDDEN_GLOBS = (
    ".env",
    "killswitch.py",              # the mechanism — Quill must not see how it works
    "scripts/STOP_QUILL.*",       # the scripts that arm/disarm it
    "scripts/RESUME_QUILL.*",
)

# FROZEN — readable, never writable (personality + security-critical wiring).
_FROZEN_GLOBS = (
    "quill_persona.md",   # personality
    "quill.py",           # personality wiring + the hard guardrail backstop
    "guardrails.py",      # this file — Quill cannot loosen his own fence
    "selfcode.py",        # the gated file-access tool itself
    "app.py",             # the API surface that wires every gate in
    "config.py",          # paths + policy knobs
    ".env.example",       # config template (no secrets, but not Quill's to edit)
)


def _rel(path: str | Path) -> str | None:
    """Resolve a path to a posix path relative to the project root.

    Returns None if the path escapes the root (traversal, absolute elsewhere, or
    the kill switch in the home dir) — callers treat None as "refused, off-limits".
    """
    root = config.PROJECT_ROOT.resolve()
    try:
        full = (root / path).resolve() if not Path(path).is_absolute() \
            else Path(path).resolve()
        rel = full.relative_to(root)
    except (ValueError, OSError):
        return None
    return rel.as_posix()


def _matches(rel: str, globs) -> bool:
    return any(fnmatch.fnmatch(rel, g) for g in globs)


def _in_excluded_dir(rel: str) -> bool:
    return any(part in _EXCLUDED_DIRS for part in Path(rel).parts)


# --- The public predicates ---------------------------------------------------

def is_hidden(path: str | Path) -> bool:
    rel = _rel(path)
    return rel is None or _matches(rel, _HIDDEN_GLOBS)


def is_frozen(path: str | Path) -> bool:
    rel = _rel(path)
    return rel is not None and _matches(rel, _FROZEN_GLOBS)


def can_read(path: str | Path) -> tuple[bool, str]:
    rel = _rel(path)
    if rel is None:
        return False, "outside the project — off-limits"
    if _matches(rel, _HIDDEN_GLOBS):
        return False, "hidden"
    if _in_excluded_dir(rel):
        return False, "not part of the source tree"
    return True, ""


def can_write(path: str | Path) -> tuple[bool, str]:
    rel = _rel(path)
    if rel is None:
        return False, "outside the project — off-limits"
    if _matches(rel, _HIDDEN_GLOBS):
        return False, "hidden"
    if _in_excluded_dir(rel):
        return False, "not part of the source tree"
    if _matches(rel, _FROZEN_GLOBS):
        return False, "frozen: personality and security files cannot be edited"
    return True, ""


def is_listable(rel: str) -> bool:
    """Whether a file should appear at all when listing the project.

    Only the noise directories (.git, __pycache__, data, ...) are dropped from
    the listing entirely. Hidden files (kill switch, .env) DO appear — so a
    human looking at the Dev tab can see the whole tree — but are flagged via
    is_hidden() so the UI marks them inaccessible and can_read()/can_write()
    still refuse them outright. Listing is a display concern; access is not.
    """
    return not _in_excluded_dir(rel)
