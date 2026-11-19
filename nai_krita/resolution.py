"""Map an arbitrary rectangle to a NovelAI generation size and back. Krita-free."""
import math
from typing import Tuple

STEP = 64
FREE_PIXELS = 1024 * 1024        # Opus free cap (width x height)
LARGE_PIXELS = 1536 * 1024       # "Large" preset; always paid
MIN_SIDE = 384                   # accepted live on V5 (384x1024, 2026-11-13)
MAX_SIDE = 2048                  # accepted live on V5 (512x2048 and 2048x512, 2026-11-13)
MIN_FILL = 0.85                  # keep at least this share of the pixel budget


def nearest_size(width: int, height: int, max_pixels: int = FREE_PIXELS,
                 step: int = STEP) -> Tuple[int, int]:
    """The allowed size closest in aspect to width:height that still uses most
    of the pixel budget.

    Candidates are multiples of `step`, within MIN_SIDE..MAX_SIDE, with
    w*h <= max_pixels and at least MIN_FILL of the largest candidate area (so a
    tiny exact-aspect size never beats a full-size near match). Ties on aspect
    go to the larger area.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    target = math.log(width / height)

    def near(a_len, b_len):
        """(a, b) candidates: each allowed a, with b just below and above the ideal."""
        out = []
        for a in range(MIN_SIDE, MAX_SIDE + 1, step):
            b_max = min(MAX_SIDE, (max_pixels // a) // step * step)
            if b_max < MIN_SIDE:
                continue
            b_ideal = a * b_len / a_len
            for b in {max(MIN_SIDE, min(b_max, int(b_ideal // step) * step)),
                      max(MIN_SIDE, min(b_max, int(math.ceil(b_ideal / step)) * step))}:
                out.append((a, b))
        return out
    # from both sides, so a shape and its 90° turn get mirrored sizes
    cands = set(near(width, height)) | {(w, h) for h, w in near(height, width)}
    biggest = max(w * h for w, h in cands)
    cands = [c for c in cands if c[0] * c[1] >= MIN_FILL * biggest]
    return min(cands, key=lambda c: (round(abs(math.log(c[0] / c[1]) - target), 6),
                                     -(c[0] * c[1])))


def cover_rect(src_w: int, src_h: int, dst_x: int, dst_y: int, dst_w: int, dst_h: int):
    """Scale a src_w x src_h image to cover the destination rect, centred.

    Returns (scaled_w, scaled_h, offset_x, offset_y): draw the scaled image at
    (offset_x, offset_y) and crop to the destination rect.
    """
    scale = max(dst_w / src_w, dst_h / src_h)
    sw, sh = max(dst_w, round(src_w * scale)), max(dst_h, round(src_h * scale))
    return sw, sh, dst_x - (sw - dst_w) // 2, dst_y - (sh - dst_h) // 2
