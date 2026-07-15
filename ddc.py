"""Dewey Decimal genres — the shared vocabulary of the shelves.

The ten DDC main classes are the "ten wider genres" every archived memory is
filed under. This data lives in its own module because two very different parts
of the system need it: the Librarian (to *classify* a turn into a genre) and the
Archive (to *label and browse* the shelves). Keeping it here avoids a circular
import between those two.

`classify_keywords()` is the pure, model-free fallback classifier. The
model-backed classifier that normally does the job lives in librarian.py.
"""

# code -> (human label, keyword hints used by the rule-based fallback classifier)
DDC = {
    "000": ("General knowledge, computing & information", (
        "computer", "software", "program", "code", "data", "algorithm",
        "internet", "encyclopedia", "information", "library")),
    "100": ("Philosophy & psychology", (
        "philosophy", "psychology", "ethics", "logic", "mind", "consciousness",
        "emotion", "meaning", "metaphysics")),
    "200": ("Religion", (
        "religion", "god", "faith", "church", "bible", "spiritual", "prayer",
        "theology", "buddhis", "islam", "christian")),
    "300": ("Social sciences", (
        "society", "politic", "econom", "law", "education", "government",
        "culture", "social", "money", "finance", "war")),
    "400": ("Language", (
        "language", "grammar", "linguistic", "translation", "vocabulary",
        "dialect", "etymology", "syntax")),
    "500": ("Science & mathematics", (
        "science", "physic", "chemistr", "biolog", "math", "astronomy",
        "whale", "animal", "species", "geolog", "quantum", "evolution")),
    "600": ("Technology & applied science", (
        "engineering", "medicine", "health", "manufactur", "business",
        "agricultur", "machine", "device", "cooking", "medical")),
    "700": ("Arts & recreation", (
        "art", "music", "song", "paint", "film", "photo", "sport", "game",
        "design", "architecture", "dance")),
    "800": ("Literature", (
        "novel", "poem", "poetry", "fiction", "literature", "story", "essay",
        "play", "author", "writing")),
    "900": ("History & geography", (
        "history", "geography", "travel", "biography", "war", "ancient",
        "country", "map", "empire", "century")),
}
UNSORTED = "000"  # where things land when nothing else fits


def label(code: str) -> str:
    """Human name for a DDC hundred, or the general-works label if unknown."""
    return DDC.get(code, DDC[UNSORTED])[0]


def to_hundred(code: str) -> str:
    """Snap any DDC-ish string ('599', '782.4') to its main class ('500', '700')."""
    import re
    m = re.search(r"\d{3}", str(code) or "")
    if m:
        hundred = f"{int(m.group()) // 100 * 100:03d}"
        if hundred in DDC:
            return hundred
    return UNSORTED


def classify_keywords(text: str) -> tuple[str, str]:
    """Model-free classifier: vote by how often each class's hint words appear."""
    low = (text or "").lower()
    best, best_score = UNSORTED, 0
    for code, (_, hints) in DDC.items():
        score = sum(low.count(h) for h in hints)
        if score > best_score:
            best, best_score = code, score
    return best, DDC[best][0]
