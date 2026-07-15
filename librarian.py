"""The Librarian — the mind that sorts and finds.

The Librarian classifies and recalls; he does not store (that is the Archive) or
speak (that is Quill). Given a conversation turn he decides *what it is* and
*where it belongs*, and given a query he *finds* what is relevant.

His work splits along how time-sensitive it is:

  * TRIAGE (live) — the moment you say "remind me to message Regina tonight at
    9", the actionable part is filed straight into the Desk: a calendar event
    and a to-do. This happens at reply time so Quill can confirm it, and so a
    reminder is never five turns late.

  * CONSOLIDATE (background) — later, when a stretch of conversation is closed,
    each turn is condensed into a catalog card, classified into one of the ten
    DDC genres, and shelved in the Archive as a compressed book. Any references
    the assistant generated (a reading list) are pinned into the library here,
    since they depend on the reply and aren't time-critical.

  * RECALL — embeds a query and asks the MemFTL store for the most relevant
    cards, then enriches each with the Archive shelf it sits on.

Routing is HYBRID: model-free trigger phrases catch the obvious cases, the local
model refines the rest, and every model step has a rule fallback so the pipeline
still runs with no model. The model is reached only through `ask_fn`, mirroring
how memory.py hides its embedder behind `embed_fn`; tests replace it.
"""
import datetime
import re

import archive
import ddc
import desk
import llm
import memory
import rag

# Indirection for the model, so routing/extraction can run with no Ollama up.
ask_fn = llm.ask


# --- Trigger phrases (the high-precision, model-free half of routing) --------
_TODO_TRIGGERS = re.compile(
    r"\b(remind me|reminder|don'?t forget|note to self|make a note|"
    r"add (?:this |it )?to (?:my |the )?(?:list|todo)|to-?do|"
    r"remember to|i need to|i have to|i must)\b", re.IGNORECASE)

_WHEN_SIGNAL = re.compile(
    r"\b("
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)"
    r"|\d{1,2}:\d{2}"
    r"|at \d{1,2}\b"
    r"|tonight|today|tomorrow|tonite|noon|midnight|this evening|this morning"
    r"|next (?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|(?:on |next )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|in \d+ (?:minute|hour|day|week)s?"
    r"|on the \d{1,2}(?:st|nd|rd|th)?"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}"
    r")\b", re.IGNORECASE)

_REFERENCE_REQUEST = re.compile(
    r"\b(list|generate|give me|find|compile|suggest)\b.{0,40}\b"
    r"(paper|reference|citation|book|article|source|study|studies|"
    r"reading|bibliograph|author)s?\b", re.IGNORECASE)

_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")

# Route names
CALENDAR, TODO, REFERENCE, KNOWLEDGE, DISCARD = (
    "calendar", "todo", "reference", "knowledge", "discard")


# --- Small helpers -----------------------------------------------------------

def _slug(text: str, n: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "-".join(words[:n]) or "untitled"


def _parse_json(text: str):
    """Pull the first JSON object/array out of a model reply, tolerantly."""
    if not text:
        return None
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1),
                default=-1)
    if start == -1:
        return None
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                import json
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _ask_json(prompt: str, system: str):
    try:
        raw = ask_fn(prompt, system=system)
    except Exception:
        return None
    return _parse_json(raw)


# --- DDC classification (the shelf) -----------------------------------------

def classify_ddc(text: str) -> tuple[str, str]:
    """Assign a turn to one of the ten DDC genres. Model first, keywords if not."""
    system = (
        "You are a librarian assigning a Dewey Decimal main class. Reply with "
        "ONLY a JSON object like {\"ddc\":\"500\"} using one of these hundreds: "
        + ", ".join(sorted(ddc.DDC)) + ". Pick the single best-fitting class.")
    obj = _ask_json(text[:1500], system)
    if isinstance(obj, dict) and re.search(r"\d{3}", str(obj.get("ddc", ""))):
        hundred = ddc.to_hundred(obj["ddc"])
        return hundred, ddc.label(hundred)
    return ddc.classify_keywords(text)


# --- Routing (what was this turn?) -------------------------------------------

def detect_routes(user_text: str, assistant_text: str = "") -> set[str]:
    """Cheap, model-free first pass: which routes clearly apply to this turn."""
    routes: set[str] = set()
    u = user_text or ""
    if _TODO_TRIGGERS.search(u):
        routes.add(TODO)
        if _WHEN_SIGNAL.search(u):
            routes.add(CALENDAR)
    if _REFERENCE_REQUEST.search(u) or _looks_like_list(assistant_text):
        routes.add(REFERENCE)
    return routes


