"""Approval-gated developer actions for Quill.

The model never receives filesystem access.  In developer conversations it can
ask this small broker to read an allowed file, search allowed source text, or
stage a guarded proposal.  ``selfcode`` remains the sole write path and every
proposal still needs a human approval in the Dev tab.
"""
from __future__ import annotations

import json
import re

import config
import selfcode


_ACTION_RE = re.compile(r"<quill-action>\s*(\{.*?\})\s*</quill-action>", re.S)
_MAX_ACTIONS = 6


def is_developer_request(text: str) -> bool:
    """This build does not support self-editing or developer actions."""
    return False


def architecture_context() -> str:
    """Explain that self-editing is disabled in this build."""
    return (
        "Self-editing is disabled in this build. Quill may answer normally, "
        "but he cannot inspect or propose edits to his own source code."
    )


def _search_code(query: str) -> dict:
    query = (query or "").strip().lower()
    if not query:
        return {"error": "search query is empty"}
    hits = []
    for item in selfcode.list_files().get("files", []):
        if item["hidden"]:
            continue
        read = selfcode.read_file(item["path"])
        text = read.get("content")
        if text is None:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if query in line.lower():
                hits.append({"path": item["path"], "line": line_no,
                             "text": line[:300]})
                if len(hits) >= 30:
                    return {"query": query, "hits": hits}
    return {"query": query, "hits": hits}


def execute(marker: str) -> dict:
    """Validate a model action and return only safe, structured tool output."""
    try:
        action = json.loads(marker)
    except json.JSONDecodeError:
        return {"error": "invalid action JSON"}
    kind = action.get("type")
    if kind == "read_file":
        return selfcode.read_file(str(action.get("path", "")))
    if kind == "search_code":
        return _search_code(str(action.get("query", "")))
    if kind == "propose_edit":
        path, content = action.get("path"), action.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            return {"error": "propose_edit requires string path and content"}
        return selfcode.propose_write(path, content, str(action.get("note", "")))
    return {"error": f"unsupported developer action: {kind!r}"}


def run(messages: list[dict], chat) -> str:
    """Run a bounded inspect/propose loop, returning Quill's final user reply."""
    conversation = [*messages, {"role": "system", "content": architecture_context()}]
    for _ in range(_MAX_ACTIONS):
        response = chat(conversation)
        match = _ACTION_RE.search(response)
        if not match:
            return response
        result = execute(match.group(1))
        conversation.extend((
            {"role": "assistant", "content": response},
            {"role": "system", "content": "Developer tool result:\n" +
             json.dumps(result, ensure_ascii=False) +
             "\nContinue: use another action marker or give the final answer."},
        ))
    return ("I reached the safety limit of six developer actions without a final "
            "response. Please narrow the requested change and try again.")
