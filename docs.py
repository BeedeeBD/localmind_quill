"""Document upload — drop a file in, and Quill can reason over it.

Thin wrapper over the RAG layer (rag.py): decode the bytes into text and hand
them to the same chunk-embed-store pipeline that ingest_docs.py uses for folders.
Nothing here leaves the machine — the file is chunked, embedded locally, and kept
in the on-disk vector store, exactly like documents indexed from a folder.

Supported: .txt, .md, .pdf. A .pdf is read with pypdf, in memory, so nothing is
written to a temp file.
"""
from __future__ import annotations

import io
from pathlib import Path

import rag

SUPPORTED = {".txt", ".md", ".pdf"}


def _extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return data.decode("utf-8", errors="ignore")


def ingest_upload(filename: str, data: bytes) -> dict:
    """Index one uploaded file. Returns how many chunks it became, or an error."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        return {"error": f"unsupported type '{suffix or '?'}'. "
                         f"Use {', '.join(sorted(SUPPORTED))}."}
    try:
        text = _extract_text(filename, data)
    except Exception as e:  # a malformed PDF shouldn't 500 the server
        return {"error": f"could not read '{filename}': {e}"}
    if not text.strip():
        return {"error": f"'{filename}' had no extractable text"}
    n = rag.add_document(text, source=filename)
    return {"filename": filename, "chunks": n}


def list_documents() -> dict:
    return {"documents": rag.list_sources()}
