"""Windowless entry point for auto-start.

Runs the FastAPI server in a single process (no reloader), so it can be launched
by `pythonw.exe` with no console window — which is what the auto-start uses. Host
and port come from config (.env), so this honours the same settings as everything
else.

Run it directly for a plain, no-frills start:
    python serve.py
"""
import sys

import config

# Under pythonw.exe there is no console, so sys.stdout / sys.stderr are None and
# anything that writes to them (uvicorn's logger, a stray print, a traceback)
# would crash the server on launch — silently, since there's nowhere to show the
# error. Redirect both to a log file so the server runs *and* any problem is
# recorded where you can find it.
if sys.stdout is None or sys.stderr is None:
    _log = open(config.DATA_DIR / "server.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = _log

import uvicorn  # noqa: E402  (imported after the stream redirect above)

if __name__ == "__main__":
    uvicorn.run("app:app", host=config.HOST, port=config.PORT, log_level="info")
