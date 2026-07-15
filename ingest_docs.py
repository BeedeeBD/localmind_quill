"""Index a folder of documents into the private knowledge base.

Usage (from the project folder):
    python ingest_docs.py "C:/path/to/documents"

Supports .txt, .md, and .pdf files — point it at any folder of material the
assistant should be able to reason over.
"""
import sys
from pathlib import Path

import rag

SUPPORTED = {".txt", ".md", ".pdf"}


def read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def main(folder: str):
    root = Path(folder)
    if not root.exists():
        sys.exit(f"Folder not found: {root}")

    files = [p for p in root.rglob("*") if p.suffix.lower() in SUPPORTED]
    if not files:
        sys.exit(f"No .txt/.md/.pdf files found under {root}")

    total_chunks = 0
    for path in files:
        try:
            text = read_file(path)
            n = rag.add_document(text, source=path.name)
            total_chunks += n
            print(f"  indexed {path.name}: {n} chunks")
        except Exception as e:  # one bad PDF shouldn't sink the whole batch
            print(f"  SKIPPED {path.name}: {e}")

    print(f"\nDone. {len(files)} files, {total_chunks} chunks in the knowledge base.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python ingest_docs.py <folder>")
    main(sys.argv[1])
