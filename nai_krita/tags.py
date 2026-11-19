"""Tag autocomplete core: the tag index, search, and editing the prompt text. Krita-free.

The tag list (data/tags.tsv.gz, built by tools/build_tags.py from Danbooru's public
API) has one line per tag: name, category, post count, aliases ("|"-separated),
already in NovelAI form (spaces, not underscores) and sorted most-used first.

Search matches the start of a tag ("long h" → long hair), the start of an alias
("twin tails" → twintails), and the start of any later word in a tag ("hair" →
long hair, after the direct matches). Results are ranked by match kind, then by
post count.
"""
import bisect
import gzip
import os
import re
import threading
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tags.tsv.gz")
MIN_QUERY = 2
MAX_RESULTS = 12
WORD_WEIGHT = 0.1   # a match on a later word ranks as if a tenth as popular

# match kinds, best first
EXACT, PREFIX, ALIAS, WORD = 0, 1, 2, 3

SEPARATORS = ",\n|"
OPENERS = "{[("
CLOSERS = "}])"
WEIGHT_PREFIX = re.compile(r"^-?\d*\.?\d+::")


@dataclass
class Match:
    name: str
    category: str
    count: int
    alias: str = ""         # the alias that matched, if any
    kind: int = PREFIX


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("_", " ")).strip().lower()


def format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k" if n >= 10_000 else f"{n / 1_000:.1f}k"
    return str(n) if n else ""


class TagIndex:
    def __init__(self, rows: List[Tuple[str, str, int, List[str]]]):
        """rows: (name, category, count, aliases), most used first."""
        self.names = [r[0] for r in rows]
        self.categories = [r[1] for r in rows]
        self.counts = [r[2] for r in rows]
        keys = []   # (key, tag index, kind, alias)
        for i, (name, _, _, aliases) in enumerate(rows):
            low = name.lower()
            keys.append((low, i, PREFIX, ""))
            for a in aliases:
                keys.append((a.lower(), i, ALIAS, a))
            for m in re.finditer(r"[ (]+", low):
                rest = low[m.end():]
                if len(rest) >= MIN_QUERY:
                    keys.append((rest, i, WORD, ""))
        keys.sort()
        self._keys = [k[0] for k in keys]
        self._entries = [k[1:] for k in keys]

    def __len__(self):
        return len(self.names)

    @classmethod
    def load(cls, path: str = DATA_FILE) -> "TagIndex":
        rows = []
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 3:
                    continue
                aliases = parts[3].split("|") if len(parts) > 3 and parts[3] else []
                rows.append((parts[0], parts[1], int(parts[2] or 0), aliases))
        return cls(rows)

    def search(self, query: str, limit: int = MAX_RESULTS,
               exclude: Optional[Set[str]] = None, minimum: int = MIN_QUERY) -> List[Match]:
        q = normalize(query)
        if len(q) < max(1, minimum):
            return []
        lo = bisect.bisect_left(self._keys, q)
        hi = bisect.bisect_left(self._keys, q + "￿")
        best = {}   # tag index -> (kind, alias)
        for key, (i, kind, alias) in zip(self._keys[lo:hi], self._entries[lo:hi]):
            if kind == PREFIX and key == q:
                kind = EXACT
            prev = best.get(i)
            if prev is None or kind < prev[0]:
                best[i] = (kind, alias)
        exclude = exclude or set()
        # direct matches first (exact, prefix, alias), then word matches; by use within each
        # exact match first; then by use, a later-word match counting a tenth, so
        # "miku" puts hatsune miku above rare tags that start with "miku"
        def rank(kv):
            i, (kind, _) = kv
            weight = WORD_WEIGHT if kind == WORD else 1.0
            return (kind != EXACT, -self.counts[i] * weight, i)
        ranked = sorted(best.items(), key=rank)
        out = []
        for i, (kind, alias) in ranked:
            if self.names[i].lower() in exclude:
                continue
            out.append(Match(self.names[i], self.categories[i], self.counts[i], alias, kind))
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------- shared, loaded lazily
_index: Optional[TagIndex] = None
_lock = threading.Lock()
_error = ""


