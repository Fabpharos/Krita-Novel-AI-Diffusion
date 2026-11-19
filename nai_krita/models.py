"""NovelAI model table: ids, families and per-family limits.

Krita-free. Values marked VERIFY come from third-party reports and still need a
live check (tools/verify_api.py).
"""
from dataclasses import dataclass

V3, V4, V4_5, V5 = "v3", "v4", "v4.5", "v5"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    family: str
    max_characters: int      # 0 = no character prompts. Live 2026-11-13: V4.5 takes 6 (a 7th
                             # gets a bare "500 Internal Server Error", so the plugin refuses
                             # it first); V5 accepted 23, so 22 is a safe limit, not NovelAI's
    params_version: int      # VERIFY: 4 for V5, 3 for V4/V4.5; V3 reported as 2 or 3
    variety_sigma: float     # skip_cfg_above_sigma at ~832x1216 when Variety is on; VERIFY for V5
    free_positions: bool = False  # character centres anywhere (V5) vs the 5x5 grid

    @property
    def has_characters(self) -> bool:
        return self.max_characters > 0

    @property
    def is_v4_style(self) -> bool:
        """V4 and later take v4_prompt / characterPrompts; V3 does not."""
        return self.family != V3


MODELS = [
    ModelInfo("nai-diffusion-5-full", "V5 Full", V5, 22, 4, 58.0, free_positions=True),
    ModelInfo("nai-diffusion-5-curated", "V5 Curated", V5, 22, 4, 58.0, free_positions=True),
    ModelInfo("nai-diffusion-4-5-full", "V4.5 Full", V4_5, 6, 3, 58.0),
    ModelInfo("nai-diffusion-4-5-curated", "V4.5 Curated", V4_5, 6, 3, 58.0),
    ModelInfo("nai-diffusion-4-full", "V4 Full", V4, 6, 3, 19.0),
    ModelInfo("nai-diffusion-4-curated-preview", "V4 Curated", V4, 6, 3, 19.0),
    ModelInfo("nai-diffusion-3", "V3 Anime", V3, 0, 3, 19.0),
]
BY_ID = {m.id: m for m in MODELS}
DEFAULT_MODEL = "nai-diffusion-5-full"


def get(model_id: str) -> ModelInfo:
    try:
        return BY_ID[model_id]
    except KeyError:
        raise ValueError(f"Unknown NovelAI model id: {model_id}") from None


SAMPLERS = ["k_euler_ancestral", "k_euler", "k_dpmpp_2s_ancestral", "k_dpmpp_2m",
            "k_dpmpp_2m_sde", "k_dpmpp_sde", "ddim_v3"]
NOISE_SCHEDULES = ["karras", "native", "exponential", "polyexponential"]

# NovelAI's own defaults, per model: the quality tags its site appends when "Add
# quality tags" is on, and its undesired-content presets (put before the user's
# text). Source: caru-ini/novelai-sdk (MIT), constants/negative_prompts.py, which
# mirrors the web UI; checked against the site's V4.5 strings we had before.
# The site also prepends "nsfw" to the undesired content on non-curated models
# unless the prompt mentions nsfw; the plugin never adds that on its own.
_Q_45 = "very aesthetic, masterpiece, no text"
_UC_V45_HEAVY = ("lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, "
                 "jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, "
                 "screentone, multiple views, logo, too many watermarks, negative space, "
                 "blank page")
_UC_HUMAN = _UC_V45_HEAVY + ", @_@, mismatched pupils, glowing eyes, bad anatomy"
_UC_FURRY = ("{worst quality}, distracting watermark, unfinished, bad quality, {widescreen}, "
             "upscale, {sequence}, {{grandfathered content}}, blurred foreground, chromatic "
             "aberration, sketch, everyone, [sketch background], simple, [flat colors], "
             "ych (character), outline, multiple scenes, [[horror (theme)]], comic")
_UC_V5 = {
    "Heavy": _UC_V45_HEAVY,
    "Light": "lowres, bad hands, bad anatomy, artistic error, sepia, white haze, worst quality, "
             "very displeasing, jpeg artifacts, 0::ai-generated::",
    "Human Focus": _UC_HUMAN,
    "Furry Focus": _UC_FURRY,
}