def _looks_like_list(text: str) -> bool:
    items = [ln for ln in (text or "").splitlines() if _LIST_ITEM.match(ln)]
    return len(items) >= 3


def _classify_route(user_text: str, assistant_text: str) -> str:
    """Model-backed keep/discard for turns the triggers didn't catch."""
    import config
    system = (
        "Classify this conversation turn for a personal memory archive. Reply "
        "ONLY with JSON {\"route\":\"knowledge\"} or {\"route\":\"discard\"}. "
        "Use 'discard' only for greetings, small talk, or content with no "
        "lasting value; use 'knowledge' for anything worth remembering.")
    obj = _ask_json(f"USER: {user_text}\nASSISTANT: {assistant_text}"[:2000],
                    system)
    if isinstance(obj, dict) and obj.get("route") in (KNOWLEDGE, DISCARD):
        return obj["route"]
    return KNOWLEDGE if len((user_text or "") + (assistant_text or "")) \
        >= config.ARCHIVE_MIN_CHARS else DISCARD


# --- Extraction (structured slots per route) ---------------------------------

def _now() -> datetime.datetime:
    return datetime.datetime.now()


def extract_calendar(user_text: str, when_hint: str = "") -> dict | None:
    """Pull {title, start, end} for a calendar event, resolving natural time."""
    now = _now()
    system = (
        "Extract a calendar event from the user's message. The current date and "
        f"time is {now.isoformat(timespec='minutes')}. Reply ONLY with JSON: "
        "{\"title\":\"...\",\"start\":\"YYYY-MM-DDTHH:MM\",\"end\":"
        "\"YYYY-MM-DDTHH:MM\"}. Resolve relative times like 'tonight at 9' "
        "against the current time. If there is no end, repeat start.")
    obj = _ask_json(user_text[:1000], system)
    title, start = None, None
    if isinstance(obj, dict):
        title = (obj.get("title") or "").strip() or None
        start = _valid_iso(obj.get("start"))
    if not start:
        start = _resolve_when(when_hint or user_text, now)
    if not start:
        return None
    if not title:
        title = _reminder_title(user_text)
    end = _valid_iso(obj.get("end")) if isinstance(obj, dict) else None
    return {"title": title, "start": start,
            "end": end or start, "description": user_text.strip()}


def extract_todo(user_text: str) -> dict:
    """Pull a single to-do {task, due} from the user's message."""
    task = _reminder_title(user_text)
    due = _resolve_when(user_text, _now())
    return {"task": task, "due": due}


def extract_references(user_text: str, assistant_text: str) -> list[dict]:
    """Pull individual works {title, authors, year, kind} from a generated list."""
    system = (
        "The assistant produced a list of works (papers, books, or articles). "
        "Extract each as JSON in an array: [{\"title\":\"...\",\"authors\":"
        "\"...\",\"year\":\"...\",\"kind\":\"paper|book|article\"}]. Titles are "
        "required; leave other fields empty if unknown. Reply ONLY with the "
        "JSON array.")
    obj = _ask_json(assistant_text[:4000], system)
    items: list[dict] = []
    if isinstance(obj, list):
        for it in obj:
            if isinstance(it, dict) and (it.get("title") or "").strip():
                items.append({
                    "title": it["title"].strip(),
                    "authors": (it.get("authors") or "").strip(),
                    "year": str(it.get("year") or "").strip(),
                    "kind": (it.get("kind") or "reference").strip(),
                })
    if not items:
        items = _references_from_lines(assistant_text)
    return items


def _references_from_lines(text: str) -> list[dict]:
    out = []
    for ln in (text or "").splitlines():
        if _LIST_ITEM.match(ln):
            title = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).strip()
            title = title.strip("*_` ")
            if title:
                out.append({"title": title, "authors": "", "year": "",
                            "kind": "reference"})
    return out


