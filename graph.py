"""The knowledge graph — Quill's second brain, drawn from what he's shelved.

Nothing new is stored here. The graph is *derived* on demand from two things the
system already keeps: the Archive's books (each a shelved memory with a title, a
DDC shelf, and key terms) and the MemFTL store's embeddings (one vector per
memory). That keeps the "second brain" honest — it can only ever reflect what
Quill actually remembers, and it costs no extra writes.

  * NODES are archived memories (one per logical key / book).
  * EDGES are drawn two ways, and an edge can be both at once:
      - "topic"   — the two memories share enough key terms (Jaccard overlap).
      - "meaning" — their embeddings are close (cosine similarity), catching
                    connections that don't share vocabulary.

The whole point of a second brain is to *trace connections*: connections() and
neighbors_of_key() answer "what else does this touch?", which is what lets Quill
link what you're saying now to something he shelved weeks ago.
"""
from __future__ import annotations

import numpy as np

import archive
import config
import memory


def _norm(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _nodes() -> list[dict]:
    """One node per archived book, newest first, capped for a readable graph."""
    books = archive.browse_shelf().get("books", [])[: config.GRAPH_MAX_NODES]
    nodes = []
    for b in books:
        nodes.append({
            "key": b["logical_key"],
            "book_id": b["book_id"],
            "label": b["title"],
            "ddc": b["ddc"],
            "ddc_label": b["ddc_label"],
            "terms": [t.lower() for t in b.get("terms", [])],
            "summary": b.get("summary", ""),
        })
    return nodes


def _edges(nodes: list[dict]) -> list[dict]:
    vecs = memory.vectors_by_key()
    prepared = []
    for n in nodes:
        v = vecs.get(n["key"])
        prepared.append((n, set(n["terms"]),
                         _norm(np.asarray(v, dtype=float)) if v else None))

    edges = []
    for i in range(len(prepared)):
        n_i, terms_i, v_i = prepared[i]
        for j in range(i + 1, len(prepared)):
            n_j, terms_j, v_j = prepared[j]
            jac = _jaccard(terms_i, terms_j)
            cos = float(np.dot(v_i, v_j)) if v_i is not None and v_j is not None \
                else 0.0
            topic = jac >= config.GRAPH_TERM_JACCARD
            meaning = cos >= config.GRAPH_SEMANTIC_MIN
            if not (topic or meaning):
                continue
            kind = "both" if topic and meaning else ("topic" if topic else "meaning")
            edges.append({
                "source": n_i["key"], "target": n_j["key"],
                "kind": kind, "weight": round(max(jac, cos), 3),
                "shared_terms": sorted(terms_i & terms_j)[:6],
            })
    return edges


def build() -> dict:
    """The whole graph: nodes, edges, and a little shape summary."""
    nodes = _nodes()
    edges = _edges(nodes)
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    for n in nodes:
        n["degree"] = degree.get(n["key"], 0)
    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "isolated": sum(1 for n in nodes if n["degree"] == 0),
        },
    }


def neighbors_of_key(logical_key: str, k: int = 5) -> list[dict]:
    """The memories most connected to one node — cheap, single-vector compare.

    Used live in chat (quill.py) so a recalled memory can pull its neighbours in
    without building the whole graph on every turn.
    """
    vecs = memory.vectors_by_key()
    base = vecs.get(logical_key)
    if base is None:
        return []
    vb = _norm(np.asarray(base, dtype=float))
    by_key = {b["logical_key"]: b for b in archive.browse_shelf().get("books", [])}
    base_terms = set(t.lower() for t in by_key.get(logical_key, {}).get("terms", []))

    scored = []
    for key, v in vecs.items():
        if key == logical_key:
            continue
        cos = float(np.dot(vb, _norm(np.asarray(v, dtype=float))))
        info = by_key.get(key, {})
        jac = _jaccard(base_terms, set(t.lower() for t in info.get("terms", [])))
        score = max(cos, jac)
        if score >= min(config.GRAPH_SEMANTIC_MIN, 0.5) or jac >= config.GRAPH_TERM_JACCARD:
            scored.append((score, key, info))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"key": key, "title": info.get("title", key),
             "ddc_label": info.get("ddc_label", ""),
             "summary": info.get("summary", ""), "score": round(s, 3)}
            for s, key, info in scored[:k]]


def connections(query: str, k: int = 5) -> dict:
    """Trace connections from a free-text query: find the nearest memory, then
    return what it links to. This is the 'make a connection' entry point."""
    hit = memory.recall(query, 1)
    chunks = hit.get("chunks", [])
    if not chunks:
        return {"anchor": None, "connections": []}
    anchor_key = chunks[0]["logical_key"]
    return {"anchor": anchor_key,
            "connections": neighbors_of_key(anchor_key, k)}
