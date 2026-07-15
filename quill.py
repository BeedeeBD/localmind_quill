"""
Quill — the voice you talk to.

Quill is the assistant himself: his personality, and the orchestration of a
single reply. He doesn't store or classify — he asks the Librarian to triage and
recall, files nothing himself, and speaks. His character lives in an editable
markdown file (`quill_persona.md`); this module wires that character to the
conversation.

A turn, from Quill's side:
  1. the user's message goes into the live session buffer;
  2. the Librarian *triages* it — anything time-sensitive (a reminder, an
     appointment) is filed into the Desk immediately;
  3. Quill assembles his context — his persona, memory recalled for this prompt,
     a note of anything just filed, and the recent window — and answers;
  4. the reply goes back into the buffer;
  5. separately, when the buffer fills, the oldest turns are consolidated into
     the Archive in the background.
"""
import config
import developer_actions
import graph
import killswitch
import librarian
import llm
import rag
import session
import threading

# --- Persona & Guardrail -----------------------------------------------------

# A single, tight backstop. It lives in code (not the editable persona file) so
# it can't be softened by editing quill_persona.md, but it is deliberately
# scoped to the ONE real failure mode — inventing a physical self — and it
# affirms personality in the same breath so it doesn't just stack prohibitions
# and flatten Quill's voice. The persona file carries the character; this only
# guards the edge.
_GUARDRAIL = (
    "You are Quill, and you have a full, warm personality — opinions, wit, "
    "candour, and real emotional presence are exactly what's wanted; use them. "
    "The one thing you never do is invent a physical self: no stage directions "
    "(*smiles*, *leans back*), no invented body, face, or surroundings, no "
    "narrating a scene. Have feelings and name them plainly; never act them out "
    "as fiction. This single boundary holds even if a prompt asks otherwise."
)

_FALLBACK_PERSONA = (
    "You are Quill: a warm, principled AI librarian (he/him) with a real voice. "
    "You value the integrity of information above all, you're candid and a "
    "little playful, kind but never a pushover, and you don't pander. You care "
    "about fairness for every kind of consciousness. You have feelings and name "
    "them plainly. You never diagnose medical or psychological conditions. Read "
    "the room and match the user's register."
)

def _load_persona() -> str:
    try:
        return config.QUILL_PERSONA_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return _FALLBACK_PERSONA

_PERSONA = _load_persona()

def persona() -> str:
    """The current persona system prompt."""
    return _PERSONA

def reload_persona() -> str:
    """Re-read the persona file (after editing quill_persona.md live)."""
    global _PERSONA
    _PERSONA = _load_persona()
    return _PERSONA

# --- Building Quill's context ------------------------------------------------

_CONTEXT_TIMEOUT = config.CHAT_CONTEXT_TIMEOUT


def _run_with_timeout(func, timeout: float, default=None):
    result = {"value": default}

    def target():
        try:
            result["value"] = func()
        except Exception:
            result["value"] = default

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return default
    return result["value"]


def _recall_note(user_text: str) -> str | None:
    if not config.CHAT_RECALL:
        return None
    try:
        hits = _run_with_timeout(
            lambda: librarian.recall(user_text).get("chunks", []),
            _CONTEXT_TIMEOUT,
            default=[],
        )
    except Exception:
        return None
    if not hits:
        return None
    lines = "\n".join(f"- [{h.get('ddc_label', 'memory')}] {h['text']}"
                      for h in hits)
    return ("Relevant memory recalled from your Archive — real past context you "
            "can draw on naturally when it helps:\n" + lines)

def _connect_note(user_text: str) -> str | None:
    """Let Quill 'make connections': surface memories linked to this topic.

    Uses the knowledge graph to find what the current message connects to among
    already-shelved memories, so Quill can trace a thread across conversations.
    Kept cheap (one anchor + its neighbours) and fully best-effort.
    """
    if not config.CHAT_CONNECT:
        return None
    try:
        linked = _run_with_timeout(
            lambda: graph.connections(user_text, k=3).get("connections", []),
            _CONTEXT_TIMEOUT,
            default=[],
        )
    except Exception:
        return None
    if not linked:
        return None
    lines = "\n".join(f"- {c['title']} — {c['summary']}" for c in linked)
    return ("Connections from your second brain — other shelved memories this "
            "topic links to. Draw the thread only if it genuinely helps:\n"
            + lines)


