"""Build NovelAI /ai/generate-image request bodies. Krita-free.

The body shape follows NovelAI's web client. Every field here still needs
checking against a live call; tools/verify_api.py saves the bodies it sends as
fixtures so tests can pin them once confirmed.
"""
import random
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from . import models

GENERATE, IMG2IMG, INFILL = "generate", "img2img", "infill"
GRID = 5  # NovelAI's character position grid is 5x5
INPAINT_MODELS = {"nai-diffusion-5-curated": "nai-diffusion-4-5-curated-inpainting"}
MAX_SEED = 4294967295


def grid_to_coord(index: int) -> float:
    """Grid cell 0..4 -> 0.1, 0.3, 0.5, 0.7, 0.9."""
    if not 0 <= index < GRID:
        raise ValueError(f"grid index {index} outside 0..{GRID - 1}")
    return round(index * 0.2 + 0.1, 1)


def coord_to_grid(value: float) -> int:
    """0..1 -> nearest grid cell."""
    return max(0, min(GRID - 1, int(value * GRID)))


def snap(value: float) -> float:
    """0..1 -> centre of its grid cell (0.1, 0.3, 0.5, 0.7 or 0.9)."""
    return grid_to_coord(coord_to_grid(value))


def clamp01(value: float) -> float:
    return round(min(1.0, max(0.0, float(value))), 3)


@dataclass
class Character:
    prompt: str
    uc: str = ""
    name: str = ""
    enabled: bool = True
    pos: Optional[tuple] = None    # (x, y), 0..1 across the image; None = AI's choice
    layer_id: str = ""             # linked Krita node uniqueId, if any
    cid: str = ""                  # stable id within the page's cast (not sent)

    def center(self, free: bool = True):
        """Position sent to NovelAI. Free (V5): the exact point. Otherwise snapped to
        the centre of its 5x5 grid cell, the only positions V4/V4.5 offer."""
        if self.pos is None:
            return {"x": 0.5, "y": 0.5}
        x, y = clamp01(self.pos[0]), clamp01(self.pos[1])
        if not free:
            x, y = snap(x), snap(y)
        return {"x": x, "y": y}


@dataclass
class VibeRef:
    """Vibe Transfer reference: an encoding made for this request's model."""
    encoding: str
    strength: float = 0.6
    name: str = ""


CHAR_REF_KINDS = ("character", "style", "character&style")


@dataclass
class CharRef:
    """Character Reference (V4.5 only). image_b64 is already padded to a
    NovelAI reference canvas (see document.reference_png)."""
    image_b64: str
    kind: str = "character&style"
    strength: float = 1.0
    fidelity: float = 1.0
    name: str = ""


@dataclass
class GenerationParams:
    prompt: str
    negative: str = ""
    model: str = models.DEFAULT_MODEL
    width: int = 832
    height: int = 1216
    steps: int = 28
    scale: float = 5.0             # guidance
    cfg_rescale: float = 0.0
    sampler: str = "k_euler_ancestral"
    noise_schedule: str = "karras"
    seed: int = 0                  # 0 = pick a random seed when the body is built
    n_samples: int = 1
    variety: bool = False
    quality_toggle: bool = True    # metadata only; quality text is already in `prompt`
    uc_preset: int = 0             # metadata only; preset text is already in `negative`
    characters: List[Character] = field(default_factory=list)
    vibes: List[VibeRef] = field(default_factory=list)
    char_refs: List[CharRef] = field(default_factory=list)
    vibe_encodes: int = 0          # new encodings this request needs (cost only)
    action: str = GENERATE
    image_b64: str = ""            # img2img / infill base image (PNG, base64)
    mask_b64: str = ""             # infill mask (PNG, base64)
    strength: float = 0.7          # img2img
    noise: float = 0.0             # img2img
    inpaint_strength: float = 1.0  # infill: below 1 repaints the masked area only partly
    add_original_image: bool = True
    transparent: bool = False      # V5: PNG with a transparent background (new images only)

    @property
    def info(self) -> models.ModelInfo:
        return models.get(self.model)

    @property
    def uses_base_image(self) -> bool:
        return self.action in (IMG2IMG, INFILL)

    def enabled_characters(self) -> List[Character]:
        if not self.info.has_characters:
            return []
        return [c for c in self.characters if c.enabled and c.prompt.strip()]

    def with_seed(self) -> "GenerationParams":
        """Copy with a concrete seed (random if seed is 0)."""
        d = asdict(self)
        d["characters"] = [Character(**c) for c in d["characters"]]
        d["vibes"] = [VibeRef(**v) for v in d["vibes"]]
        d["char_refs"] = [CharRef(**c) for c in d["char_refs"]]
        p = GenerationParams(**d)
        if not p.seed:
            p.seed = random.randint(1, MAX_SEED)
        return p


