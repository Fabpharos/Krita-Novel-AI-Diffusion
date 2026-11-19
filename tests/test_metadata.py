"""Phase 7: reading settings back out of NovelAI PNGs (Krita-free)."""
import gzip
import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nai_krita import metadata, settings  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _png(text: dict, w=2, h=2) -> bytes:
    """A tiny RGBA PNG with tEXt chunks."""
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data))
    raw = b"".join(b"\0" + b"\xff" * (4 * w) for _ in range(h))
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    for k, v in text.items():
        out += chunk(b"tEXt", k.encode("latin-1") + b"\0" + v.encode("latin-1"))
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _styles():
    return {s.model: s for s in settings.default_styles()}


def test_live_v5_characters_and_positions():
    imp = metadata.parse(_fixture("png_meta_v5_two_characters.json"))
    assert imp.model == "nai-diffusion-5-full" and not imp.model_guessed
    assert imp.prompt.startswith("2girls, park, bench") and imp.negative == "lowres"
    assert imp.positioned and [c.pos for c in imp.characters] == [(0.1, 0.5), (0.9, 0.5)]
    assert imp.characters[0].prompt == "girl, red hair, red dress"
    assert (imp.seed, imp.steps, imp.scale, imp.width, imp.height) == (123456789, 28, 5.0, 832, 1216)
    assert not imp.variety and imp.request_type == "PromptGenerateRequest" and imp.strength is None


def test_live_v4_5_charref_and_quality_tags():
    imp = metadata.parse(_fixture("png_meta_v4_5_charref.json"))
    assert imp.model == "nai-diffusion-4-5-full" and imp.char_ref_count == 1 and not imp.vibes
    prompt, negative, notes = metadata.fit_to_style(imp, _styles()["nai-diffusion-4-5-full"])
    assert prompt == "1girl, black hair, park, sitting on bench" and notes
    assert metadata.differences(imp, _styles()["nai-diffusion-4-5-full"]) == []


def test_live_refine_strength():
    imp = metadata.parse(_fixture("png_meta_v4_5_refine.json"))
    assert imp.request_type == "Img2ImgRequest" and imp.strength == 0.5
    assert "Refine" in metadata.describe(imp)


def test_png_roundtrip_vibes_variety_and_preset():
    st = _styles()["nai-diffusion-4-5-full"]
    comment = {"prompt": "1girl, smile, " + st.quality_tags, "uc": st.negative_preset + ", bad hands",
               "seed": 7, "steps": 23, "scale": 6.2, "sampler": "k_dpmpp_2m",
               "noise_schedule": "karras", "skip_cfg_above_sigma": 58.0, "width": 1024,
               "height": 1024, "reference_image_multiple": ["ENC"],
               "reference_strength_multiple": [0.45], "request_type": "PromptGenerateRequest"}
    png = _png({"Source": "NovelAI Diffusion V4.5 4BDE2A90", "Comment": json.dumps(comment)})
    imp = metadata.parse(metadata.read_chunks(png))
    assert imp.variety and imp.vibes == [("ENC", 0.45)] and imp.seed == 7
    prompt, negative, notes = metadata.fit_to_style(imp, st)
    assert prompt == "1girl, smile" and negative == "bad hands" and len(notes) == 2
    assert metadata.differences(imp, st) == ["sampler k_dpmpp_2m (style uses k_euler_ancestral)"]


def test_model_detection():
    assert metadata._model_from("NovelAI Diffusion V4.5 4BDE2A90", "") == ("nai-diffusion-4-5-full", False)
    assert metadata._model_from("NovelAI Diffusion V4.5 DEADBEEF", "") == ("nai-diffusion-4-5-full", True)
    assert metadata._model_from("NovelAI Diffusion V4 DEADBEEF", "") == ("nai-diffusion-4-full", True)
    assert metadata._model_from("", "NovelAI Diffusion V5") == ("nai-diffusion-5-full", True)
    assert metadata._model_from("Stable Diffusion XL 7BCCAA2C", "") == ("nai-diffusion-3", False)
    assert metadata._model_from("", "") == (None, True)


def test_no_metadata():
    for png in (_png({}), _png({"Comment": "not json"}), b"not a png"):
        try:
            metadata.parse(metadata.read_chunks(png))
        except metadata.NoMetadata:
            pass
        else:
            raise AssertionError(png[:20])


def _stealth_alpha(payload: bytes, magic: bytes, w: int, h: int):
    bits = []
    for byte in magic + struct.pack(">I", len(payload) * 8) + payload:
        bits += [(byte >> (7 - i)) & 1 for i in range(8)]
    assert len(bits) <= w * h
    bits += [0] * (w * h - len(bits))
    # column-major: bit index = x * h + y
    return lambda x, y: 254 | bits[x * h + y]


def test_stealth_alpha_fallback():
    comment = {"prompt": "1girl, rain", "uc": "lowres", "seed": 3, "steps": 28, "scale": 5,
               "width": 832, "height": 1216}
    data = {"Source": "NovelAI Diffusion V4.5 4BDE2A90", "Comment": comment}
    w, h = 40, 64
    alpha = _stealth_alpha(gzip.compress(json.dumps(data).encode()), b"stealth_pngcomp", w, h)
    imp = metadata.parse(metadata.read_chunks(_png({}), alpha, (w, h)))
    assert imp.prompt == "1girl, rain" and imp.model == "nai-diffusion-4-5-full"
    # plain variant holding just the Comment JSON
    alpha = _stealth_alpha(json.dumps(comment).encode(), b"stealth_pnginfo", 64, 64)
    assert metadata.parse(metadata.read_chunks(b"", alpha, (64, 64))).seed == 3
    # opaque image with no hidden data
    try:
        metadata.read_chunks(_png({}), lambda x, y: 255, (8, 8))
    except metadata.NoMetadata:
        pass
    else:
        raise AssertionError("expected NoMetadata")


def test_transparent_flag_imported():
    comment = {"prompt": "1girl, transparent background", "uc": "", "seed": 1,
               "straight_alpha": True, "tag_hint_transparent_background": True}
    png = _png({"Source": "NovelAI Diffusion V5 0ADF9AB7", "Comment": json.dumps(comment)})
    assert metadata.parse(metadata.read_chunks(png)).transparent
    assert not metadata.parse(_fixture("png_meta_v5_two_characters.json")).transparent
