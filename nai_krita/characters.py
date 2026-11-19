"""Character prompt helpers. Krita-free.

A character row is a request.Character. Its position is a free point (x, y)
from 0 to 1 across the generated image (None = "AI's choice"). V5 receives the
point as is; V4 and V4.5 receive it snapped to the centre of NovelAI's 5x5 grid
cell, written like the site: columns A-E left to right, rows 1-5 top to bottom.
"""
import re
from typing import List, Optional, Tuple

from .request import Character, GRID, coord_to_grid, clamp01

COLS = "ABCDE"
_LABELLED = re.compile(r"^\s*([^:\n]{1,40}?)\s*:\s*(.+?)\s*$")


def cell_label(cell: Optional[Tuple[int, int]]) -> str:
    if cell is None:
        return "AI"
    return f"{COLS[cell[0]]}{cell[1] + 1}"


def parse_cell(label: str) -> Optional[Tuple[int, int]]:
    label = label.strip().upper()
    if label in ("", "AI"):
        return None
    m = re.fullmatch(r"([A-E])([1-5])", label)
    if not m:
        raise ValueError(f"not a grid cell: {label!r} (use A1..E5 or AI)")
    return COLS.index(m.group(1)), int(m.group(2)) - 1


def cell_of(pos) -> Optional[Tuple[int, int]]:
    """Grid cell a free position falls in."""
    if pos is None:
        return None
    return coord_to_grid(pos[0]), coord_to_grid(pos[1])


def cell_center(cell: Tuple[int, int]) -> Tuple[float, float]:
    return round(cell[0] * 0.2 + 0.1, 1), round(cell[1] * 0.2 + 0.1, 1)


def pos_label(pos, free: bool) -> str:
    """Short label: a grid cell like 'C3', or '42%, 60%' for free positions."""
    if pos is None:
        return "–"
    if not free:
        return cell_label(cell_of(pos))
    return f"{round(pos[0] * 100)}%, {round(pos[1] * 100)}%"


def default_position(index: int, count: int) -> Tuple[float, float]:
    """Starting spot for character `index` of `count` when positions are switched
    on: spread evenly left to right across the middle."""
    return clamp01((index + 1) / (count + 1)), 0.5


def fill_positions(items: List[Character]) -> List[Character]:
    """Give every character without a position a default one (in place)."""
    for i, c in enumerate(items):
        if c.pos is None:
            c.pos = default_position(i, len(items))
    return items


def pos_from_point(cx: float, cy: float, area) -> Tuple[float, float]:
    """Free position (0..1) for a point in document pixels, relative to area (x, y, w, h)."""
    x, y, w, h = area
    return clamp01((cx - x) / w), clamp01((cy - y) / h)


def bounds_center(bounds) -> Optional[Tuple[float, float]]:
    """Centre of (x, y, w, h) bounds, or None for empty bounds."""
    x, y, w, h = bounds
    if w <= 0 or h <= 0:
        return None
    return x + w / 2, y + h / 2


def parse_labelled(text: str) -> List[Character]:
    """Turn labelled lines into characters.

        Mika: 1girl, red hair, school uniform
        Ren: 1boy, glasses

    Lines without "Name:" become unnamed characters. Blank lines are skipped.
    Characters added this way use "AI's choice" for position.
    """
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _LABELLED.match(line)
        if m and _is_label(m.group(1), line):
            out.append(Character(prompt=m.group(2), name=m.group(1)))
        else:
            out.append(Character(prompt=line.strip()))
    return out


def _is_label(label: str, line: str) -> bool:
    """A "Name: " prefix needs a space after the colon and no commas or digits-only,
    so tags like "artist:foo" and weights like "1.2::tag ::" are not taken as names."""
    after = line.split(":", 1)[1]
    return (bool(after) and after[0].isspace() and "," not in label
            and not label.replace(".", "").strip().isdigit())


def to_dict(c: Character, keep_link: bool = False) -> dict:
    """For saved character sets, layer links are dropped (they belong to one
    document). Per-document panel state keeps them with keep_link=True."""
    d = {"name": c.name, "prompt": c.prompt, "uc": c.uc, "enabled": c.enabled,
         "pos": list(c.pos) if c.pos is not None else None}
    if keep_link and c.layer_id:
        d["layer_id"] = c.layer_id
    if c.cid:
        d["cid"] = c.cid
    return d


def new_cid() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def from_dict(d: dict) -> Character:
    pos = d.get("pos")
    if pos is not None:
        pos = (clamp01(pos[0]), clamp01(pos[1]))
    elif d.get("cell") is not None:   # sets saved before free positions
        cell = d["cell"]
        pos = cell_center((max(0, min(GRID - 1, int(cell[0]))),
                           max(0, min(GRID - 1, int(cell[1])))))
    return Character(prompt=d.get("prompt", ""), uc=d.get("uc", ""), name=d.get("name", ""),
                     enabled=bool(d.get("enabled", True)), pos=pos,
                     layer_id=str(d.get("layer_id", "")), cid=str(d.get("cid", "")))
