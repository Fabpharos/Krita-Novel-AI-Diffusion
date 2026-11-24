"""Break-out layers: a character generated on a transparent background (V5) that pokes
out of its panel, e.g. a head over the top border. Krita-free.

The panel's own image stays clipped inside the panel group. The break-out layer sits
above the panel borders, unclipped, and is masked to:
  - the inside of the panel, plus
  - the zone beyond the chosen edge(s), within the panel's extent along that edge.
So a head may cross the top border, but a body that runs out of the panel's sides
or bottom is still cut at the panel edge.

Panels from manga_layout.py are convex quadrilaterals (or rectangles), so each row
of the panel's base-layer alpha holds one opaque span. The zones are worked out from
those spans with C-speed byte operations, one row at a time.
"""
import re
from typing import List, Optional, Sequence, Tuple

TOP, BOTTOM, LEFT, RIGHT, ALL = "top", "bottom", "left", "right", "all"
EDGES = (TOP, BOTTOM, LEFT, RIGHT, ALL)
EDGE_LABELS = {TOP: "Top", BOTTOM: "Bottom", LEFT: "Left", RIGHT: "Right", ALL: "All sides"}
DEFAULT_EDGE, DEFAULT_AMOUNT = TOP, 30          # percent of the panel's size across that edge
GROUP_NAME = "Break-outs (above borders)"       # root group holding every break-out layer
BORDERS_NAME = "Panel borders"                   # manga_layout.py's border layer
KEY_SUFFIX = " break-out"                         # context key: "P3 break-out"


def sides(edge: str) -> Tuple[str, ...]:
    if edge == ALL:
        return TOP, BOTTOM, LEFT, RIGHT
    if edge not in EDGES:
        raise ValueError(f"unknown break-out edge {edge!r}")
    return (edge,)


def expand(rect, edge: str, amount: int, canvas: Tuple[int, int]):
    """The panel rect (x, y, w, h) extended past `edge` by `amount` percent of the
    panel's height (top/bottom) or width (left/right), clamped to the canvas."""
    x, y, w, h = rect
    cw, ch = canvas
    dy, dx = round(h * amount / 100), round(w * amount / 100)
    x0, y0, x1, y1 = x, y, x + w, y + h
    for s in sides(edge):
        if s == TOP:
            y0 -= dy
        elif s == BOTTOM:
            y1 += dy
        elif s == LEFT:
            x0 -= dx
        else:
            x1 += dx
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(cw, x1), min(ch, y1)
    return x0, y0, x1 - x0, y1 - y0


def _spans(alpha: bytes, w: int, h: int) -> List[Optional[Tuple[int, int]]]:
    """Per row, the panel's opaque span as (first, end) column indices, or None."""
    out = []
    for r in range(h):
        row = alpha[r * w:(r + 1) * w]
        left = w - len(row.lstrip(b"\x00"))
        if left == w:
            out.append(None)
        else:
            out.append((left, len(row.rstrip(b"\x00"))))
    return out


def _beyond_vertical(spans, rows) -> List[Optional[Tuple[int, int, int, int]]]:
    """For a top (rows top-down) or bottom (rows bottom-up) break-out: per row, the
    columns beyond the edge as up to two ranges (a, b, c, d) = [a, b) and [c, d), i.e.
    the panel's full column range minus the columns the panel has reached so far.
    Convex panel: everything it covers up to this row is one interval."""
    full = [s for s in spans if s]
    if not full:
        return [None] * len(spans)
    lo, hi = min(s[0] for s in full), max(s[1] for s in full)
    out = [None] * len(spans)
    seen = None
    for r in rows:
        s = spans[r]
        if s is not None:
            seen = s if seen is None else (min(seen[0], s[0]), max(seen[1], s[1]))
        if seen is None:
            out[r] = (lo, hi, hi, hi)         # wholly beyond the edge: the panel's width
        else:
            out[r] = (lo, seen[0], seen[1], hi)
    return out


def allowed_mask(alpha: bytes, w: int, h: int, edge: str) -> bytes:
    """Where the break-out layer may show (0-255 per pixel, w*h bytes): the panel's own
    alpha (antialiased edges kept) plus 255 in the zones beyond `edge`.

    `alpha` is the panel's base-layer alpha over the expanded break-out rect."""
    if len(alpha) != w * h:
        raise ValueError("alpha does not match the area size")
    spans = _spans(alpha, w, h)
    zones: List[List[Tuple[int, int]]] = [[] for _ in range(h)]
    for s in sides(edge):
        if s in (TOP, BOTTOM):
            order = range(h) if s == TOP else range(h - 1, -1, -1)
            for r, z in enumerate(_beyond_vertical(spans, order)):
                if z:
                    zones[r] += [(z[0], z[1]), (z[2], z[3])]
        else:
            for r, sp in enumerate(spans):   # rows the panel spans, outside its span
                if sp is not None:
                    zones[r].append((0, sp[0]) if s == LEFT else (sp[1], w))
    out = bytearray(alpha)
    full = b"\xff" * w
    for r, zs in enumerate(zones):
        base = r * w
        for a, b in zs:
            if b > a:
                out[base + a:base + b] = full[:b - a]
    return bytes(out)


def alpha_channel(bgra: bytes) -> bytes:
    """Alpha bytes from Krita 8-bit RGBA pixel data (BGRA in memory)."""
    return bytes(bgra[3::4])


# ---------------------------------------------------------------- names
def context_key(label: str) -> str:
    return label + KEY_SUFFIX


def is_context_key(key: str) -> bool:
    return key.endswith(KEY_SUFFIX)


def panel_of_key(key: str) -> str:
    return key[:-len(KEY_SUFFIX)] if is_context_key(key) else key


def layer_prefix(label: str) -> str:
    """Name prefix of a panel's break-out results: "P3 break-out NovelAI <seed>"."""
    return f"{label} break-out NovelAI"


def is_result_name(name: str, label: str) -> bool:
    return bool(re.match(r"^%s \d+$" % re.escape(layer_prefix(label)), name.strip()))


def insert_index(names: Sequence[str]) -> Optional[int]:
    """Where the break-out group goes among the root's children (bottom first, as
    Krita lists them): directly above the panel borders, or None for the top."""
    for i, n in enumerate(names):
        if n == BORDERS_NAME:
            return i
    return None
