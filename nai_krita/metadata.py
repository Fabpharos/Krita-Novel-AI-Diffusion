"""Read generation settings back out of a NovelAI PNG. Krita-free.

NovelAI writes its settings into every image it returns (checked against the live
fixtures in tools/out, 2026-11-13):
  tEXt "Source":  "NovelAI Diffusion V4.5 4BDE2A90" (family + model hash)
  tEXt "Comment": JSON with prompt, uc, seed, steps, scale, cfg_rescale, sampler,
                  noise_schedule, width, height, skip_cfg_above_sigma (Variety+),
                  v4_prompt / v4_negative_prompt (character captions and centres),
                  reference_image_multiple / reference_strength_multiple (vibe
                  encodings), director_reference_* (strengths only, no images),
                  request_type and, for Refine, strength.
Images that lost their text chunks (e.g. re-saved by some apps) may still carry the
same data hidden in the alpha channel's lowest bits ("stealth" metadata, as written
by NovelAI's site and read by the SD WebUI): magic "stealth_pngcomp" (gzip) or
"stealth_pnginfo" (plain), a 32-bit bit length, then the JSON, read column by column.
That path is implemented to the published format but not yet checked on a real file.
"""
import gzip
import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from . import models
from .api import png_text_chunks

# Model hash in "Source" -> model id. Hashes measured on live images; curated
# models' hashes are unknown, so those fall back to the family's Full model.
MODEL_HASHES = {
    "0ADF9AB7": "nai-diffusion-5-full",
    "657484A5": "nai-diffusion-5-full",       # V5 Full inpainting
    "4BDE2A90": "nai-diffusion-4-5-full",
    "1229B44F": "nai-diffusion-4-5-full",     # V4.5 Full inpainting
    "37442FCA": "nai-diffusion-4-full",
    "7BCCAA2C": "nai-diffusion-3",
}
FAMILY_DEFAULTS = [   # (text in "Source" / model_name, model id), most specific first
    ("V4.5", "nai-diffusion-4-5-full"),
    ("V4", "nai-diffusion-4-full"),
    ("V5", "nai-diffusion-5-full"),
    ("Stable Diffusion XL", "nai-diffusion-3"),
]
STEALTH_MAGICS = {b"stealth_pngcomp": True, b"stealth_pnginfo": False}


class NoMetadata(ValueError):
    pass


@dataclass
class ImportedCharacter:
    prompt: str
    uc: str = ""
    pos: Optional[Tuple[float, float]] = None


@dataclass
class Imported:
    prompt: str = ""
    negative: str = ""
    model: Optional[str] = None
    model_guessed: bool = False        # family known, exact model (Full/Curated) not
    seed: int = 0
    steps: int = 28
    scale: float = 5.0
    cfg_rescale: float = 0.0
    sampler: str = ""
    noise_schedule: str = ""
    variety: bool = False
    width: int = 0
    height: int = 0
    characters: List[ImportedCharacter] = field(default_factory=list)
    positioned: bool = False
    vibes: List[Tuple[str, float]] = field(default_factory=list)   # (encoding, strength)
    char_ref_count: int = 0             # images are not stored in the PNG
    request_type: str = ""
    strength: Optional[float] = None    # Refine (img2img) strength
    transparent: bool = False           # V5 transparent background


# ---------------------------------------------------------------- reading
def read_chunks(png: bytes, alpha: Optional[Callable[[int, int], int]] = None,
                size: Tuple[int, int] = (0, 0)) -> dict:
    """Text chunks holding NovelAI settings. Falls back to stealth metadata when
    `alpha(x, y)` (0-255) and the image size are given."""
    chunks = {}
    try:
        chunks = png_text_chunks(png) if png[:8] == b"\x89PNG\r\n\x1a\n" else {}
    except Exception:
        chunks = {}
    if chunks.get("Comment"):
        return chunks
    if alpha is not None and size[0] and size[1]:
        stealth = read_stealth(alpha, *size)
        if stealth:
            return stealth
    raise NoMetadata("This image has no NovelAI settings in it. Images saved from "
                     "NovelAI keep them; screenshots and re-saved copies often don't.")


