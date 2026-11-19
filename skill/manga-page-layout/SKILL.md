---
name: "manga-page-layout"
description: "Generate a manga page panel layout (preview PNG + layered Krita .ora + per-panel sizes) from a terse description like '5 panels, 0/2/1/1/2 chars, panel 3 is the big moment'. The .ora carries panel markers read by the NovelAI Krita plugin."
---

# Manga page layout

The user describes a page tersely; you design the panel layout, render it with `manga_layout.py`, and hand back a preview, a layered `.ora` for Krita, and per-panel generation sizes. The `.ora` is ready for the NovelAI Krita plugin: each panel group holds a hidden marker layer the plugin reads (see §6).

## 1. Read the request — ask only what's missing

Minimum to proceed: **panel count** and **characters per panel**. Infer everything else. Ask (one short AskUserQuestion batch, max 3 questions) only for things that change the layout and weren't given:
- Which panel is the big moment? (if not obvious from notes)
- Shot distance per panel (wide / full / cowboy / medium / close / extreme) — if not given, choose from context: 0 chars → establishing/wide, 3+ chars → full or wide, emotional beat → close.
- Heavy dialogue panels (need bubble room → bigger/wider).
- Bleeds or a two-page spread.
If the user says "just make it", don't ask — decide and state your choices in one line.

