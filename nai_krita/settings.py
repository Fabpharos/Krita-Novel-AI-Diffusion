"""Plugin settings, styles and saved characters as JSON on disk. Krita-free.

The token lives here, in Krita's app data folder, and nowhere else: never in a
document, a log, a fixture or an autosaved PNG.
"""
import json
import os
from dataclasses import dataclass, field, asdict, fields
from typing import List

from . import models

SETTINGS_FILE = "nai_krita_settings.json"


@dataclass
class Style:
    name: str
    model: str = models.DEFAULT_MODEL
    sampler: str = "k_euler_ancestral"
    noise_schedule: str = "karras"
    steps: int = 28
    scale: float = 5.0
    cfg_rescale: float = 0.0
    variety: bool = False
    quality_tags: str = ""      # appended to the prompt; shown, not hidden
    negative_preset: str = ""   # prepended to the negative prompt
    prompt_prefix: str = ""     # prepended to the prompt

    def compose_prompt(self, prompt: str) -> str:
        """Prefix, the prompt, then the style's quality tags. Tags already in the
        prompt (e.g. loaded from the Presets menu) aren't added twice."""
        from .presets import merge
        parts = [self.prompt_prefix.strip(" ,"), prompt.strip(" ,")]
        return merge(", ".join(p for p in parts if p), self.quality_tags)

    def compose_negative(self, negative: str) -> str:
        """The style's undesired-content preset, then the user's text; preset tags
        already in the text aren't added twice."""
        from .presets import merge
        return merge(negative.strip(" ,"), self.negative_preset, at_start=True)


def default_styles() -> List[Style]:
    out = []
    for model_id, preset in (("nai-diffusion-5-full", None),
                             ("nai-diffusion-4-5-full", "Light"),
                             ("nai-diffusion-4-5-curated", "Light"),
                             ("nai-diffusion-4-full", "Light"),
                             ("nai-diffusion-3", "Light")):
        info = models.get(model_id)
        out.append(Style(
            name=info.label, model=model_id,
            sampler="k_euler_ancestral",
            noise_schedule="native" if info.family == models.V3 else "karras",
            quality_tags=models.QUALITY_TAGS.get(info.family, ""),
            negative_preset=models.UC_PRESETS.get(info.family, {}).get(preset, "") if preset else "",
        ))
    return out


@dataclass
class Settings:
    token: str = ""
    autosave_dir: str = ""
    default_style: str = "V5 Full"
    free_only: bool = True          # block paid requests outright
    read_panel_names: bool = False
    timeout: int = 120              # seconds per request
    fill_context: str = "whole"     # Fill/Refine selection: "whole" panel/page or "around" it
    tag_autocomplete: bool = True   # Danbooru tag suggestions in prompt boxes
    multi_mode: str = "queue"       # several images per click: "queue" or "batch"
    styles: List[Style] = field(default_factory=default_styles)
    saved_characters: dict = field(default_factory=dict)   # name -> list of character dicts
    tag_presets: dict = field(default_factory=dict)   # {"positive"|"negative": {name: text}}
    preset_replace: bool = False    # loading a preset replaces the box's text instead of adding

    def style(self, name: str) -> Style:
        for s in self.styles:
            if s.name == name:
                return s
        return self.styles[0] if self.styles else Style(name="Default")

    # ---- persistence ----
    @classmethod
    def load(cls, folder: str) -> "Settings":
        path = os.path.join(folder, SETTINGS_FILE)
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known and k != "styles"})
        if data.get("styles"):
            style_keys = {f.name for f in fields(Style)}
            s.styles = [Style(**{k: v for k, v in st.items() if k in style_keys})
                        for st in data["styles"]]
        return s

    def save(self, folder: str) -> None:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, SETTINGS_FILE)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def redacted(self) -> dict:
        d = asdict(self)
        d["token"] = "<set>" if self.token else "<empty>"
        return d
