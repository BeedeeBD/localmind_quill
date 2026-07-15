# Quill

**A private, fully offline AI assistant — your own machine, your own data, nothing leaves.**

Built as an exploration of local-first AI and information integrity.

---

## Why this exists

**Confidential material can't go through someone else's API.** Legal, medical and research work runs on privileged information. If AI is going to touch it, it has to run on infrastructure you control. Quill is fully local: FastAPI backend, a local LLM (Ollama or llama.cpp), ChromaDB for vector search — zero cloud calls. The one exception is opt-in, tightly-guarded web search, brokered entirely through the backend with SSRF and private-IP blocking, no JS execution, and size/time caps.

**Memory shouldn't silently rot — or silently rewrite itself.** My research examines how generative AI distorts and destabilises knowledge. Quill's memory is append-only and versioned: nothing is overwritten, "forgetting" flips a validity bit rather than deleting, and retrieval is auditable rather than asserted.

**An AI that can modify itself needs a human in the loop, always.** This build does not expose self-editing or a developer tab, and Quill cannot propose edits to his own source code. He also has no way to reach his own kill switch: it lives outside the project tree, and he has no HTTP-calling ability to touch it.

---

## Architecture

One backend, four working parts, each with a single job:

| Part | Role |
|---|---|
| **Quill** (`quill.py`, `quill_persona.md`) | The voice. Orchestrates a turn — session buffer → triage → in-character reply. Never stores anything directly. |
| **The Librarian** (`librarian.py`) | Classification and routing. Files time-sensitive items live; consolidates closed conversation turns and shelves them. |
| **The Archive** (`archive.py`, `ddc.py`) | Tiered cold storage — hot spine → warm "card" → cold, gzip-compressed "book" — organised under a Dewey-Decimal-style ten-genre scheme. |
| **The Desk** (`desk.py`) | Notes, calendar, references — plain SQLite. |

Underneath sits **MemFTL** (`memory.py`) — the memory engine proper:

- **Append-only semantic chunks** (text + embedding), never mutated
- **A logical→physical mapping table**, so chunks can be moved or compacted without breaking references
- **Per-key version history** — "forgetting" flips a validity bit rather than deleting
- **Cosine-similarity recall** over valid chunks, with access-count heat tracking
- A **knowledge-graph layer** (`graph.py`) that derives edges between archived memories on demand, so Quill can surface "this connects to what you said before"

**The hypothesis, stated plainly:** that an LLM's memory can be made auditable and trustworthy the way a flash-translation layer makes physical storage trustworthy — never overwrite, always version, invalidate by metadata not deletion, and *measure* what's happening (chunks loaded, access heat) rather than asserting it. This is explicitly framed as an **untested, unbenchmarked hypothesis**.

A second, related hypothesis: that a small local model plus hard structural guardrails — grounded citations, human-approval gates, a kill switch it structurally cannot reach — can be made safe to run autonomously at home without cloud-scale alignment infrastructure.

---

## Safety & guardrails

- **Guardrails** (`guardrails.py`) — a single source of truth distinguishing **FROZEN** files (readable, never writable: persona, security, core app files) from **HIDDEN** files (Quill cannot even see them: the kill switch, `.env`, stop/resume scripts).
- **Kill switch** (`killswitch.py`) — the mere presence of a file *outside* the project tree halts every turn before any model call. Toggled only from a browser-only UI button Quill has no way to reach.
- **Self-code** (`selfcode.py`) — self-editing is disabled in this build, so Quill cannot read or propose edits to his own source code through the UI or API.
- **Guarded web access** — DuckDuckGo only, SSRF/private-IP blocking, no JavaScript execution, size and time caps, untrusted content quarantined in the prompt, optional Tor/proxy routing.

---

## Features

- Streaming chat, stateful across turns, with auto-archiving once a conversation grows past a configurable length
- RAG over uploaded documents (PDF/text ingestion), answers grounded with sources
- Guarded web research
- Notes + calendar ("Desk"), with `.ics` export as a one-way bridge into any calendar app — no OAuth
- A "second brain" knowledge-graph view over archived conversations
- No self-code editing surface in this build

---

## Stack

- **Backend:** FastAPI, single process
- **LLM:** `llm.py` abstracts over two backends — **Ollama** (default: `qwen2.5-coder:7b` for chat, `nomic-embed-text` for embeddings) or **llama.cpp** in-process via `llama-cpp-python` (GGUF, no external service)
- **Storage:** SQLite for structured data (desk, memory log, sessions, archive, self-code proposals) + ChromaDB for vectors — all under `data/`, relocatable via `DATA_DIR`
- **Frontend:** a single self-contained `static/index.html` — no CDNs, no web fonts, inline SVG icons, deliberately dependency-free to preserve the offline guarantee

---

## Setup

**Prerequisites:** Windows (helper scripts are Windows-specific; the Python code is cross-platform) · Python 3.12 · Ollama, if using the default backend.

```bash
# 1. Clone and create the virtual environment
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 2. Pull the models (Ollama backend, default)
ollama pull qwen2.5-coder:7b
ollama pull nomic-embed-text

# 3. Configure (optional — every setting has a sensible default)
# edit .env — DATA_DIR, WEB_SEARCH policy, NUM_CTX, KILL_SWITCH_FILE, etc.

# 4. Run
uvicorn app:app --reload
# then open http://127.0.0.1:8000
```

*(Alternative backend: set `LLM_BACKEND=llamacpp` in `.env`, `pip install llama-cpp-python`, and fetch GGUF weights with `scripts/get_model.py`.)*

**Kill switch:** `scripts/STOP_QUILL.bat` (or the END button in the UI) halts Quill instantly. `scripts/RESUME_QUILL.bat` brings him back.

---

## Status — honestly

| Component | Status |
|---|---|
| Core chat, RAG, archive, desk | Built and running |
| Guardrails, kill switch, self-editing lockout | Built and running |
| MemFTL append-only memory + mapping table | Built and running |
| Knowledge-graph layer | Built and running |
| Full benchmarking of the MemFTL hypothesis (context-amplification metric vs. naive baseline) | Not yet done — this is the next real milestone |

**The honest claim:** the caching/paging concept behind MemFTL is not novel on its own — it echoes ideas already present in systems like vLLM's PagedAttention. What I believe is genuinely new here is applying the same append-only, mapping-and-invalidation discipline one level up, to **semantic memory** rather than to KV cache blocks — and pairing it with a persona and safety layer designed so a self-modifying local agent stays firmly human-gated. Both remain hypotheses until properly benchmarked.

---

## Feel free to reach out!

If you work on local inference, retrieval, or AI safety and governance, I would welcome being told where this is wrong.
