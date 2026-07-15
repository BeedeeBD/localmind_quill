"""The single door everything else knocks on.

Keeping it all behind one small HTTP server is the whole point: VS Code, curl,
a browser, or a future chat UI all speak to the same handful of endpoints, and
none of them need to know how the model or the vector DB actually work.

Run it with:
    uvicorn app:app --host 127.0.0.1 --port 8000

Endpoints:
    POST /chat       {"prompt": "..."}           -> plain local-model answer
    POST /rag        {"prompt": "..."}           -> answer grounded in your docs
    POST /web        {"query": "..."}            -> answer grounded in web research
    POST /web/fetch  {"url": "...", "query":""}  -> read one approved page + summarise
    POST /diagnose   {"url": "https://..."}      -> front-end code review of a page
    GET/POST/PUT/DELETE /notes[/id]              -> Desk notes
    GET/POST/PUT/DELETE /events[/id]             -> Desk calendar
    GET  /events/{id}.ics                        -> export an event for Outlook
    POST /archive        {"messages":[...]}      -> close & shelve a conversation
    POST /archive/turn   {"user":"","assistant":""} -> route + shelve one turn
    GET  /shelves                                -> the ten DDC genres + counts
    GET  /shelf?ddc=500                          -> browse spine cards on a shelf
    GET  /book/{id}                              -> open one cold book (full text)
    POST /archive/recall {"query":"..."}         -> recall cards + their shelf
    GET  /session/{id} · POST /session/{id}/flush · DELETE /session/{id}
"""
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Response, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import archive
import config
import desk
import docs
import graph
import killswitch
import librarian
import memory
import quill
import rag
import selfcode
import session
import websearch

# docs_url is moved off "/docs" so that path can serve the document library
# (GET /docs); the interactive API docs live at /apidocs instead.
app = FastAPI(title="Quill", version="0.1", docs_url="/apidocs")

STATIC_DIR = Path(__file__).parent / "static"


class Prompt(BaseModel):
    prompt: str
    # Optional conversation id, so separate chats don't interleave in the
    # buffer. Defaults to a single shared session for simple/one-off callers.
    session: str = "default"


class Url(BaseModel):
    url: str


class Query(BaseModel):
    query: str


class FetchReq(BaseModel):
    url: str
    query: str | None = None


class NoteIn(BaseModel):
    title: str = ""
    body: str = ""
    tags: list[str] = []


class EventIn(BaseModel):
    title: str = ""
    start: str = ""
    end: str = ""
    description: str = ""


class Remember(BaseModel):
    key: str
    text: str


class Recall(BaseModel):
    query: str
    k: int | None = None


class KeyReq(BaseModel):
    key: str


class Turn(BaseModel):
    user: str
    assistant: str = ""


class Conversation(BaseModel):
    messages: list[dict]


class GraphQuery(BaseModel):
    query: str
    k: int | None = None


class ProposeIn(BaseModel):
    path: str
    content: str
    note: str = ""


@app.get("/", response_class=HTMLResponse)
def home():
    """Serve the single-page chat UI — just open http://127.0.0.1:8000."""
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health():
    # Report *that* Quill is halted so a UI can show it — never where the switch
    # lives (killswitch.py keeps the path off every endpoint).
    return {"status": "ok", "halted": killswitch.engaged()}


@app.post("/killswitch/engage")
def killswitch_engage():
    """Human-operated STOP from the UI's END button. Quill cannot call this."""
    return killswitch.engage()


@app.post("/killswitch/release")
def killswitch_release():
    """Human-operated resume."""
    return killswitch.release()


# --- Talking to Quill -------------------------------------------------------
# Quill owns the turn: the user's message goes into the session buffer, the
# Librarian triages it (filing any reminder straight away), Quill answers in
# character, and — after the response is sent — the buffer is consolidated into
# the Archive if it has filled. AUTO_ARCHIVE only gates that background step;
# chat is stateful either way.

@app.post("/chat")
def chat(body: Prompt, background: BackgroundTasks):
    """A full turn with Quill, non-streaming. Returns the answer + what he filed."""
    out = quill.reply(body.session, body.prompt)
    background.add_task(quill.consolidate_if_full, body.session)
    return out


@app.post("/chat/stream")
def chat_stream(body: Prompt):
    """Stream Quill's reply token-by-token so the UI feels responsive on CPU."""
    return StreamingResponse(
        quill.stream(body.session, body.prompt),
        media_type="text/plain; charset=utf-8",
        # Consolidation runs only after the stream is fully consumed and closed.
        background=BackgroundTask(quill.consolidate_if_full, body.session),
    )


@app.post("/rag")
def rag_query(body: Prompt):
    return rag.answer(body.prompt)


# --- Web research -----------------------------------------------------------

@app.post("/web")
def web(body: Query):
    """Search the web (through the hardened gate) and answer with citations."""
    return websearch.research(body.query)


@app.post("/web/fetch")
def web_fetch(body: FetchReq):
    """Read one page the user explicitly approved, and summarise it."""
    return websearch.fetch_and_summarize(body.url, body.query or "")


# --- Desk: notes ------------------------------------------------------------

@app.get("/notes")
def notes_list():
    return desk.list_notes()


@app.post("/notes")
def notes_create(n: NoteIn):
    return desk.create_note(n.title, n.body, n.tags)


@app.get("/notes/{note_id}")
def notes_get(note_id: str):
    note = desk.get_note(note_id)
    return note or Response(status_code=404)


@app.put("/notes/{note_id}")
def notes_update(note_id: str, n: NoteIn):
    note = desk.update_note(note_id, n.title, n.body, n.tags)
    return note or Response(status_code=404)


