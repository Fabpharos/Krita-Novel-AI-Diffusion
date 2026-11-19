"""Tag presets: merging a set of tags into a prompt without duplicates. Krita-free.

Saved presets live in the settings file (Settings.tag_presets), one list for the
prompt and one for the undesired content. NovelAI's own quality tags and
undesired-content presets come from models.NAI_QUALITY / models.NAI_UC.
"""
import re
from typing import Dict, List

from . import models, tags

POSITIVE, NEGATIVE = "positive", "negative"


def split_tags(text: str) -> List[str]:
    """The comma/newline-separated pieces of a prompt, as written (weights kept)."""
    return [p.strip() for p in re.split(r"[,\n]", text or "") if p.strip()]


def _key(piece: str) -> str:
    keys = tags.tags_in(piece)
    return next(iter(keys)) if keys else ""


def missing_tags(text: str, preset: str) -> List[str]:
    """Pieces of `preset` whose tag isn't in `text` yet (weights and brackets
    ignored when comparing, so "{{masterpiece}}" counts as masterpiece)."""
    have = tags.tags_in(text or "")
    out, seen = [], set()
    for piece in split_tags(preset):
        k = _key(piece)
        if not k or k in have or k in seen:
            continue
        seen.add(k)
        out.append(piece)
    return out


def merge(text: str, preset: str, at_start: bool = False) -> str:
    """`text` with the preset's missing tags added at the end (or the start, as
    NovelAI does with undesired-content presets). Existing text is kept as typed."""
    add = missing_tags(text, preset)
    if not add:
        return text
    body = (text or "").strip(" ,\n\t")
    joined = ", ".join(add)
    if not body:
        return joined
    return f"{joined}, {body}" if at_start else f"{body}, {joined}"


# ---------------------------------------------------------------- saved presets
def empty_store() -> Dict[str, Dict[str, str]]:
    return {POSITIVE: {}, NEGATIVE: {}}


def normalize_store(store) -> Dict[str, Dict[str, str]]:
    """A valid store from whatever was in the settings file."""
    out = empty_store()
    if isinstance(store, dict):
        for kind in (POSITIVE, NEGATIVE):
            items = store.get(kind)
            if isinstance(items, dict):
                out[kind] = {str(k): str(v) for k, v in items.items() if str(k).strip()}
    return out


def sorted_names(store, kind) -> List[str]:
    return sorted(store.get(kind, {}), key=str.lower)


# ---------------------------------------------------------------- NovelAI defaults
def nai_quality(model_id: str) -> str:
    return models.NAI_QUALITY.get(model_id, "")


def nai_uc_presets(model_id: str) -> Dict[str, str]:
    return dict(models.NAI_UC.get(model_id, {}))