Defaults (don't ask): B5 doujin page, 350 dpi, 3 mm bleed, right-to-left reading, row gutter 5 mm, column gutter 2.5 mm, border 0.6 mm. Presets: b5, a5, a4, b4; override with `trim_mm`/`frame_mm`.

## 2. Design rules

- Reading order: rows top→bottom, within a row right→left. Spec children are listed in reading order; the script numbers panels depth-first, which matches manga order (a tall right-side panel next to a stack = panel 1, then the stack top→bottom).
- 3–6 panels per page is normal; 7+ only for fast dialogue.
- Big moment gets the most area (often 1.5–2.5× a normal panel), often a full-width row or a tall side panel; may bleed.
- Establishing / scenery: full-width short row at the top, bleed top+sides is common.
- Extreme close-ups (eyes): small and wide, or thin and tall.
- Two characters side by side need width (aspect ≥ 1.0); full body solo works tall.
- Slants (mm) add energy: use on action/emotional beats, keep calm dialogue pages square. Typical 6–15 mm. Don't slant every cut.
- Vary row heights; avoid a perfectly even grid unless it's a 4-koma or deliberate.
- Don't put the most important panel where the eye leaves the page (bottom-left) unless it's a cliffhanger page turn.

## 3. Spec format

```json
{
  "page": {"preset": "b5", "dpi": 350, "bleed_mm": 3, "rtl": true,
           "gutter_row_mm": 5, "gutter_col_mm": 2.5, "border_mm": 0.6},
  "layout": {"split": "rows", "sizes": [1, 1.6, 1.1], "slants": [0, 10],
    "children": [
      {"chars": 0, "shot": "establishing", "note": "school rooftop", "bleed": ["top","left","right"]},
      {"split": "cols", "sizes": [1, 1.5], "slants": [12], "children": [
        {"chars": 2, "shot": "medium"},
        {"chars": 1, "shot": "close", "note": "BIG - she turns"}]},
      {"split": "cols", "children": [
        {"chars": 1, "shot": "extreme", "note": "eyes"},
        {"chars": 2, "shot": "full", "cast": "1girl, 1boy", "bleed": ["bottom","left"]}]}
    ]}
}
```
- `split: rows` stacks children top→bottom; `split: cols` places them in reading order (right→left when rtl). Nest freely.
- `sizes`: relative proportions (n values). `slants`: one per cut (n-1), mm. Rows: positive = right end lower. Cols: positive = top end further right.
- Panel keys: `chars` (number), `shot` (extreme/close/medium/cowboy/full/wide/establishing), `note`, `cast` (tag string, e.g. "2girls" — use 1girl/2girls style tags), `bleed` (list of top/bottom/left/right; only on sides touching the page frame), `frameless` (no border).
- Always give `chars` and `shot` for every panel, and `cast` whenever the characters are known: the NovelAI plugin uses them to set up each panel (character rows, panel prompt = cast + shot tags; "no humans" when `chars` is 0 and no cast). Keep `note` short and descriptive ("BIG - she turns"); the plugin shows it in its panel bar.

## 4. Run

If `manga_layout.py` isn't in the working directory, write it from the block at the end of this skill. If a copy exists, check it writes `nai-panel` marker layers (search it for `MARKER_PREFIX`); if it doesn't, it's an old version: replace it with the block below. Then:

```
python3 manga_layout.py spec.json -o out/page01   # --gen-mp 1.0 sets gen-size megapixels
```
Outputs: `out/page01_preview.png`, `out/page01.ora`, `out/page01_panels.json`. Read the preview image yourself and check: order arrows go right→left/top→bottom, no tiny or inverted panels, big moment really is biggest, bleeds hit the page edge. Fix and re-run before sending.

Also check the markers: `python3 -c "import zipfile;print('\n'.join(l for l in zipfile.ZipFile('out/page01.ora').read('stack.xml').decode().splitlines() if 'nai-panel' in l))"` should print one hidden `nai-panel {...}` layer per panel, with the right id, chars, shot, cast and note.

## 5. Deliver

Send the preview PNG and the .ora (SendUserFile). Then a compact table: panel, px size, suggested gen size, prompt hint (cast + shot tags). Offer tweaks in the user's terms ("swap 2 and 3's sizes", "slant the cut under row 2"). On a change, edit the spec and re-run — don't redesign from scratch.

Krita usage (tell the user once): open the .ora; each panel is a group `P# – …` with a white base shape. Select the base layer, paste/drag the image in so it lands above it inside the group, then toggle **Inherit Alpha** on the image layer to clip it to the panel. Hide the Labels layer before export; Guides layer (hidden) shows trim and inner frame. With the NovelAI plugin, tick **Panels** in its docker and select any layer in a panel: it generates straight into that panel.

## 6. Panel format shared with the NovelAI Krita plugin

Each panel group contains, at the bottom, a hidden 1×1 layer named
`nai-panel {"v": 1, "id": 3, "chars": 1, "shot": "close", "cast": "1girl", "note": "BIG - she turns"}`
(JSON after the prefix; `chars`, `shot`, `cast`, `note` only when known). Layer names survive Krita's .ora import and saving as .kra, so the data travels with the page. The plugin reads it together with the group name: what the name shows (P-number, char count, shot) wins when present, the marker adds cast and note, and if the user renames the group the marker alone identifies the panel. Pages without markers still work from the group name.

Rules: never change the prefix (`nai-panel `) or remove keys; add new keys only (the plugin ignores unknown ones) and bump `"v"` only for incompatible changes. Keep the group name format `P<id> – <chars> char, <shot> – <w>x<h>` and the base layer name `P<id> base (paste above, Inherit Alpha)`. The plugin's copy of this script and its reader live in the Krita NAI Plugin project (`manga_layout.py`, `nai_krita/panels.py`); change both together. Tell the user the marker layer can be ignored but shouldn't be deleted.

## Script: manga_layout.py

```python
#!/usr/bin/env python3
"""
manga_layout.py - render a manga page layout spec (JSON) into:
  <out>_preview.png   flat preview: panel numbers, reading order, sizes
  <out>.ora           layered OpenRaster file for Krita (one group per panel, each with
                      a hidden "nai-panel {...}" marker layer for the NovelAI plugin)
  <out>_panels.json   per-panel pixel size, aspect, suggested generation size, prompt hint

Usage:  python manga_layout.py spec.json -o out/page01 [--gen-mp 1.0]

Spec format (all lengths in mm; see SKILL.md for the full reference):
{
  "page": {"preset": "b5", "dpi": 350, "bleed_mm": 3, "rtl": true,
           "gutter_row_mm": 5, "gutter_col_mm": 2.5, "border_mm": 0.6},
  "layout": {"split": "rows", "sizes": [1, 2], "slants": [0],
             "children": [ {"chars": 0, "shot": "wide"},
                           {"split": "cols", "sizes": [2, 1], "slants": [8],
                            "children": [{"chars": 2, "shot": "medium"},
                                         {"chars": 1, "shot": "close", "bleed": ["bottom"]}]} ]}
}
"rows" stacks children top->bottom; "cols" places children in reading order
(right->left when rtl). Panels are numbered depth-first = manga reading order.
"""
import argparse, io, json, math, os, sys, zipfile
from xml.sax.saxutils import quoteattr
from PIL import Image, ImageDraw, ImageFont

PRESETS = {  # (trim w,h), (inner frame w,h) in mm
    "b5": ((182, 257), (150, 220)),
    "a5": ((148, 210), (122, 180)),
    "a4": ((210, 297), (176, 254)),
    "b4": ((257, 364), (220, 320)),
}
DEFAULTS = dict(preset="b5", dpi=350, bleed_mm=3, gutter_row_mm=5, gutter_col_mm=2.5,
                border_mm=0.6, rtl=True)
SHOT_TAGS = {
    "extreme": "close-up, eye focus", "close": "close-up, portrait", "medium": "upper body",
    "cowboy": "cowboy shot", "full": "full body", "wide": "wide shot",
    "establishing": "very wide shot, scenery",
}

# ---------- geometry: a line is (point, direction) ----------
def line(p, q): return (p, (q[0] - p[0], q[1] - p[1]))
def shift(L, dx, dy): return ((L[0][0] + dx, L[0][1] + dy), L[1])
def lerp(a, b, t): return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

def inter(A, B):
    (p, d), (q, e) = A, B
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-9:
        raise ValueError("two panel edges are parallel - check slants")
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return (p[0] + t * d[0], p[1] + t * d[1])

def corners(r):
    return [inter(r["top"], r["left"]), inter(r["top"], r["right"]),
            inter(r["bottom"], r["right"]), inter(r["bottom"], r["left"])]

def area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % 4][1] - poly[(i + 1) % 4][0] * poly[i][1]
                   for i in range(4))) / 2

# ---------- layout ----------
def layout(node, region, cfg, out):
    if "split" not in node:
        out.append((node, dict(region)))
        return
    kind, kids = node["split"], node["children"]
    n = len(kids)
    sizes = node.get("sizes") or [1] * n
    slants = node.get("slants") or [0] * (n - 1)
    if len(sizes) != n or len(slants) != n - 1:
        raise ValueError(f"{kind} split: need {n} sizes and {n-1} slants")
    tot, cum, fr = sum(sizes), 0, []
    for s in sizes[:-1]:
        cum += s
        fr.append(cum / tot)
    tl, tr, br, bl = corners(region)
    mm = cfg["mm"]
    cuts = []
    if kind == "rows":
        g = cfg["gutter_row"] / 2
        for f, s in zip(fr, slants):  # slant > 0: right end lower
            a, b, s = lerp(tl, bl, f), lerp(tr, br, f), s * mm / 2
            cuts.append(line((a[0], a[1] - s), (b[0], b[1] + s)))
        for i, k in enumerate(kids):
            r = dict(region)
            if i > 0: r["top"] = shift(cuts[i - 1], 0, g)
            if i < n - 1: r["bottom"] = shift(cuts[i], 0, -g)
            layout(k, r, cfg, out)
    elif kind == "cols":
        g, rtl = cfg["gutter_col"] / 2, cfg["rtl"]
        for f, s in zip(fr, slants):  # slant > 0: top end further right
            a, b = (lerp(tr, tl, f), lerp(br, bl, f)) if rtl else (lerp(tl, tr, f), lerp(bl, br, f))
            s = s * mm / 2
            cuts.append(line((a[0] + s, a[1]), (b[0] - s, b[1])))
        first, last, sign = ("right", "left", -1) if rtl else ("left", "right", 1)
        for i, k in enumerate(kids):
            r = dict(region)
            if i > 0: r[first] = shift(cuts[i - 1], sign * g, 0)
            if i < n - 1: r[last] = shift(cuts[i], -sign * g, 0)
            layout(k, r, cfg, out)
    else:
        raise ValueError(f"unknown split '{kind}' (use rows/cols)")

# ---------- helpers ----------
def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf",
              "arialbd.ttf", "Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def gen_size(w, h, mp=1.0, step=64, max_ratio=3.0):
    a = max(1 / max_ratio, min(max_ratio, w / h))
    A = mp * 1024 * 1024
    gw = math.sqrt(A * a)
    return int(round(gw / step) * step), int(round(A / gw / step) * step)

def png_bytes(img):
    b = io.BytesIO(); img.save(b, "PNG", optimize=False); return b.getvalue()

def centered_text(d, xy, text, f, fill, stroke=0, stroke_fill="white"):
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=f, stroke_width=stroke)
    d.text((xy[0] - (x1 - x0) / 2 - x0, xy[1] - (y1 - y0) / 2 - y0), text, font=f, fill=fill,
           stroke_width=stroke, stroke_fill=stroke_fill)

def closed(poly): return [tuple(map(float, p)) for p in poly] + [tuple(map(float, poly[0]))]

# ---------- panel marker (read by the NovelAI Krita plugin) ----------
# A hidden 1x1 layer at the bottom of each panel group, named
#   nai-panel {"v": 1, "id": 3, "chars": 1, "shot": "close", "cast": "1girl", "note": "..."}
# Layer names survive Krita's .ora import and saving as .kra, so the panel data
# travels with the page. Keep in step with nai_krita/panels.py (MARKER_PREFIX).
MARKER_PREFIX, MARKER_VERSION = "nai-panel ", 1
MARKER_IMG = Image.new("RGBA", (1, 1), (0, 0, 0, 0))

def panel_marker(p):
    d = {"v": MARKER_VERSION, "id": p["id"]}
    if isinstance(p["chars"], int): d["chars"] = p["chars"]
    for k in ("shot", "cast", "note"):
        if p.get(k): d[k] = p[k]
    return MARKER_PREFIX + json.dumps(d, ensure_ascii=False, separators=(", ", ": "))

# ---------- ORA ----------
def write_ora(path, W, H, dpi, tree, merged):
    files, n = {}, [0]
    def emit(items, ind):
        xml = ""
        for it in items:
            if it["type"] == "stack":
                xml += f'{ind}<stack name={quoteattr(it["name"])} visibility="visible" isolation="isolate">\n'
                xml += emit(it["children"], ind + "  ")
                xml += f"{ind}</stack>\n"
            else:
                n[0] += 1
                src = f"data/layer{n[0]:03d}.png"
                files[src] = png_bytes(it["img"])
                vis = "visible" if it.get("visible", True) else "hidden"
                xml += (f'{ind}<layer name={quoteattr(it["name"])} src="{src}" x="{it.get("x",0)}" '
                        f'y="{it.get("y",0)}" visibility="{vis}" opacity="{it.get("opacity",1.0):.2f}" '
                        f'composite-op="svg:src-over"/>\n')
        return xml
    body = emit(tree, "    ")
    stack = (f"<?xml version='1.0' encoding='UTF-8'?>\n"
             f'<image version="0.0.3" w="{W}" h="{H}" xres="{dpi}" yres="{dpi}">\n'
             f'  <stack>\n{body}  </stack>\n</image>\n')
    thumb = merged.copy(); thumb.thumbnail((256, 256))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "image/openraster", compress_type=zipfile.ZIP_STORED)
        z.writestr("stack.xml", stack, compress_type=zipfile.ZIP_DEFLATED)
        for k, v in files.items():
            z.writestr(k, v, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("mergedimage.png", png_bytes(merged), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("Thumbnails/thumbnail.png", png_bytes(thumb), compress_type=zipfile.ZIP_DEFLATED)

# ---------- main ----------
def build(spec, out, gen_mp=1.0):
    page = {**DEFAULTS, **spec.get("page", {})}
    if page["preset"] not in PRESETS:
        raise ValueError(f"unknown preset {page['preset']} (have {', '.join(PRESETS)})")
    trim, frame = PRESETS[page["preset"]]
    trim, frame = page.get("trim_mm", trim), page.get("frame_mm", frame)
    dpi = page["dpi"]; mm = dpi / 25.4; b = page["bleed_mm"]
    W, H = round((trim[0] + 2 * b) * mm), round((trim[1] + 2 * b) * mm)
    fw, fh = frame[0] * mm, frame[1] * mm
    x0, y0 = (W - fw) / 2, (H - fh) / 2
    x1, y1 = x0 + fw, y0 + fh
    root = dict(top=line((x0, y0), (x1, y0)), bottom=line((x0, y1), (x1, y1)),
                left=line((x0, y0), (x0, y1)), right=line((x1, y0), (x1, y1)))
    edges = dict(top=line((0, 0), (W, 0)), bottom=line((0, H), (W, H)),
                 left=line((0, 0), (0, H)), right=line((W, 0), (W, H)))
    cfg = dict(mm=mm, gutter_row=page["gutter_row_mm"] * mm, gutter_col=page["gutter_col_mm"] * mm,
               rtl=page["rtl"])
    raw = []
    layout(spec["layout"], root, cfg, raw)

    panels, warnings = [], []
    for i, (node, reg) in enumerate(raw, 1):
        for side in node.get("bleed", []):
            reg[side] = edges[side]
        poly = corners(reg)
        if area(poly) < (20 * mm) ** 2:
            warnings.append(f"P{i} is very small or inverted - check sizes/slants")
        xs = [min(W, max(0, p[0])) for p in poly]; ys = [min(H, max(0, p[1])) for p in poly]
        bx0, by0, bx1, by1 = int(min(xs)), int(min(ys)), int(math.ceil(max(xs))), int(math.ceil(max(ys)))
        w, h = bx1 - bx0, by1 - by0
        chars = node.get("chars", "?")
        shot = node.get("shot", "")
        cast = node.get("cast") or ("no humans" if chars == 0 else "")
        hint = ", ".join(t for t in (cast, SHOT_TAGS.get(shot, shot)) if t)
        panels.append(dict(id=i, chars=chars, shot=shot, note=node.get("note", ""),
                           cast=node.get("cast", ""),
                           bleed=node.get("bleed", []), frameless=node.get("frameless", False),
                           poly=[[round(p[0], 1), round(p[1], 1)] for p in poly],
                           bbox=[bx0, by0, bx1, by1], size=[w, h], aspect=round(w / h, 3),
                           gen_size=list(gen_size(w, h, gen_mp)), prompt_hint=hint))

    bw = max(1, round(page["border_mm"] * mm))
    # borders layer
    borders = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(borders)
    for p in panels:
        if not p["frameless"]:
            bd.line(closed(p["poly"]), fill=(0, 0, 0, 255), width=bw, joint="curve")
    # guides
    guides = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(guides)
    bp = b * mm
    gd.rectangle([bp, bp, W - bp, H - bp], outline=(230, 60, 60, 255), width=3)    # trim
    gd.rectangle([x0, y0, x1, y1], outline=(60, 140, 230, 255), width=3)           # inner frame
    # labels + reading order
    labels = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(labels)
    R = int(W * 0.035)
    fnum, fsmall = font(int(R * 1.1)), font(int(R * 0.55))
    cents = [(sum(q[0] for q in p["poly"]) / 4, sum(q[1] for q in p["poly"]) / 4) for p in panels]
    cents = [(min(p["bbox"][2] - R, max(p["bbox"][0] + R, c[0])),
              min(p["bbox"][3] - R, max(p["bbox"][1] + R, c[1]))) for p, c in zip(panels, cents)]
    red = (220, 50, 50, 200)
    for a, c in zip(cents, cents[1:]):
        dist = math.hypot(c[0] - a[0], c[1] - a[1]) or 1
        u = ((c[0] - a[0]) / dist, (c[1] - a[1]) / dist)
        a, c = (a[0] + u[0] * R, a[1] + u[1] * R), (c[0] - u[0] * R, c[1] - u[1] * R)
        ld.line([a, c], fill=red, width=max(3, bw))
        m = lerp(a, c, 0.5); ang = math.atan2(c[1] - a[1], c[0] - a[0]); s = R * 0.6
        ld.polygon([(m[0] + s * math.cos(ang), m[1] + s * math.sin(ang)),
                    (m[0] + s * math.cos(ang + 2.5), m[1] + s * math.sin(ang + 2.5)),
                    (m[0] + s * math.cos(ang - 2.5), m[1] + s * math.sin(ang - 2.5))], fill=red)
    for p, c in zip(panels, cents):
        ld.ellipse([c[0] - R, c[1] - R, c[0] + R, c[1] + R], fill=(255, 255, 255, 235),
                   outline=(220, 50, 50, 255), width=max(3, bw))
        centered_text(ld, c, str(p["id"]), fnum, (220, 50, 50, 255))
        lines = [f'{p["chars"]} char · {p["shot"]}'.strip(" ·"), f'{p["size"][0]}×{p["size"][1]}px']
        if p["note"]: lines.append(p["note"][:40])
        for j, t in enumerate(lines):
            centered_text(ld, (c[0], c[1] + R * 1.5 + j * R * 0.75), t, fsmall, (40, 40, 40, 255),
                          stroke=4, stroke_fill=(255, 255, 255, 255))

    paper = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    preview = paper.copy()
    for layer in (guides, borders, labels):
        preview.alpha_composite(layer)
    merged = paper.copy(); merged.alpha_composite(borders)

    # ORA tree (top first)
    groups = []
    for p in panels:
        bx0, by0, bx1, by1 = p["bbox"]
        base = Image.new("RGBA", (max(1, bx1 - bx0), max(1, by1 - by0)), (0, 0, 0, 0))
        ImageDraw.Draw(base).polygon([(q[0] - bx0, q[1] - by0) for q in p["poly"]], fill=(255, 255, 255, 255))
        desc = f'{p["chars"]} char, {p["shot"]}'.strip(", ") if p["shot"] or p["chars"] != "?" else ""
        groups.append(dict(type="stack", name=f'P{p["id"]} – {desc} – {p["size"][0]}x{p["size"][1]}',
                           children=[dict(type="layer", name=f'P{p["id"]} base (paste above, Inherit Alpha)',
                                          img=base, x=bx0, y=by0),
                                     dict(type="layer", name=panel_marker(p), img=MARKER_IMG,
                                          x=bx0, y=by0, visible=False)]))
    tree = ([dict(type="layer", name="Labels (hide before export)", img=labels, opacity=0.85),
             dict(type="layer", name="Panel borders", img=borders)] + groups +
            [dict(type="layer", name="Guides: trim (red) / inner frame (blue)", img=guides, visible=False),
             dict(type="layer", name="Paper", img=paper)])

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    preview.convert("RGB").save(out + "_preview.png")
    write_ora(out + ".ora", W, H, dpi, tree, merged)
    meta = dict(canvas=[W, H], dpi=dpi, preset=page["preset"], rtl=page["rtl"], panels=panels)
    with open(out + "_panels.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    return meta, warnings

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec"); ap.add_argument("-o", "--out", default="page")
    ap.add_argument("--gen-mp", type=float, default=1.0, help="megapixels for suggested gen size")
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    meta, warnings = build(spec, a.out, a.gen_mp)
    print(f"canvas {meta['canvas'][0]}x{meta['canvas'][1]} @ {meta['dpi']}dpi, {len(meta['panels'])} panels")
    for p in meta["panels"]:
        print(f"P{p['id']}: {p['size'][0]}x{p['size'][1]} (aspect {p['aspect']}) gen {p['gen_size'][0]}x{p['gen_size'][1]}"
              f"  | {p['prompt_hint']}" + (f"  | {p['note']}" if p['note'] else ""))
    for w in warnings:
        print("WARNING:", w, file=sys.stderr)
    print(f"wrote {a.out}_preview.png, {a.out}.ora, {a.out}_panels.json")

if __name__ == "__main__":
    main()
```