@app.delete("/notes/{note_id}")
def notes_delete(note_id: str):
    return {"deleted": desk.delete_note(note_id)}


@app.post("/notes/search")
def notes_search(body: Query):
    hits = desk.search_notes(body.query)
    return {"results": [{"title": m.get("title"), "text": d} for d, m in hits]}


@app.post("/keep")
def keep(body: Prompt):
    """Distil a passage into terse notes + calendar events, and file them.

    This backs the chat "keep" action: instead of saving a whole message, Quill
    pulls out the sentences worth keeping (rewritten short) and lifts any dated
    thing into the calendar.
    """
    d = librarian.distill(body.prompt)
    notes = [desk.create_note(title=n[:60], body=n, tags=["kept"])
             for n in d["notes"]]
    events = [desk.create_event(e["title"], e["start"], e["end"],
                                e.get("description", ""))
              for e in d["events"]]
    return {"notes": notes, "events": events}


# --- Desk: calendar ---------------------------------------------------------

@app.get("/events")
def events_list():
    return desk.list_events()


@app.post("/events")
def events_create(e: EventIn):
    return desk.create_event(e.title, e.start, e.end, e.description)


@app.put("/events/{event_id}")
def events_update(event_id: str, e: EventIn):
    ev = desk.update_event(event_id, e.title, e.start, e.end, e.description)
    return ev or Response(status_code=404)


@app.delete("/events/{event_id}")
def events_delete(event_id: str):
    return {"deleted": desk.delete_event(event_id)}


@app.get("/events/{event_id}.ics")
def events_ics(event_id: str):
    """Export one event as a .ics file — double-click it to add to Outlook."""
    event = desk.get_event(event_id)
    if not event:
        return Response(status_code=404)
    return Response(
        content=desk.export_ics(event),
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{event_id}.ics"'},
    )


# --- MemFTL: append-only semantic memory ------------------------------------

@app.post("/memory/remember")
def mem_remember(b: Remember):
    """Append a memory chunk under a logical key (append-only revise)."""
    return memory.remember(b.key, b.text)


@app.post("/memory/recall")
def mem_recall(b: Recall):
    """Load only the most relevant valid chunks for a query."""
    return memory.recall(b.query, b.k)


@app.get("/memory/history/{key}")
def mem_history(key: str):
    """Every version ever written for a key — the audit trail."""
    return memory.history(key)


@app.post("/memory/forget")
def mem_forget(b: KeyReq):
    return memory.forget(b.key)


@app.post("/memory/gc")
def mem_gc():
    """Reclaim space held by superseded chunks (v1 consolidation)."""
    return memory.garbage_collect()


@app.get("/memory/stats")
def mem_stats():
    """Chunk counts and the context-amplification instrumentation."""
    return memory.stats()


# --- Librarian + Archive: classify, shelve, browse, recall ------------------

@app.post("/archive/turn")
def archive_turn(t: Turn):
    """Fully route one standalone turn (triage + references + shelve)."""
    return librarian.archive_turn(t.user, t.assistant)


@app.post("/archive")
def archive_conversation(c: Conversation):
    """Fully route a whole conversation that didn't go through Quill's loop."""
    return librarian.archive_conversation(c.messages)


@app.get("/shelves")
def shelves():
    """The ten DDC genres and how many books sit on each."""
    return archive.shelves()


@app.get("/shelf")
def shelf(ddc: str | None = None):
    """Browse spine cards, optionally filtered to one DDC class. Opens no book."""
    return archive.browse_shelf(ddc)


@app.get("/book/{book_id}")
def book(book_id: str):
    """Open one cold book and decompress its full raw text."""
    b = archive.open_book(book_id)
    return b or Response(status_code=404)


@app.post("/archive/recall")
def archive_recall(b: Recall):
    """Semantic recall over archived cards, enriched with each book's shelf."""
    return librarian.recall(b.query, b.k)


# --- Documents: upload material for Quill to reason over --------------------

@app.post("/docs/upload")
async def docs_upload(file: UploadFile = File(...)):
    """Index one uploaded .txt/.md/.pdf into the private knowledge base."""
    data = await file.read()
    return docs.ingest_upload(file.filename or "upload", data)


@app.get("/docs")
def docs_list():
    """What's been indexed, with per-source chunk counts."""
    return docs.list_documents()


# --- Knowledge graph: the second brain --------------------------------------

@app.get("/graph")
def graph_build():
    """The whole memory graph — nodes (shelved memories) and their connections."""
    return graph.build()


@app.post("/graph/connections")
def graph_connections(b: GraphQuery):
    """Trace what a topic connects to: the nearest memory and its neighbours."""
    return graph.connections(b.query, b.k or 5)


# --- Self-code: disabled in this build --------------------------------------
# This version of Quill no longer exposes or supports self-editing routes.

# --- Session buffer (the live conversation the loop consolidates from) -------

@app.get("/session/{session_id}")
def session_get(session_id: str):
    """Inspect a session: its full message log and how many are still live."""
    return {"session": session_id, "pending": session.pending(session_id),
            "messages": session.history(session_id)}


@app.post("/session/{session_id}/flush")
def session_flush(session_id: str):
    """Force-consolidate a live session now, regardless of the threshold."""
    batch = session.take_batch(session_id, keep_tail=0)
    if not batch:
        return {"archived": 0}
    # These turns were live-triaged already, so only references + shelving remain.
    return {"archived": len(batch), **librarian.consolidate(batch)}


@app.delete("/session/{session_id}")
def session_clear(session_id: str):
    """Drop a session's live buffer (shelved books are untouched)."""
    return {"cleared": session.clear(session_id)}