def variety_sigma(info: models.ModelInfo, width: int, height: int) -> float:
    # Scales with resolution, anchored at 832x1216 (behaviour seen in other clients).
    return round(info.variety_sigma * ((width * height) / 1011712) ** 0.5, 4)


def build_body(p: GenerationParams) -> dict:
    """Return the JSON body for /ai/generate-image. Seed must already be set."""
    info = p.info
    if p.width % 64 or p.height % 64:
        raise ValueError(f"size {p.width}x{p.height} is not a multiple of 64")
    if not p.seed:
        raise ValueError("seed must be set; call GenerationParams.with_seed() first")
    chars = p.enabled_characters()
    if len(chars) > info.max_characters:
        raise ValueError(f"{info.label} takes at most {info.max_characters} characters")

    model_id = p.model
    if p.action == INFILL:
        # Verified live for V4.5 Full and V5 Full. V5 Curated has no inpainting model
        # of its own and uses V4.5 Curated's (per caru-ini/novelai-sdk; unverified).
        model_id = INPAINT_MODELS.get(p.model, f"{p.model}-inpainting")

    params = {
        "params_version": info.params_version,
        "width": p.width,
        "height": p.height,
        "scale": p.scale,
        "sampler": p.sampler,
        "steps": p.steps,
        "seed": p.seed,
        "n_samples": p.n_samples,
        "ucPreset": p.uc_preset,
        "qualityToggle": p.quality_toggle,
        "dynamic_thresholding": False,
        "controlnet_strength": 1.0,
        "legacy": False,
        "add_original_image": p.add_original_image,
        "cfg_rescale": p.cfg_rescale,
        "noise_schedule": p.noise_schedule,
        "legacy_v3_extend": False,
        "skip_cfg_above_sigma": variety_sigma(info, p.width, p.height) if p.variety else None,
        "negative_prompt": p.negative,
    }
    if p.transparent:
        # V5 only (caru-ini/novelai-sdk validates the same); the site also adds the tag
        # "transparent background" to the prompt, which the docker does.
        if info.family != models.V5:
            raise ValueError(f"Transparent backgrounds work on V5 only, not on {info.label}.")
        if p.action != GENERATE:
            raise ValueError("Transparent backgrounds are for new images, not Refine or Fill.")
        params["straight_alpha"] = True
        params["tag_hint_transparent_background"] = True
    if p.sampler == "k_euler_ancestral" and p.noise_schedule != "native":
        params["deliberate_euler_ancestral_bug"] = False
        params["prefer_brownian"] = True

    if info.is_v4_style:
        # NovelAI positions all characters or none (use_coords is per request), so a
        # mix would silently put the "AI's choice" ones at the centre.
        use_coords = any(c.pos is not None for c in chars)
        if use_coords and not all(c.pos is not None for c in chars):
            raise ValueError("Either every character has a position or none does: "
                             "NovelAI positions all characters or lets the AI place all of them.")
        free = info.free_positions
        params["use_coords"] = use_coords
        params["characterPrompts"] = [
            {"prompt": c.prompt, "uc": c.uc, "center": c.center(free), "enabled": True}
            for c in chars
        ]
        params["v4_prompt"] = {
            "caption": {
                "base_caption": p.prompt,
                "char_captions": [{"char_caption": c.prompt, "centers": [c.center(free)]}
                                  for c in chars],
            },
            "use_coords": use_coords,
            "use_order": True,  # VERIFY: website default
        }
        params["v4_negative_prompt"] = {
            "caption": {
                "base_caption": p.negative,
                "char_captions": [{"char_caption": c.uc, "centers": [c.center(free)]}
                                  for c in chars],
            },
            "use_coords": use_coords,
            "use_order": True,
        }
    else:
        params["sm"] = False
        params["sm_dyn"] = False
        params["uc"] = p.negative

    if p.vibes and p.char_refs:
        # NovelAI refuses the combination (found in testing, 2026-11-13; NovelAI's web app disables one).
        raise ValueError("Vibe Transfer and Character Reference can't be used together; "
                         "turn one of them off.")
    if p.vibes:
        from . import vibes as _vibes
        if not _vibes.supports_vibes(p.model):
            raise ValueError(f"Vibe Transfer works on V4 and V4.5, not on {info.label}.")
        params["reference_image_multiple"] = [v.encoding for v in p.vibes]
        params["reference_strength_multiple"] = [round(v.strength, 2) for v in p.vibes]
        params["normalize_reference_strength_multiple"] = False
    if p.char_refs:
        if info.family != models.V4_5:
            raise ValueError(f"Character Reference works on V4.5 only, not on {info.label}.")
        params["director_reference_images"] = [c.image_b64 for c in p.char_refs]
        params["director_reference_descriptions"] = [
            {"caption": {"base_caption": c.kind, "char_captions": []}, "legacy_uc": False}
            for c in p.char_refs]
        params["director_reference_strength_values"] = [round(c.strength, 2) for c in p.char_refs]
        params["director_reference_secondary_strength_values"] = [
            round(1 - c.fidelity, 2) for c in p.char_refs]
        params["director_reference_information_extracted"] = [1.0] * len(p.char_refs)

    if p.action == IMG2IMG:
        if not p.image_b64:
            raise ValueError("img2img needs a base image")
        params.update(image=p.image_b64, strength=p.strength, noise=p.noise,
                      extra_noise_seed=p.seed)
    elif p.action == INFILL:
        if not (p.image_b64 and p.mask_b64):
            raise ValueError("infill needs a base image and a mask")
        params.update(image=p.image_b64, mask=p.mask_b64, extra_noise_seed=p.seed)
        if p.inpaint_strength < 1.0:
            # Partial repaint of the masked area with the whole image as context (the
            # website's inpaint strength). Shape taken from caru-ini/novelai-sdk (MIT):
            # a nested "img2img" object plus inpaintImg2ImgStrength. Verified live
            # 2026-11-13 on V5: at 0.4 the face kept the original expression and pose
            # (mask diff 9.9 vs 18.2 at 1.0). Top-level inpaintImg2ImgStrength + strength
            # without the nested object was ignored (identical output at 1.0 and 0.4).
            s = round(max(0.01, p.inpaint_strength), 2)
            params.update(inpaintImg2ImgStrength=s,
                          img2img={"strength": s, "color_correct": True})

    return {"input": p.prompt, "model": model_id, "action": p.action, "parameters": params}


def redact(body: dict) -> dict:
    """Copy of a body safe for logs and fixtures: image data is shortened."""
    out = dict(body)
    params = dict(body.get("parameters", {}))
    for key in ("image", "mask"):
        if params.get(key):
            params[key] = f"<{len(params[key])} base64 chars>"
    for key in ("reference_image_multiple", "director_reference_images"):
        if params.get(key):
            params[key] = [f"<{len(x)} base64 chars>" for x in params[key]]
    out["parameters"] = params
    return out
