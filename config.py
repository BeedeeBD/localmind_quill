"""Central configuration — every tunable setting in one place.

Design rule: never edit code just to change a setting. Every value reads from an
environment variable (or a .env file) with a sensible default, so switching
models or moving data to another drive is a one-line change in .env rather than
a code change. See .env.example.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # read a local .env if present

# --- Where data lives -------------------------------------------------------
# The vector DB can get big once you've indexed a lot of research, so this is
# the knob to move it off the system drive. Point it at the external drive:
#   DATA_DIR=E:/localmind_data
# Defaults to a data/ folder right next to the code, which is fine to start.
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent / "data"))
CHROMA_DIR = DATA_DIR / "chroma"          # where ChromaDB keeps the vectors
# Create these on first run so nothing blows up before you've made the folder.
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

# --- Which model backend to run ---------------------------------------------
# 'ollama'   — talk to the local Ollama server (the original default).
# 'llamacpp' — run a local GGUF model in-process via llama-cpp-python, with no
#              Ollama at all. This is the "just Llama, no Ollama" path.
# llm.py is the ONLY file that reads this; nothing else knows or cares which
# backend is live, so switching is a one-line change here or in .env.
LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama").lower()

# --- Models: Ollama backend -------------------------------------------------
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5-coder:7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# --- Models: llama.cpp backend ----------------------------------------------
# Weights live off the system drive by default — C: is nearly full, E: is not.
MODELS_DIR = Path(os.getenv("MODELS_DIR", "E:/localmind_models"))
# GGUF files for chat and embeddings. Point these at whatever Llama you use.
LLAMA_MODEL_PATH = os.getenv("LLAMA_MODEL_PATH", str(MODELS_DIR / "chat.gguf"))
LLAMA_EMBED_PATH = os.getenv("LLAMA_EMBED_PATH", str(MODELS_DIR / "embed.gguf"))
LLAMA_N_CTX = int(os.getenv("LLAMA_N_CTX", os.getenv("NUM_CTX", "8192")))
LLAMA_THREADS = int(os.getenv("LLAMA_THREADS", "0"))   # 0 = llama.cpp's default
LLAMA_GPU_LAYERS = int(os.getenv("LLAMA_GPU_LAYERS", "0"))  # >0 offloads to GPU

# Thinking models (e.g. Qwen3) narrate their reasoning inside <think>...</think>
# tags in the reply. That reasoning is noise in a normal answer, so it is
# stripped out by default. Set STRIP_THINK=false to keep the reasoning visible.
STRIP_THINK = os.getenv("STRIP_THINK", "true").lower() in ("1", "true", "yes")

# How big a context window to ask the model to run with. NOTE: Ollama defaults
# this low (~4k) unless told otherwise, which quietly hurts long chats and RAG.
# We pass it on every request so the setting actually takes effect (an env var
# alone wouldn't reach the separate Ollama service). 8192 is a safe balance on
# a 16 GB CPU machine; raise toward 16384 if you have RAM headroom to spare.
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))
# Optional context features (memory recall, graph connections, reference lookup)
# are run with this soft timeout per turn. If they exceed it, Quill skips them
# rather than making the whole chat feel frozen. Set to 0 to disable the timeout.
CHAT_CONTEXT_TIMEOUT = float(os.getenv("CHAT_CONTEXT_TIMEOUT", "1.2"))

# --- RAG behaviour ----------------------------------------------------------
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))      # characters per chunk
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
TOP_K = int(os.getenv("TOP_K", "4"))                   # chunks retrieved per query

# --- Web research (DuckDuckGo, brokered through this backend) ----------------
# This is the ONLY outbound internet path besides local Ollama, and every fetch
# passes through the guards in websearch.py. Turn it off entirely with
# WEB_SEARCH=false. Fetch policy:
#   "hybrid"    search the whole web, but only auto-open reputable domains;
#               everything else comes back as links you click to approve. (safe default)
#   "open"      auto-open any result (guards still apply, but more trusting)
#   "allowlist" only ever fetch reputable domains
WEB_SEARCH = os.getenv("WEB_SEARCH", "true").lower() in ("1", "true", "yes")
WEB_FETCH_POLICY = os.getenv("WEB_FETCH_POLICY", "hybrid").lower()
WEB_MAX_RESULTS = int(os.getenv("WEB_MAX_RESULTS", "6"))    # search hits to consider
WEB_FETCH_MAX = int(os.getenv("WEB_FETCH_MAX", "3"))        # pages actually opened
WEB_MAX_BYTES = int(os.getenv("WEB_MAX_BYTES", "2000000"))  # 2 MB hard cap per page
WEB_TIMEOUT = int(os.getenv("WEB_TIMEOUT", "15"))           # seconds per request
# Comma-separated extra domains to treat as reputable (adds to the built-in list).
WEB_ALLOWLIST = os.getenv("WEB_ALLOWLIST", "")
# Optional outbound proxy for web search + page fetches, to hide your IP from the
# sites you read. Empty = direct connection. DuckDuckGo already doesn't track or
# profile you; this adds IP-level anonymity on top. Examples:
#   WEB_PROXY=socks5://127.0.0.1:9050   # Tor (run Tor Browser or `tor` first;
#                                       # needs `pip install httpx[socks]`)
#   WEB_PROXY=http://127.0.0.1:8080     # a plain HTTP proxy
WEB_PROXY = os.getenv("WEB_PROXY", "").strip()

# --- Desk (the librarian's desk: notes + calendar; SQLite is source of truth) -
# Formerly "quill.db" — the Quill name now belongs to the assistant himself
# (see quill.py / quill_persona.md); his desk is where notes and appointments sit.
DESK_DB = DATA_DIR / "desk.db"

# --- Quill (the assistant's persona) ----------------------------------------
# Quill's personality lives in an editable markdown file so it can be tuned
# without touching code. If the file is missing, quill.py falls back to a short
# built-in persona so the assistant still has a voice.
QUILL_PERSONA_FILE = Path(os.getenv(
    "QUILL_PERSONA_FILE", Path(__file__).parent / "quill_persona.md"))

# --- MemFTL (append-only semantic memory; SQLite is the append log) ----------
MEMORY_DB = DATA_DIR / "memory.db"
MEM_TOP_K = int(os.getenv("MEM_TOP_K", "4"))   # chunks loaded per recall

# --- Stateful chat loop -----------------------------------------------------
# The live conversation buffer that the archival loop consolidates from.
SESSION_DB = DATA_DIR / "sessions.db"
# Inject semantically-recalled archive cards into the chat context, so that the
# assistant can still "remember" turns that have been archived out of the live
# window. This is what stops auto-archiving from causing amnesia.
CHAT_RECALL = os.getenv("CHAT_RECALL", "true").lower() in ("1", "true", "yes")
# Hard cap on how many live (unarchived) messages are sent to the model, so the
# prompt can't grow without bound even if archiving is turned off.
CHAT_WINDOW_MAX = int(os.getenv("CHAT_WINDOW_MAX", "20"))

# --- Librarian (archival consolidation: routing + tiered book/card/spine) -----
# When a conversation is "closed", the librarian files its actionable turns into
# Quill and shelves the rest as compressed "books" under a DDC class. The cold
# books live here; the warm catalog cards live in the MemFTL store above so they
# share its measured semantic recall. Separate file so the measured memory.db
# stays clean.
ARCHIVE_DB = DATA_DIR / "archive.db"
# How many messages accumulate before a conversation is auto-archived. The
# archival unit itself is a *turn* (a user message + the assistant's reply);
# this is the message count that trips consolidation.
ARCHIVE_EVERY = int(os.getenv("ARCHIVE_EVERY", "10"))
# When a session is archived, keep this many of the most recent messages live in
# the window for conversational continuity (they'll be archived in a later
# batch). Cut on a turn boundary. 2 = keep the last full turn.
ARCHIVE_TAIL = int(os.getenv("ARCHIVE_TAIL", "2"))
# Master switch for the whole stateful chat loop. Off => /chat behaves like the
# original single-shot endpoint (no buffering, no archival).
AUTO_ARCHIVE = os.getenv("AUTO_ARCHIVE", "true").lower() in ("1", "true", "yes")
# A turn shorter than this many characters is treated as chit-chat and dropped
# rather than shelved (the "remove irrelevant catalogues" rule). Routing still
# runs first, so a short "remind me..." is never lost — only unremarkable
# filler with no route is discarded.
ARCHIVE_MIN_CHARS = int(os.getenv("ARCHIVE_MIN_CHARS", "40"))

# --- Self-development, guardrails & safety ----------------------------------
# The project tree itself. selfcode.py lets Quill read these files and *propose*
# edits, but every write is human-approved and the security-critical files are
# frozen. guardrails.py is the single source of truth for what's readable,
# frozen, or hidden; this is only where the paths that need env overrides live.
# Defaults to this repository, but remains configurable for packaged or moved
# installations.  Resolving it here makes the Dev tab's reported root stable
# and ensures all self-code path guards use the same absolute base directory.
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).parent)).resolve()

# Kill switch. The moment this file exists, Quill halts and answers nothing.
# It lives OUTSIDE the project tree and is hidden from every self-code tool, so
# Quill can neither see it nor remove it. Create it (or double-click
# scripts/STOP_QUILL) to stop him; delete it (scripts/RESUME_QUILL) to resume.
KILL_SWITCH_FILE = Path(os.getenv(
    "KILL_SWITCH_FILE", Path.home() / ".localmind" / "STOP"))

# Pending, approval-gated self-code write proposals (nothing here touches disk
# until you approve it in the UI).
SELFCODE_DB = DATA_DIR / "selfcode.db"

# --- Knowledge graph (the "second brain") -----------------------------------
# Edges between archived memories are drawn when they share enough key terms OR
# are semantically close. Tune how densely the graph connects here.
GRAPH_TERM_JACCARD = float(os.getenv("GRAPH_TERM_JACCARD", "0.30"))
GRAPH_SEMANTIC_MIN = float(os.getenv("GRAPH_SEMANTIC_MIN", "0.82"))
GRAPH_MAX_NODES = int(os.getenv("GRAPH_MAX_NODES", "150"))
# Let Quill surface 1-hop connections from the graph during chat, so he can
# "make connections" between what you're saying now and what he's shelved.
CHAT_CONNECT = os.getenv("CHAT_CONNECT", "true").lower() in ("1", "true", "yes")

# --- Server -----------------------------------------------------------------
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
