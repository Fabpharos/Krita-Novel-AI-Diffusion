"""Rectangle maths for Refine and Fill. Krita-free.

Rects are (x, y, w, h) tuples in document pixels.
"""
from typing import Optional, Tuple

Rect = Tuple[int, int, int, int]

CONTEXT_FRACTION = 0.5   # padding around a selection, as a share of its larger side
MIN_CONTEXT = 64         # but at least this many pixels


def intersect(a: Rect, b: Rect) -> Optional[Rect]:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def pad(rect: Rect, bounds: Rect, fraction: float = CONTEXT_FRACTION,
        minimum: int = MIN_CONTEXT) -> Rect:
    """Grow rect on every side by context around it, clamped to bounds. The model
    sees the surroundings, so a fill blends with what is next to the selection."""
    x, y, w, h = rect
    p = max(minimum, int(max(w, h) * fraction))
    grown = (x - p, y - p, w + 2 * p, h + 2 * p)
    return intersect(grown, bounds) or rect


def grow_to_aspect(rect: Rect, aspect: float, bounds: Rect) -> Rect:
    """Grow the short side of rect (around its centre) towards width/height ==
    aspect, staying inside bounds. Never shrinks. Whatever aspect difference is
    left over is absorbed by a slight stretch when the image is scaled."""
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    if w / h < aspect:      # too narrow: widen
        nw = min(bw, round(h * aspect))
        nx = min(max(bx, x - (nw - w) // 2), bx + bw - nw)
        return nx, y, nw, h
    nh = min(bh, round(w / aspect))   # too wide: heighten
    ny = min(max(by, y - (nh - h) // 2), by + bh - nh)
    return x, ny, w, nh


def contains(outer: Rect, inner: Rect) -> bool:
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and inner[0] + inner[2] <= outer[0] + outer[2]
            and inner[1] + inner[3] <= outer[1] + outer[3])
