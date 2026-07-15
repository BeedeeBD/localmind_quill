"""Retrieval layer — answers come from your documents, not guesswork.

Each document is split into chunks, every chunk is embedded locally, and the
vectors are kept in ChromaDB on disk. At question time the chunks closest to the
query are pasted in front of the model as context. The model is never retrained
or fine-tuned — the documents stay in a local database that can be deleted at
any time.
"""
from typing import List
import uuid

import chromadb
from chromadb.config import Settings

import config
import llm

# anonymized_telemetry=False is important: ChromaDB otherwise phones home with
# anonymous usage stats by default, which quietly breaks the "no telemetry"
# promise. This keeps it fully silent — nothing leaves the machine.
_client = chromadb.PersistentClient(
    path=str(config.CHROMA_DIR),
    settings=Settings(anonymized_telemetry=False),
)
_collection = _client.get_or_create_collection("documents")
# A separate index just for Quill notes, so the librarian can search everything
# you've saved by meaning, not just keyword. Quill (quill.py) keeps the real
# copy in SQLite; this is only a search mirror it can rebuild any time.
_notes = _client.get_or_create_collection("quill_notes")


def chunk_text(text: str) -> List[str]:
    """Cut text into overlapping windows.

    The overlap matters: without it, a chunk boundary can slice a sentence in
    half and neither piece makes sense on its own. Sharing a bit between
    neighbours means an idea that straddles a boundary still survives whole.
    """
    size, overlap = config.CHUNK_SIZE, config.CHUNK_OVERLAP
    chunks, start = [], 0
    text = text.strip()
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return [c for c in chunks if c.strip()]


def add_document(text: str, source: str) -> int:
    """Chunk, embed, and store one document. Returns number of chunks added."""
    chunks = chunk_text(text)
    if not chunks:
        return 0
    embeddings = [llm.embed(c) for c in chunks]
    _collection.add(
        ids=[str(uuid.uuid4()) for _ in chunks],
        documents=chunks,
        embeddings=embeddings,
        metadatas=[{"source": source, "chunk": i} for i in range(len(chunks))],
    )
    return len(chunks)


def list_sources() -> list[dict]:
    """Distinct document sources in the knowledge base, with chunk counts."""
    got = _collection.get(include=["metadatas"])
    counts: dict[str, int] = {}
    for m in got.get("metadatas") or []:
        src = m.get("source") or "(unknown)"
        counts[src] = counts.get(src, 0) + 1
    return [{"source": s, "chunks": n}
            for s, n in sorted(counts.items())]


def retrieve(query: str, k: int | None = None):
    """Return the top-k most relevant chunks for a query."""
    k = k or config.TOP_K
    result = _collection.query(query_embeddings=[llm.embed(query)], n_results=k)
    docs = result["documents"][0]
    metas = result["metadatas"][0]
    return list(zip(docs, metas))


def answer(query: str) -> dict:
    """Retrieve context, then answer the query grounded in it."""
    hits = retrieve(query)
    if not hits:
        return {"answer": llm.ask(query), "sources": []}

    context = "\n\n---\n\n".join(
        f"[Source: {m.get('source')}]\n{d}" for d, m in hits
    )
    # Keep the model honest: force it to stick to the supplied context and to
    # admit when the answer isn't there, rather than inventing something that
    # sounds right. Citing sources makes each answer auditable.
    system = (
        "You are a careful research assistant. Answer using ONLY the context "
        "below. If the context does not contain the answer, say so plainly. "
        "Cite the source names you used.\n\nCONTEXT:\n" + context
    )
    reply = llm.ask(query, system=system)
    sources = sorted({m.get("source") for _, m in hits})
    return {"answer": reply, "sources": sources}


# --- Quill note index -------------------------------------------------------
# These keep the "quill_notes" search mirror in sync with SQLite. Called by
# quill.py whenever a note is created, edited, or deleted.

def index_note(note_id: str, text: str, title: str) -> None:
    """Embed one note so it's semantically searchable. Idempotent (re-run to update)."""
    remove_note(note_id)  # drop any old version first so edits don't duplicate
    text = (text or "").strip()
    if not text:
        return
    _notes.add(
        ids=[str(note_id)],
        documents=[text[:6000]],                 # cap: a note is usually short
        embeddings=[llm.embed(text[:6000])],
        metadatas=[{"title": title or "(untitled)", "note_id": str(note_id)}],
    )


def remove_note(note_id: str) -> None:
    """Forget a note from the search mirror (safe to call even if absent)."""
    try:
        _notes.delete(ids=[str(note_id)])
    except Exception:
        pass


def search_notes(query: str, k: int = 5):
    """Return the notes most relevant to a query, as (text, metadata) pairs."""
    if _notes.count() == 0:
        return []
    result = _notes.query(
        query_embeddings=[llm.embed(query)],
        n_results=min(k, _notes.count()),
    )
    return list(zip(result["documents"][0], result["metadatas"][0]))
