"""Break-out layers (nai_krita/breakout.py): area, clip mask, names. Krita-free."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nai_krita import breakout, panels  # noqa: E402


def _poly_alpha(poly, w, h):
    """Alpha (0/255) of a convex polygon over a w x h area, sampled at pixel centres."""
    out = bytearray(w * h)
    n = len(poly)
    for y in range(h):
        for x in range(w):
            px, py = x + 0.5, y + 0.5
            inside = True
            for i in range(n):
                (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
                if (bx - ax) * (py - ay) - (by - ay) * (px - ax) < 0:
                    inside = False
                    break
            if inside:
                out[y * w + x] = 255
    return bytes(out)


def _at(mask, w, x, y):
    return mask[y * w + x]


def test_expand_each_edge_and_clamp():
    r = (100, 200, 400, 300)
    assert breakout.expand(r, "top", 30, (2000, 2000)) == (100, 110, 400, 390)
    assert breakout.expand(r, "bottom", 10, (2000, 2000)) == (100, 200, 400, 330)
    assert breakout.expand(r, "left", 25, (2000, 2000)) == (0, 200, 500, 300)
    assert breakout.expand(r, "right", 25, (2000, 2000)) == (100, 200, 500, 300)
    assert breakout.expand(r, "all", 10, (2000, 2000)) == (60, 170, 480, 360)
    # clamped to the canvas: a top panel can't break out above the page
    assert breakout.expand((0, 0, 400, 300), "top", 50, (2000, 2000)) == (0, 0, 400, 300)
    assert breakout.expand(r, "right", 100, (520, 2000)) == (100, 200, 420, 300)


def test_top_breakout_rectangle():
    # area 20x20; panel occupies rows 8..19, columns 4..15 (a top break-out area of 8 rows)
    w = h = 20
    alpha = _poly_alpha([(4, 8), (16, 8), (16, 20), (4, 20)], w, h)
    m = breakout.allowed_mask(alpha, w, h, "top")
    assert _at(m, w, 10, 2) == 255          # above the panel, within its width: shows
    assert _at(m, w, 2, 2) == 0             # above, but beside the panel: cut
    assert _at(m, w, 10, 12) == 255         # inside
    assert _at(m, w, 2, 12) == 0            # beside the panel: cut
    assert _at(m, w, 17, 12) == 0


def test_top_breakout_slanted_top_edge():
    # top edge slants down to the right: from (0, 4) to (20, 12)
    w = h = 20
    poly = [(0, 4), (20, 12), (20, 20), (0, 20)]
    alpha = _poly_alpha(poly, w, h)
    m = breakout.allowed_mask(alpha, w, h, "top")
    assert _at(m, w, 18, 9) == 255          # above the slant (outside the panel): shows
    assert alpha[9 * w + 18] == 0
    assert _at(m, w, 2, 9) == 255           # inside the panel
    assert all(v == 255 for v in m[:w * 4])  # rows wholly above the panel


def test_slanted_side_is_cut_below_its_top():
    # a panel whose left edge slants: the zone beside it (below its top) stays cut
    w = h = 20
    poly = [(2, 6), (18, 6), (18, 20), (8, 20)]
    alpha = _poly_alpha(poly, w, h)
    m = breakout.allowed_mask(alpha, w, h, "top")
    assert _at(m, w, 3, 18) == 0             # left of the slanted edge, low down: cut
    assert _at(m, w, 3, 2) == 255            # above the top edge: shows


def test_bottom_left_right_and_all():
    w = h = 20
    alpha = _poly_alpha([(4, 4), (16, 4), (16, 16), (4, 16)], w, h)
    bottom = breakout.allowed_mask(alpha, w, h, "bottom")
    assert _at(bottom, w, 10, 18) == 255 and _at(bottom, w, 10, 2) == 0
    left = breakout.allowed_mask(alpha, w, h, "left")
    assert _at(left, w, 1, 10) == 255 and _at(left, w, 18, 10) == 0 and _at(left, w, 1, 1) == 0
    right = breakout.allowed_mask(alpha, w, h, "right")
    assert _at(right, w, 18, 10) == 255 and _at(right, w, 1, 10) == 0
    every = breakout.allowed_mask(alpha, w, h, "all")
    for x, y in ((10, 1), (10, 18), (1, 10), (18, 10)):
        assert _at(every, w, x, y) == 255, (x, y)
    assert _at(every, w, 1, 1) == 0           # diagonal corners stay cut


def test_mask_keeps_antialiased_panel_alpha():
    w, h = 6, 4
    alpha = bytes([0, 0, 0, 0, 0, 0,
                   0, 128, 255, 255, 128, 0,
                   0, 128, 255, 255, 128, 0,
                   0, 0, 0, 0, 0, 0])
    m = breakout.allowed_mask(alpha, w, h, "top")
    assert m[w + 1] == 128 and m[w + 2] == 255
    assert m[1:5] == b"\xff" * 4 and m[0] == 0     # top row: over the panel's columns only


def test_empty_or_bad_alpha():
    assert breakout.allowed_mask(bytes(12), 4, 3, "top") == bytes(12)
    try:
        breakout.allowed_mask(bytes(5), 4, 3, "top")
        assert False
    except ValueError:
        pass
    try:
        breakout.expand((0, 0, 1, 1), "diagonal", 10, (5, 5))
        assert False
    except ValueError:
        pass


def test_alpha_channel_from_bgra():
    assert breakout.alpha_channel(bytes([1, 2, 3, 40, 5, 6, 7, 80])) == bytes([40, 80])


def test_names_and_keys():
    assert breakout.context_key("P3") == "P3 break-out"
    assert breakout.is_context_key("P3 break-out") and not breakout.is_context_key("P3")
    assert breakout.panel_of_key("P3 break-out") == "P3" and breakout.panel_of_key("P3") == "P3"
    assert breakout.is_result_name("P3 break-out NovelAI 123", "P3")
    assert not breakout.is_result_name("P3 break-out NovelAI 123", "P31")
    assert not breakout.is_result_name("P13 break-out NovelAI 123", "P3")
    assert not breakout.is_result_name("P3 break-out NovelAI 123 (mine)", "P3")
    # a panel's own result replacement ignores break-out layers
    assert not panels.is_result_layer_name("P3 break-out NovelAI 123")


def test_group_goes_above_borders():
    root = ["Paper", "Guides: trim (red) / inner frame (blue)", "P2 – …", "P1 – …",
            "Panel borders", "Labels (hide before export)"]
    assert breakout.insert_index(root) == 4
    assert breakout.insert_index(["Paper", "Background"]) is None


def test_target_carries_edge():
    t = panels.Target(rect=(0, 0, 10, 10), group_id="{g}", label="P3", breakout="top")
    assert t.breakout == "top" and t.is_panel
    assert panels.Target(rect=(0, 0, 1, 1)).breakout == ""
