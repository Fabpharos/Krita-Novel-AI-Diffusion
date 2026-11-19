"""Manga panel awareness for pages made by manga_layout.py. Krita-free.

Two sources, both inside the .ora so they travel with the page:

1. The panel marker (phase 9): a hidden 1x1 layer inside each panel group, named
   'nai-panel {"v": 1, "id": 3, "chars": 1, "shot": "close", "cast": "1girl",
   "note": "BIG - she turns"}'. Written by manga_layout.py; a layer name is what
   survives Krita's .ora import and saving as .kra.
2. The group name, "P3 – 1 char, close – 1306x1250" (en dashes). Parsing is
   lenient: hand-edited names may use hyphens, drop the char count or the shot, or
   drop the size.

What the name shows (number, char count, shot) wins when it is there, since that is
what the user sees and edits; the marker adds what a name can't hold (cast, note) and
supplies everything when the group was renamed to something else. Real bounds always
come from the base layer, never from either.
"""
import json
import re
from dataclasses import dataclass
from typing import Optional

SHOT_TAGS = {
    "extreme": "close-up, eye focus",
    "close": "close-up, portrait",
    "medium": "upper body",
    "cowboy": "cowboy shot",
    "full": "full body",
    "wide": "wide shot",
    "establishing": "very wide shot, scenery",
}

_DASH = r"\s*[–—-]\s*"
_HEAD = re.compile(r"^P(\d+)(?:" + _DASH + r"|\s*$)")
_CHARS = re.compile(r"(\d+)\s*chars?\b", re.I)
_SIZE = re.compile(r"(\d+)\s*[x×]\s*(\d+)\s*$")
_SHOT = re.compile(r"\b(" + "|".join(SHOT_TAGS) + r")\b", re.I)
_BASE = re.compile(r"^P\d+\s+base\b", re.I)
MARKER_PREFIX = "nai-panel "
MARKER_VERSION = 1


@dataclass
class PanelInfo:
    id: int
    chars: Optional[int] = None
    shot: str = ""
    size: Optional[tuple] = None   # (w, h) from the name; informational only
    cast: str = ""                 # e.g. "2girls" (marker only)
    note: str = ""                 # e.g. "BIG - she turns" (marker only)
    source: str = "name"           # "name", "marker" or "name+marker"

    @property
    def label(self) -> str:
        return f"P{self.id}"

    @property
    def shot_tags(self) -> str:
        return SHOT_TAGS.get(self.shot, "")


def parse_panel_name(name: str) -> Optional[PanelInfo]:
    name = name.strip()
    head = _HEAD.match(name)
    if not head:
        return None
    info = PanelInfo(id=int(head.group(1)))
    rest = name[head.end():]
    size = _SIZE.search(rest)
    if size:
        info.size = (int(size.group(1)), int(size.group(2)))
        rest = rest[:size.start()]
    chars = _CHARS.search(rest)
    if chars:
        info.chars = int(chars.group(1))
    shot = _SHOT.search(rest)
    if shot:
        info.shot = shot.group(1).lower()
    return info


def is_base_layer_name(name: str) -> bool:
    return bool(_BASE.match(name.strip()))


# ---------------------------------------------------------------- panel marker
def marker_name(data: dict) -> str:
    """The marker layer's name for a panel (what manga_layout.py writes)."""
    d = {"v": MARKER_VERSION}
    d.update({k: v for k, v in data.items() if v not in (None, "", [])})
    return MARKER_PREFIX + json.dumps(d, ensure_ascii=False, separators=(", ", ": "))


def is_marker_name(name: str) -> bool:
    return name.strip().startswith(MARKER_PREFIX.strip())


def parse_marker(name: str) -> Optional[dict]:
    """The marker's data, or None if this isn't a readable marker. Unknown keys are
    kept (newer script versions may add some); a newer major version is still read
    for the keys this plugin knows."""
    name = name.strip()
    if not is_marker_name(name):
        return None
    try:
        d = json.loads(name[len(MARKER_PREFIX.strip()):].strip())
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def find_marker(group) -> Optional[dict]:
    for child in group.childNodes():
        if child.type() != "grouplayer":
            d = parse_marker(child.name())
            if d is not None:
                return d
    return None


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def group_info(group) -> Optional[PanelInfo]:
    """PanelInfo for a group from its name and/or its marker, or None if it isn't a
    panel."""
    info = parse_panel_name(group.name())
    marker = find_marker(group)
    if marker is None:
        return info
    m_id = _int_or_none(marker.get("id"))
    if info is None:
        if m_id is None:
            return None
        info = PanelInfo(id=m_id, source="marker")
        info.chars = _int_or_none(marker.get("chars"))
        info.shot = str(marker.get("shot") or "").lower()
    else:
        info.source = "name+marker"
        if info.chars is None:
            info.chars = _int_or_none(marker.get("chars"))
        if not info.shot:
            info.shot = str(marker.get("shot") or "").lower()
    info.cast = str(marker.get("cast") or "").strip()
    info.note = str(marker.get("note") or "").strip()
    return info


def find_panel_group(node):
    """Walk up from a Krita node (duck-typed: name(), type(), parentNode(),
    childNodes()) to the nearest panel group. Returns (group, PanelInfo) or
    (None, None)."""
    while node is not None:
        if node.type() == "grouplayer":
            info = group_info(node)
            if info:
                return node, info
        node = node.parentNode()
    return None, None


def find_base_layer(group):
    """The panel's base shape layer inside a panel group, or None."""
    for child in group.childNodes():
        if is_base_layer_name(child.name()):
            return child
    return None


# ---------------------------------------------------------------- generation targets
@dataclass
class Target:
    """Where a result goes. Krita-free: nodes are referred to by uniqueId strings."""
    rect: tuple                  # (x, y, w, h) in document pixels
    group_id: str = ""           # panel group; "" = top of the active layer's parent
    base_id: str = ""            # result is inserted directly above this node
    label: str = ""              # "P3" for panels
    stretch: bool = False        # scale the result exactly onto rect (Refine/Fill), not cover
    on_top: bool = False         # insert at the top of the panel group (patches over art)
    mask: bytes = b""            # feathered selection alpha over rect (w*h bytes), if any

    @property
    def is_panel(self) -> bool:
        return bool(self.group_id)


PAGE_KEY = "page"


def default_panel_prompt(info: PanelInfo) -> str:
    """Starting panel prompt: the marker's cast tags (or "no humans" for an empty
    panel), then the shot tags. Shown as editable text in the docker, never added
    silently."""
    parts = []
    if info.cast:
        parts.append(info.cast)
    elif info.chars == 0:
        parts.append("no humans")
    if info.shot_tags:
        parts.append(info.shot_tags)
    return ", ".join(parts)


def join_prompts(*parts: str) -> str:
    return ", ".join(p.strip(" ,\n") for p in parts if p and p.strip(" ,\n"))


def is_result_layer_name(name: str) -> bool:
    """Layers the plugin created when a result was applied ("NovelAI <seed>")."""
    return bool(re.match(r"^NovelAI \d+$", name.strip()))