def read_stealth(alpha: Callable[[int, int], int], width: int, height: int) -> Optional[dict]:
    """Decode stealth metadata from the alpha channel's lowest bits, column-major."""
    total = width * height

    def bits():
        for x in range(width):
            for y in range(height):
                yield alpha(x, y) & 1

    it = bits()

    def read_bytes(n):
        out = bytearray()
        for _ in range(n):
            b = 0
            for _ in range(8):
                b = (b << 1) | next(it)
            out.append(b)
        return bytes(out)

    try:
        magic = read_bytes(15)
        if magic not in STEALTH_MAGICS:
            return None
        length = int.from_bytes(read_bytes(4), "big")
        if length <= 0 or length > total - (19 * 8) or length % 8:
            return None
        data = read_bytes(length // 8)
    except StopIteration:
        return None
    try:
        if STEALTH_MAGICS[magic]:
            data = gzip.decompress(data)
        d = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    # Either the chunk dict itself, or the bare Comment JSON
    if "Comment" not in d and "prompt" in d:
        d = {"Comment": json.dumps(d)}
    elif isinstance(d.get("Comment"), dict):
        d["Comment"] = json.dumps(d["Comment"])
    return {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in d.items()}


def _model_from(source: str, model_name: str):
    """(model id or None, guessed)"""
    m = re.search(r"\b([0-9A-F]{8})\s*$", source or "")
    if m and m.group(1) in MODEL_HASHES:
        return MODEL_HASHES[m.group(1)], False
    text = f"{model_name or ''} {source or ''}"
    for needle, model_id in FAMILY_DEFAULTS:
        if needle in text:
            return model_id, True
    return None, True


def _caption(block) -> Tuple[str, list]:
    cap = (block or {}).get("caption") or {}
    return cap.get("base_caption", ""), cap.get("char_captions") or []


def parse(chunks: dict) -> Imported:
    try:
        c = json.loads(chunks.get("Comment") or "")
    except ValueError:
        raise NoMetadata("The image's NovelAI settings are unreadable.") from None
    if not isinstance(c, dict) or "prompt" not in c and "v4_prompt" not in c:
        raise NoMetadata("The image's metadata is not from NovelAI.")
    out = Imported()
    out.model, out.model_guessed = _model_from(chunks.get("Source", ""), c.get("model_name", ""))
    base, char_caps = _caption(c.get("v4_prompt"))
    neg_base, neg_caps = _caption(c.get("v4_negative_prompt"))
    out.prompt = base or c.get("prompt", "")
    out.negative = neg_base or c.get("uc", "")
    out.positioned = bool((c.get("v4_prompt") or {}).get("use_coords"))
    for i, cc in enumerate(char_caps):
        centers = cc.get("centers") or []
        pos = None
        if out.positioned and centers:
            pos = (float(centers[0].get("x", 0.5)), float(centers[0].get("y", 0.5)))
        uc = neg_caps[i].get("char_caption", "") if i < len(neg_caps) else ""
        out.characters.append(ImportedCharacter(cc.get("char_caption", ""), uc, pos))
    out.seed = int(c.get("seed") or 0)
    out.steps = int(c.get("steps") or 28)
    out.scale = float(c.get("scale") if c.get("scale") is not None else 5.0)
    out.cfg_rescale = float(c.get("cfg_rescale") or 0.0)
    out.sampler = c.get("sampler") or ""
    out.noise_schedule = c.get("noise_schedule") or ""
    out.variety = c.get("skip_cfg_above_sigma") is not None
    out.width, out.height = int(c.get("width") or 0), int(c.get("height") or 0)
    encs = c.get("reference_image_multiple") or []
    strengths = c.get("reference_strength_multiple") or []
    out.vibes = [(e, float(strengths[i]) if i < len(strengths) else 0.6)
                 for i, e in enumerate(encs) if isinstance(e, str) and e]
    out.char_ref_count = len(c.get("director_reference_strengths") or [])
    out.transparent = bool(c.get("straight_alpha") or c.get("tag_hint_transparent_background"))
    out.request_type = c.get("request_type") or ""
    if out.request_type == "Img2ImgRequest" and c.get("strength") is not None:
        out.strength = float(c["strength"])
    return out


# ---------------------------------------------------------------- style matching
def _strip_suffix(text: str, tags: str) -> Tuple[str, bool]:
    t, tags = text.strip(" ,"), tags.strip(" ,")
    if tags and t.endswith(tags):
        return t[:-len(tags)].rstrip(" ,"), True
    return t, False


def _strip_prefix(text: str, tags: str) -> Tuple[str, bool]:
    t, tags = text.strip(" ,"), tags.strip(" ,")
    if tags and t.startswith(tags):
        return t[len(tags):].lstrip(" ,"), True
    return t, False


def fit_to_style(imp: Imported, style) -> Tuple[str, str, List[str]]:
    """The prompt and undesired content as they should go into the docker for
    `style`: the style adds its own prefix, quality tags and preset again when it
    generates, so those are removed here to avoid doubling them. Returns
    (prompt, negative, notes)."""
    notes = []
    prompt, negative = imp.prompt, imp.negative
    prompt, cut = _strip_prefix(prompt, style.prompt_prefix)
    prompt, cut_q = _strip_suffix(prompt, style.quality_tags)
    if cut_q:
        notes.append("Quality tags removed from the prompt (the style adds them).")
    negative, cut_n = _strip_prefix(negative, style.negative_preset)
    if cut_n:
        notes.append("Undesired-content preset removed (the style adds it).")
    return prompt, negative, notes


def differences(imp: Imported, style) -> List[str]:
    """Style-level settings the docker can't change that differ from the image."""
    out = []
    if imp.sampler and imp.sampler != style.sampler:
        out.append(f"sampler {imp.sampler} (style uses {style.sampler})")
    if imp.noise_schedule and imp.noise_schedule != style.noise_schedule:
        out.append(f"noise schedule {imp.noise_schedule} (style uses {style.noise_schedule})")
    if abs(imp.cfg_rescale - style.cfg_rescale) > 1e-6:
        out.append(f"guidance rescale {imp.cfg_rescale:g} (style uses {style.cfg_rescale:g})")
    return out


def describe(imp: Imported) -> str:
    label = models.get(imp.model).label if imp.model else "unknown model"
    if imp.model and imp.model_guessed:
        label += " (Full/Curated not recorded; assumed Full)"
    kind = {"Img2ImgRequest": "Refine", "NativeInfillingRequest": "Fill"}.get(imp.request_type,
                                                                              "Generate")
    return f"{label}, {imp.width}×{imp.height}, seed {imp.seed}, {kind}"