NAI_QUALITY = {
    "nai-diffusion-5-full": _Q_45,
    "nai-diffusion-5-curated": _Q_45,
    "nai-diffusion-4-5-full": _Q_45,
    "nai-diffusion-4-5-curated": _Q_45 + ", -0.8::feet::, rating:general",
    "nai-diffusion-4-full": "no text, best quality, very aesthetic, absurdres",
    "nai-diffusion-4-curated-preview": "rating:general, best quality, very aesthetic, absurdres",
    "nai-diffusion-3": "best quality, amazing quality, very aesthetic, absurdres",
}
NAI_UC = {   # model -> preset name -> text ("None" is the empty preset, left out)
    "nai-diffusion-5-full": _UC_V5,
    "nai-diffusion-5-curated": _UC_V5,
    "nai-diffusion-4-5-full": {
        "Heavy": _UC_V45_HEAVY,
        "Light": "lowres, artistic error, scan artifacts, worst quality, bad quality, "
                 "jpeg artifacts, multiple views, very displeasing, too many watermarks, "
                 "negative space, blank page",
        "Human Focus": _UC_HUMAN,
        "Furry Focus": _UC_FURRY,
    },
    "nai-diffusion-4-5-curated": {
        "Heavy": "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, "
                 "worst quality, bad quality, jpeg artifacts, very displeasing, chromatic "
                 "aberration, halftone, multiple views, logo, too many watermarks, negative "
                 "space, blank page",
        "Light": "blurry, lowres, upscaled, artistic error, scan artifacts, jpeg artifacts, "
                 "logo, too many watermarks, negative space, blank page",
        "Human Focus": "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, "
                       "bad anatomy, bad hands, worst quality, bad quality, jpeg artifacts, "
                       "very displeasing, chromatic aberration, halftone, multiple views, logo, "
                       "too many watermarks, @_@, mismatched pupils, glowing eyes, negative "
                       "space, blank page",
    },
    "nai-diffusion-4-full": {
        "Heavy": "blurry, lowres, error, film grain, scan artifacts, worst quality, bad quality, "
                 "jpeg artifacts, very displeasing, chromatic aberration, multiple views, logo, "
                 "too many watermarks, white blank page, blank page",
        "Light": "blurry, lowres, error, worst quality, bad quality, jpeg artifacts, "
                 "very displeasing, white blank page, blank page",
    },
    "nai-diffusion-4-curated-preview": {
        "Heavy": "blurry, lowres, error, film grain, scan artifacts, worst quality, bad quality, "
                 "jpeg artifacts, very displeasing, chromatic aberration, logo, dated, "
                 "signature, multiple views, gigantic breasts, white blank page, blank page",
        "Light": "blurry, lowres, error, worst quality, bad quality, jpeg artifacts, "
                 "very displeasing, logo, dated, signature, white blank page, blank page",
    },
    "nai-diffusion-3": {
        "Heavy": "lowres, {bad}, error, fewer, extra, missing, worst quality, jpeg artifacts, "
                 "bad quality, watermark, unfinished, displeasing, chromatic aberration, "
                 "signature, extra digits, artistic error, username, scan, [abstract]",
        "Light": "lowres, jpeg artifacts, worst quality, watermark, blurry, very displeasing",
        "Human Focus": "lowres, {bad}, error, fewer, extra, missing, worst quality, jpeg "
                       "artifacts, bad quality, watermark, unfinished, displeasing, chromatic "
                       "aberration, signature, extra digits, artistic error, username, scan, "
                       "[abstract], bad anatomy, bad hands, @_@, mismatched pupils, "
                       "heart-shaped pupils, glowing eyes",
    },
}
# Note for manga work: Heavy and Human Focus list "halftone, screentone" on V4.5/V5.

# Per family (the Full model's values), used by the built-in styles and tools.
QUALITY_TAGS = {
    V3: NAI_QUALITY["nai-diffusion-3"],
    V4: NAI_QUALITY["nai-diffusion-4-full"],
    V4_5: NAI_QUALITY["nai-diffusion-4-5-full"],
    V5: "",   # the built-in V5 style starts without; load them from the prompt's Presets menu
}
UC_PRESETS = {
    V3: NAI_UC["nai-diffusion-3"],
    V4: NAI_UC["nai-diffusion-4-full"],
    V4_5: NAI_UC["nai-diffusion-4-5-full"],
    V5: {},
}