def index(block: bool = False) -> Optional[TagIndex]:
    """The shared index, or None while it loads (or if the data file is missing)."""
    global _index, _error
    if _index is not None:
        return _index
    if not block:
        start_loading()
        return None
    with _lock:
        if _index is None and not _error:
            try:
                _index = TagIndex.load()
            except Exception as e:   # missing or damaged data file
                _error = str(e)
    return _index


def start_loading():
    if _index is None and not _error and not _lock.locked():
        threading.Thread(target=index, kwargs={"block": True}, daemon=True).start()


def load_error() -> str:
    return _error


# ---------------------------------------------------------------- text editing
def current_token(text: str, cursor: int) -> Tuple[int, int, str]:
    """(start, end, prefix) of the tag being typed at `cursor`.

    The tag starts after the last separator (comma, newline, |) before the cursor,
    skipping spaces, emphasis brackets and a V4 weight like "1.3::", and ends at the
    cursor. `prefix` (the same text) is what gets searched."""
    start = max(text.rfind(s, 0, cursor) for s in SEPARATORS) + 1
    seg = text[start:cursor]
    k = text.rfind("::", start, cursor)
    if k >= 0:                       # "1.3::tag" or "tag::" closing a weight
        start, seg = k + 2, text[k + 2:cursor]
    lead = len(seg) - len(seg.lstrip(" \t" + OPENERS))
    start += lead
    m = WEIGHT_PREFIX.match(text[start:cursor])
    if m:
        start += m.end()
    # The tag ends at the cursor: text after it (even with no space, as when typing
    # just before an existing tag) is never replaced.
    return start, cursor, text[start:cursor]


SENTENCE_END = ".!?"


def word_starts(text: str, start: int, cursor: int) -> List[int]:
    """`start`, then the start of each later word that follows the end of a sentence
    ("A girl in the rain. lo" → "lo"): the places a tag could begin when the text
    before it has no comma. Not every word: then a phrase that isn't a tag ("people
    in background") would restart suggestions at each new word."""
    out = [start]
    for i in range(start + 1, cursor):
        if text[i - 1] in " \t" and text[i] not in " \t":
            before = text[start:i].rstrip(" \t")
            if before and before[-1] in SENTENCE_END:
                out.append(i)
    return out


def find(idx: "TagIndex", text: str, cursor: int, force: bool = False):
    """Suggestions for the tag being typed: (start, end, matches) or None.

    Tries the whole stretch since the last comma first ("school un" → school
    uniform), then what follows a sentence end, so "A girl in the rain. lo" offers
    tags for "lo" and leaves the sentence alone. A phrase with no matching tag gets
    no suggestions until the next comma."""
    seg_start, end, _ = current_token(text, cursor)
    minimum = 1 if force else MIN_QUERY
    for start in word_starts(text, seg_start, cursor):
        query = normalize(text[start:cursor])
        if len(query) < minimum:
            break
        exclude = tags_in(text[:start] + text[end:])
        matches = idx.search(query, exclude=exclude, minimum=minimum)
        if matches:
            return start, end, matches
    return None


def apply_completion(text: str, start: int, end: int, tag: str) -> Tuple[str, int]:
    """Replace text[start:end] (the tag being typed) with `tag` and a comma, leaving
    everything after it alone. Returns (new text, new cursor).

    - at the end of the text: "tag, " (trailing spaces replaced)
    - before a comma, closing bracket or "::": just the tag
    - before a space or newline: "tag," (the existing space follows)
    - before anything else: "tag, "
    """
    before, after = text[:start], text[end:]
    if after.strip(" \t") == "":
        insert, after = tag + ", ", ""
    elif after[0] in "," + CLOSERS or after.startswith("::"):
        insert = tag
    elif after[0] in " \t\n":
        insert = tag + ","
    else:
        insert = tag + ", "
    return before + insert + after, len(before) + len(insert)


def tags_in(text: str) -> Set[str]:
    """Lower-case tags already in a prompt, for leaving them out of suggestions."""
    out = set()
    for part in re.split(r"[,\n|]", text):
        part = WEIGHT_PREFIX.sub("", part.strip(" \t{}[]()"))
        part = part.replace("::", "").strip(" \t{}[]()")
        if part:
            out.add(normalize(part))
    return out