def distill(text: str) -> dict:
    """Pull out what's worth *keeping* from a passage: terse notes + any events.

    This is what the UI's "keep" action uses instead of saving a whole message.
    Rather than storing the raw text, the model rewrites the salient points as
    short standalone note lines and lifts out anything with a date/time as a
    calendar event. Everything has a model-free fallback so it still does
    something useful with no Ollama up.
    """
    text = (text or "").strip()
    if not text:
        return {"notes": [], "events": []}
    now = _now()
    system = (
        "From the passage, extract only what is worth keeping. Reply ONLY with "
        'JSON: {"notes":["short rewritten point"],"events":[{"title":"...",'
        '"start":"YYYY-MM-DDTHH:MM","end":"YYYY-MM-DDTHH:MM"}]}. The current '
        f"date and time is {now}. Rewrite each note as a terse, standalone line "
        "(no 'the user said', no pleasantries). Lift anything with a date or "
        "time into events and resolve relative times like 'tomorrow at 3'. Use "
        "empty arrays if there is nothing worth keeping.")
    obj = _ask_json(text[:3000], system)
    notes: list[str] = []
    events: list[dict] = []
    if isinstance(obj, dict):
        for n in obj.get("notes") or []:
            if isinstance(n, str) and n.strip():
                notes.append(n.strip())
        for ev in obj.get("events") or []:
            if isinstance(ev, dict) and (ev.get("title") or "").strip():
                start = _valid_iso(ev.get("start"))
                if start:
                    events.append({"title": ev["title"].strip(), "start": start,
                                   "end": _valid_iso(ev.get("end")) or start,
                                   "description": ""})
    # Fallback: no model (or it gave nothing usable) — summarise into one note
    # and try the rule-based calendar extractor for a date.
    if not notes and not events:
        summary = _summarise(text, "")
        if summary:
            notes.append(summary)
        cal = extract_calendar(text)
        if cal:
            events.append(cal)
    return {"notes": notes, "events": events}


def _reminder_title(text: str) -> str:
    """Strip a leading trigger phrase to get the bare task."""
    t = (text or "").strip()
    t = re.sub(r"^\s*(?:please\s+)?(?:remind me to|remind me|remember to|"
               r"note to self:?|make a note to|don'?t forget to|"
               r"i need to|i have to|i must|add(?: this)? to (?:my |the )?"
               r"(?:list|todo):?)\s*", "", t, flags=re.IGNORECASE)
    return (t[:120].strip() or text.strip()[:120]) or "Reminder"


# --- Natural-time resolution (fallback when the model doesn't give ISO) -------

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]


def _valid_iso(s) -> str | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.datetime.fromisoformat(s.strip()).isoformat(
            timespec="minutes")
    except ValueError:
        return None


def _parse_clock(text: str, default_hour: int) -> tuple[int, int]:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3).lower() == "pm":
            hour += 12
        return hour, int(m.group(2) or 0)
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\bat (\d{1,2})\b", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        if 1 <= hour <= 11 and re.search(r"tonight|evening|pm|night", text, re.I):
            hour += 12
        return hour, 0
    if re.search(r"\bnoon\b", text, re.IGNORECASE):
        return 12, 0
    if re.search(r"\bmidnight\b", text, re.IGNORECASE):
        return 0, 0
    return default_hour, 0


def _resolve_when(text: str, now: datetime.datetime) -> str | None:
    """Best-effort resolve a natural-language time to an ISO datetime string."""
    if not text:
        return None
    low = text.lower()
    hour, minute = _parse_clock(low, default_hour=9)

    def at(day: datetime.date) -> str:
        return datetime.datetime(day.year, day.month, day.day, hour, minute
                                 ).isoformat(timespec="minutes")

    m = re.search(r"in (\d+) (minute|hour|day|week)s?", low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"minute": datetime.timedelta(minutes=n),
                 "hour": datetime.timedelta(hours=n),
                 "day": datetime.timedelta(days=n),
                 "week": datetime.timedelta(weeks=n)}[unit]
        return (now + delta).isoformat(timespec="minutes")
    if "tomorrow" in low:
        return at((now + datetime.timedelta(days=1)).date())
    if "tonight" in low or "today" in low or "this evening" in low \
            or "this morning" in low:
        return at(now.date())
    for i, name in enumerate(_WEEKDAYS):
        if name in low:
            ahead = (i - now.weekday()) % 7
            if ahead == 0 and "next" in low:
                ahead = 7
            return at((now + datetime.timedelta(
                days=ahead or (7 if "next" in low else 0))).date())
    if re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|at \d{1,2}|"
                 r"noon|midnight)\b", low):
        cand = datetime.datetime(now.year, now.month, now.day, hour, minute)
        if cand <= now:
            cand += datetime.timedelta(days=1)
        return cand.isoformat(timespec="minutes")
    return None


# --- Condensing --------------------------------------------------------------

def _summarise(user_text: str, assistant_text: str) -> str:
    """Condense a turn into a dense catalog card (a few sentences)."""
    system = (
        "Summarise this conversation turn in 1-3 sentences for a memory index. "
        "Capture the specific facts, names, and conclusions; drop pleasantries. "
        "Write plain text, no preamble.")
    try:
        out = ask_fn(f"USER: {user_text}\nASSISTANT: {assistant_text}"[:4000],
                     system=system)
    except Exception:
        out = ""
    out = (out or "").strip()
    if out:
        return out
    head = " ".join((assistant_text or "").split())[:300]
    return (f"{user_text.strip()[:200]} — {head}").strip(" —")


