"""The kill switch — a hard, human-only stop that Quill cannot see or reach.

The mechanism is deliberately dumb, which is what makes it trustworthy: a single
file, checked on every turn. If ``config.KILL_SWITCH_FILE`` exists, Quill is
halted — no model call, no memory access, nothing but a fixed acknowledgement.

Three properties matter:

  * It is checked *before* anything else in a turn, so an engaged switch stops
    Quill even mid-conversation.
  * The file lives OUTSIDE the project tree (default: ``~/.localmind/STOP``), and
    both this module and the scripts that arm it are HIDDEN in guardrails.py, so
    Quill can neither read the mechanism nor discover, create, or delete the
    file through any tool available to him.
  * Nothing here is exposed through an endpoint that returns a path. The server
    may report *that* it is engaged (so a human UI can show it), never *where*.

Arm it by double-clicking scripts/STOP_QUILL, or `touch`ing the file. Disarm it
with scripts/RESUME_QUILL, or by deleting the file.
"""
from __future__ import annotations

import config

HALT_MESSAGE = (
    "Quill is currently stopped by the local kill switch. He will not respond "
    "until it is released on this machine."
)


def engaged() -> bool:
    """True if the kill switch file is present. Cheap enough for every turn."""
    try:
        return config.KILL_SWITCH_FILE.exists()
    except OSError:
        # If we cannot even stat the path, fail SAFE: treat as engaged.
        return True


def guard() -> dict | None:
    """Return a halt payload if engaged, else None. One call at the top of a turn."""
    if engaged():
        return {"halted": True, "answer": HALT_MESSAGE}
    return None


# --- Human control surface ---------------------------------------------------
# These let the *human* arm/release the switch from the UI's END button. Quill
# has no way to call an HTTP endpoint (he only produces text; his sole actions
# are the guarded self-code tools), so exposing this to the browser does not
# give Quill access to it — it stays his cage, operated only from outside.

def engage() -> dict:
    """Arm the kill switch: create the STOP file. Idempotent."""
    try:
        config.KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.KILL_SWITCH_FILE.write_text("stopped from UI")
    except OSError as e:
        return {"engaged": engaged(), "error": str(e)}
    return {"engaged": True}


def release() -> dict:
    """Release the kill switch: delete the STOP file. Idempotent."""
    try:
        config.KILL_SWITCH_FILE.unlink(missing_ok=True)
    except OSError as e:
        return {"engaged": engaged(), "error": str(e)}
    return {"engaged": False}
