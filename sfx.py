#!/usr/bin/env python3
"""
sfx.py - letter a comic sound effect onto an image.

Each letter is drawn on its own small canvas in three passes (thin dark edge,
thick white outline, black fill), turned a little, scaled up along the word, and placed
along a gentle curve. That uneven, growing look is what makes SFX read as sound
rather than as a caption.

Usage:
  python sfx.py in.png out.png "FWOOOSH" --x 0.08 --y 0.30 --width 0.75 --angle -12
  (x, y: where the word starts, as fractions of the image; width: how far it runs)

Also writes out_sfx.png: the lettering alone on transparency, to drop on its own
layer in Krita.
"""
import argparse, math, os
from PIL import Image, ImageDraw, ImageFont

FONT = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts\KOMIKSKI.ttf")  # Komika Slick Italic


def letter(ch, font, fill, outline, halo, ow, hw):
    """One letter as an RGBA tile: halo, then outline, then fill."""
    pad = ow + hw + 4
    l, t, r, b = font.getbbox(ch, stroke_width=ow + hw)
    tile = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    at = (pad - l, pad - t)
    d.text(at, ch, font=font, fill=halo, stroke_width=ow + hw, stroke_fill=halo)  # 1. halo
    d.text(at, ch, font=font, fill=outline, stroke_width=ow, stroke_fill=outline)  # 2. outline
    d.text(at, ch, font=font, fill=fill)                                           # 3. fill
    return tile


def sfx_layer(size, text, x, y, width, angle=-10, grow=1.6, curve=0.06, jitter=6,
              fill=(20, 20, 20, 255), outline=(255, 255, 255, 255), halo=(20, 20, 20, 255)):
    W, H = size
    n = len(text)
    span = width * W
    # letter heights grow from h0 to h0*grow; pick h0 so the word's measured
    # width (each glyph's real advance) fills `span`
    scales = [1 + (grow - 1) * i / max(1, n - 1) for i in range(n)]
    probe = ImageFont.truetype(FONT, 100)
    h0 = span / sum(probe.getlength(c) / 100 * s for c, s in zip(text, scales))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    a = math.radians(angle)
    pos = 0.0
    for i, (ch, s) in enumerate(zip(text, scales)):
        px = int(h0 * s)
        font = ImageFont.truetype(FONT, px)
        ow, hw = max(3, px // 9), max(2, px // 40)   # white outline, thin dark edge
        tile = letter(ch, font, fill, outline, halo, ow, hw)
        tilt = angle + (jitter if i % 2 else -jitter) * 0.5        # letters wobble
        tile = tile.rotate(-tilt, resample=Image.BICUBIC, expand=True)
        t = i / max(1, n - 1)
        bow = -math.sin(t * math.pi) * curve * H                   # gentle arc upwards
        adv = font.getlength(ch)
        cx = x * W + (pos + adv / 2) * math.cos(a)
        cy = y * H + (pos + adv / 2) * math.sin(a) + bow
        layer.alpha_composite(tile, (int(cx - tile.width / 2), int(cy - tile.height / 2)))
        pos += adv                                                 # outlines overlap: one shape
    return layer


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src"); ap.add_argument("out"); ap.add_argument("text")
    ap.add_argument("--x", type=float, default=0.1); ap.add_argument("--y", type=float, default=0.3)
    ap.add_argument("--width", type=float, default=0.7); ap.add_argument("--angle", type=float, default=-10)
    ap.add_argument("--grow", type=float, default=1.6); ap.add_argument("--curve", type=float, default=0.06)
    a = ap.parse_args()
    img = Image.open(a.src).convert("RGBA")
    layer = sfx_layer(img.size, a.text, a.x, a.y, a.width, a.angle, a.grow, a.curve)
    root, _ = os.path.splitext(a.out)
    layer.save(root + "_sfx.png")
    img.alpha_composite(layer)
    img.convert("RGB").save(a.out)
    print(f"wrote {a.out} and {root}_sfx.png")


if __name__ == "__main__":
    main()