def _title_for(user_text: str, assistant_text: str) -> str:
    t = " ".join((user_text or assistant_text or "untitled").split())
    t = re.sub(r"^\s*(?:please\s+|can you\s+|could you\s+)", "", t,
               flags=re.IGNORECASE)
    return t[:80].strip() or "untitled"


def _key_terms(text: str, n: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", (text or "").lower())
    stop = {"user", "assistant", "this", "that", "with", "from", "have", "will",
            "would", "could", "should", "about", "there", "their", "your",
            "what", "when", "which", "them", "they", "please", "remind"}
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: kv[1],
                                 reverse=True)[:n]]


# --- Filing into the Desk / library ------------------------------------------

def _file_calendar(user_text: str) -> dict | None:
    ev = extract_calendar(user_text)
    if not ev:
        return None
    created = desk.create_event(ev["title"], ev["start"], ev["end"],
                                ev["description"])
    return {"event_id": created["id"], "title": ev["title"], "start": ev["start"]}


def _file_todo(user_text: str) -> dict:
    td = extract_todo(user_text)
    body = f"- [ ] {td['task']}"
    if td.get("due"):
        body += f"  (due {td['due']})"
    note = desk.create_note(title=td["task"][:60], body=body,
                            tags=["todo", "reminder"])
    return {"note_id": note["id"], "task": td["task"], "due": td.get("due")}


def _file_references(user_text: str, assistant_text: str) -> dict | None:
    """Shelve ONLY references we can ground; skip the model's unverifiable guesses.

    A citation is pinned into the library only if it carries a real URL (web
    provenance) or its title matches something already indexed (library
    provenance). Anything the model merely asserted — the classic hallucinated
    paper — is dropped rather than filed as if it were real. Each kept reference
    is tagged with where it came from.
    """
    items = extract_references(user_text, assistant_text)
    if not items:
        return None
    topic = _reference_topic(user_text)
    # Ground against what's actually indexed. Best-effort: no model ⇒ no corpus ⇒
    # only URL-bearing (web) references survive, which is the safe direction.
    try:
        corpus = " ".join((d or "").lower() for d, _ in rag.retrieve(topic))
    except Exception:
        corpus = ""
    added = []
    for it in items:
        prov = _reference_provenance(it, corpus)
        if prov == "unverified":
            continue  # a fabricated-looking citation — do NOT shelve it
        cite = it["title"]
        if it.get("authors"):
            cite += f" — {it['authors']}"
        if it.get("year"):
            cite += f" ({it['year']})"
        cite += f"  [{prov}]"
        rag.add_document(cite, source=f"reference:{it['title'][:80]}")
        added.append(cite)
    skipped = len(items) - len(added)
    if not added:
        return {"skipped_unverified": skipped, "topic": topic}
    note = desk.create_note(
        title=f"References: {topic}"[:60],
        body="\n".join(f"- {c}" for c in added),
        tags=["references", "library"])
    return {"note_id": note["id"], "count": len(added),
            "skipped_unverified": skipped, "topic": topic}


def _reference_provenance(item: dict, corpus: str) -> str:
    """Where a citation is grounded: 'web' (real URL), 'library' (matches an
    indexed source), or 'unverified' (only the model's word for it)."""
    title = item.get("title") or ""
    if re.search(r"https?://", title):
        return "web"
    tokens = re.findall(r"[a-z0-9]{4,}", title.lower())
    if not corpus or not tokens:
        return "unverified"
    hits = sum(1 for w in tokens if w in corpus)
    return "library" if hits / len(tokens) >= 0.6 else "unverified"


def _reference_topic(text: str) -> str:
    t = re.sub(r"^.*?\b(?:on|about|for|of)\b\s*", "", (text or "").strip(),
               flags=re.IGNORECASE)
    return (t[:60].strip(" .?!") or "list") if t else "list"


# Public front doors for the reference grounding path (used by quill.py).
def is_reference_request(text: str) -> bool:
    """True if the user is asking for references/sources/citations."""
    return bool(_REFERENCE_REQUEST.search(text or ""))


def reference_topic(text: str) -> str:
    """The topic of a reference request, for grounding retrieval."""
    return _reference_topic(text)


# --- The three operations: triage, consolidate, recall -----------------------