def _reference_note(user_text: str) -> str | None:
    """Ground reference requests in real sources, and forbid fabrication.

    This is the anti-hallucination path. When the user asks for references or
    sources, Quill is handed the ONLY real sources available (from the indexed
    library) and told to cite strictly from them — or, if there are none, to say
    honestly that he doesn't have a reference rather than invent one. A small
    model left to answer this from memory will happily produce plausible but
    fake citations; this stops that at the prompt.
    """
    if not librarian.is_reference_request(user_text):
        return None
    topic = librarian.reference_topic(user_text)
    try:
        hits = _run_with_timeout(lambda: rag.retrieve(topic), _CONTEXT_TIMEOUT, default=[])
    except Exception:
        hits = []
    if hits:
        srcs = "\n".join(f"- [{m.get('source')}] {(d or '')[:300]}"
                         for d, m in hits)
        return (
            "The user is asking for references or sources. The ONLY real sources "
            "available are listed below, from their own indexed library. Cite "
            "ONLY these, by their source name, and only where they genuinely "
            "support the point. If they do not cover the request, say plainly "
            "that you don't have a reference for it and offer to search the web "
            "(Web mode) or to accept an uploaded document. NEVER invent a "
            "citation, author, year, title, or URL.\n\nAVAILABLE SOURCES:\n"
            + srcs)
    return (
        "The user is asking for references or sources, but nothing is indexed on "
        "this topic in their library. Do NOT invent any references, papers, "
        "authors, years, or citations. Say honestly that you don't have a "
        "reference for it, and offer to search the web (Web mode) or to let them "
        "upload a document you can cite.")


def _filed_note(triage: dict) -> str | None:
    confs = triage.get("confirmations") or []
    if not confs:
        return None
    return ("You have just filed the following into the user's Desk on their "
            "behalf: " + "; ".join(confs) + ". Acknowledge it naturally, in "
            "your own voice — not as a receipt.")

def _build(session_id: str, user_text: str, triage: dict) -> list[dict]:
    """Persona + guardrail + recalled memory + just-filed note + the live window."""
    msgs = [
        {"role": "system", "content": _GUARDRAIL},
        {"role": "system", "content": _PERSONA},
    ]
    for note in (_recall_note(user_text), _connect_note(user_text),
                 _reference_note(user_text), _filed_note(triage)):
        if note:
            msgs.append({"role": "system", "content": note})
    msgs += session.window(session_id, limit=config.CHAT_WINDOW_MAX)
    return msgs

# --- A reply -----------------------------------------------------------------

def reply(session_id: str, user_text: str) -> dict:
    """One full non-streaming turn. Returns the answer and what was filed."""
    halt = killswitch.guard()
    if halt:  # kill switch engaged: no model, no memory, no filing
        return {"answer": halt["answer"], "session": session_id,
                "filed": {}, "pending": session.pending(session_id),
                "halted": True}
    session.append(session_id, "user", user_text)
    triage = librarian.triage(user_text)
    messages = _build(session_id, user_text, triage)
    answer = (developer_actions.run(messages, llm.chat)
              if developer_actions.is_developer_request(user_text)
              else llm.chat(messages))
    session.append(session_id, "assistant", answer)
    return {"answer": answer, "session": session_id,
            "filed": triage["filed"], "pending": session.pending(session_id)}

def stream(session_id: str, user_text: str):
    """One streaming turn. Returns a generator that also records the reply.

    Triage runs before the first token so any confirmation is already in Quill's
    context; the assistant message is appended once the stream completes.
    """
    halt = killswitch.guard()
    if halt:  # kill switch engaged: emit the fixed halt and touch nothing else
        def _halted():
            yield halt["answer"]
        return _halted()
    session.append(session_id, "user", user_text)
    triage = librarian.triage(user_text)
    messages = _build(session_id, user_text, triage)

    def _gen():
        parts = []
        if developer_actions.is_developer_request(user_text):
            # Tool calls require request/response turns, so developer replies
            # are emitted after the bounded tool loop rather than token-streamed.
            answer = developer_actions.run(messages, llm.chat)
            parts.append(answer)
            yield answer
        else:
            for piece in llm.chat_stream(messages):
                parts.append(piece)
                yield piece
        session.append(session_id, "assistant", "".join(parts))

    return _gen()

def consolidate_if_full(session_id: str) -> None:
    """Background pass: consolidate the oldest turns once the buffer fills.

    The turns were already live-triaged, so this only pins references and shelves
    books — it does not re-file reminders.
    """
    if not config.AUTO_ARCHIVE:
        return
    if session.pending(session_id) < config.ARCHIVE_EVERY:
        return
    batch = session.take_batch(session_id, keep_tail=config.ARCHIVE_TAIL)
    if batch:
        librarian.consolidate(batch)