def triage(user_text: str) -> dict:
    """LIVE routing of the user's message: file time-sensitive actions now.

    Only calendar + to-do, because they live entirely in what the user just said
    and must not be filed late. References and shelving wait for consolidation.
    Returns what was filed plus short human confirmations Quill can echo.
    """
    user_text = (user_text or "").strip()
    routes = detect_routes(user_text)
    filed: dict = {}
    confirmations: list[str] = []
    if CALENDAR in routes:
        cal = _file_calendar(user_text)
        if cal:
            filed[CALENDAR] = cal
            when = cal["start"].replace("T", " ")
            confirmations.append(f"calendar event '{cal['title']}' at {when}")
    if TODO in routes:
        td = _file_todo(user_text)
        filed[TODO] = td
        confirmations.append(f"a to-do: {td['task']}")
    return {"filed": filed, "confirmations": confirmations}


def consolidate_turn(user_text: str, assistant_text: str = "",
                     route_actions: bool = False) -> dict:
    """BACKGROUND consolidation of one closed turn.

    Pins any references the assistant generated, then condenses + classifies +
    shelves the turn as a compressed book (unless it's routeless chit-chat).
    Does NOT re-file calendar/to-do — triage() already did that live. Pass
    route_actions=True to also file calendar/to-do (used when a turn is archived
    without having gone through live triage, e.g. an imported conversation).
    """
    user_text = (user_text or "").strip()
    assistant_text = (assistant_text or "").strip()
    routes: set[str] = set()
    fired: dict = {}

    if route_actions:
        t = triage(user_text)
        if t["filed"]:
            routes.update(t["filed"].keys())
            fired.update(t["filed"])

    if REFERENCE in detect_routes(user_text, assistant_text):
        ref = _file_references(user_text, assistant_text)
        if ref:
            routes.add(REFERENCE)
            fired[REFERENCE] = ref

    keep = bool(fired)
    if not keep:
        if _classify_route(user_text, assistant_text) == DISCARD:
            return {"routes": [], "shelved": False, "reason": "discarded"}
        keep = True

    book = _shelve(user_text, assistant_text, routes)
    return {"routes": sorted(routes) or [KNOWLEDGE], "shelved": True,
            "fired": fired, **book}


def _shelve(user_text: str, assistant_text: str, routes: set[str]) -> dict:
    """Condense -> classify -> write the card (MemFTL) + cold book (Archive)."""
    raw = f"USER: {user_text}\nASSISTANT: {assistant_text}".strip()
    ddc_code, _ = classify_ddc(raw)
    summary = _summarise(user_text, assistant_text)
    title = _title_for(user_text, assistant_text)
    terms = _key_terms(raw)
    logical_key = f"{ddc_code}:{_slug(title)}"
    # Warm card -> the MEASURED MemFTL store (semantic recall + amplification).
    memory.remember(logical_key, summary)
    # Cold book -> the Archive.
    return archive.store_book(logical_key, ddc_code, title, terms, summary,
                              raw, sorted(routes))


def archive_turn(user_text: str, assistant_text: str = "") -> dict:
    """Full routing of a standalone turn: triage actions + consolidate.

    For conversations that did NOT go through Quill's live loop (the /archive
    endpoints, imports). The live loop instead calls triage() then, later,
    consolidate_turn().
    """
    return consolidate_turn(user_text, assistant_text, route_actions=True)


def consolidate(messages: list[dict]) -> dict:
    """Consolidate a batch of already-live-triaged turns (references + shelve)."""
    return _walk(messages, route_actions=False)


def archive_conversation(messages: list[dict]) -> dict:
    """Full-route a whole conversation that never went through the live loop."""
    return _walk(messages, route_actions=True)


def _walk(messages: list[dict], route_actions: bool) -> dict:
    turns, i = [], 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "user":
            reply = ""
            if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                reply = messages[i + 1].get("content", "")
                i += 1
            turns.append((msg.get("content", ""), reply))
        i += 1
    results = [consolidate_turn(u, a, route_actions=route_actions)
               for u, a in turns]
    shelved = [r for r in results if r.get("shelved")]
    return {"turns": len(turns), "shelved": len(shelved),
            "discarded": len(results) - len(shelved), "results": results}


def recall(query: str, k: int | None = None) -> dict:
    """Semantic recall over the warm cards, enriched with their Archive shelf."""
    hit = memory.recall(query, k)
    by_key = archive.books_by_key()
    for ch in hit.get("chunks", []):
        info = by_key.get(ch.get("logical_key"))
        if info:
            ch.update(info)
    return hit


# Convenience re-exports so callers (and the API) have one front door for the
# Archive's read side without importing it directly.
def browse_shelf(ddc_code: str | None = None) -> dict:
    return archive.browse_shelf(ddc_code)


def shelves() -> list[dict]:
    return archive.shelves()


def open_book(book_id: str) -> dict | None:
    return archive.open_book(book_id)
