"""Vibe Transfer helpers: NovelAI vibe files and the encoding cache. Krita-free.

Vibe Transfer works on V4 and V4.5 only (NovelAI rejects it on V5; caru-ini/
novelai-sdk enforces the same). An image has to be encoded per model and per
"information extracted" value through /ai/encode-vibe, reported at 2 Anlas each,
so encodings are cached on disk and reused.

.naiv4vibe files (from NovelAI's site) are JSON, checked against real vibe files:
  {"identifier": "novelai-vibe-transfer", "version": 1, "type": "encoding"|"image",
   "name": ..., "image": <base64, type "image" only>,
   "encodings": {"v4-5full": {<key>: {"encoding": <base64>,
                                      "params": {"information_extracted": 0.7}}}},
   "importInfo": {"model": ..., "information_extracted": ..., "strength": ...}}
"encoding"-type files keep one encoding under the key "unknown" with no params; its
information_extracted is the one in importInfo.
"""
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# model id -> key used inside vibe files
MODEL_KEYS = {
    "nai-diffusion-4-5-full": "v4-5full",
    "nai-diffusion-4-5-curated": "v4-5curated",
    "nai-diffusion-4-full": "v4full",
    "nai-diffusion-4-curated-preview": "v4curated",
}
VIBE_MODELS = set(MODEL_KEYS)
MAX_FREE_VIBES = 4          # more than 4 vibes in one request adds 2 Anlas each (reported)
ENCODE_COST = 2             # Anlas per new encoding (reported)


def supports_vibes(model_id: str) -> bool:
    return model_id in VIBE_MODELS


@dataclass
class VibeEntry:
    name: str
    image_b64: str = ""                       # original image, if the file has one
    encodings: Dict[str, Dict[float, str]] = field(default_factory=dict)  # model -> {ie: enc}
    strength: float = 0.6
    information_extracted: float = 1.0

    def encoding_for(self, model_id: str, information_extracted: float) -> Optional[str]:
        """An encoding for this model, preferring the requested information_extracted.
        Files without the original image can only offer what they contain."""
        options = self.encodings.get(model_id) or {}
        if not options:
            return None
        ie = round(information_extracted, 2)
        if ie in options:
            return options[ie]
        if not self.image_b64:   # encoding-only file: whatever it was encoded with
            return next(iter(options.values()))
        return None              # has the image: encode at the requested value instead

    @property
    def models(self) -> List[str]:
        return sorted(self.encodings)


def _entry_from_json(d: dict) -> VibeEntry:
    info = d.get("importInfo") or {}
    default_ie = round(float(info.get("information_extracted", 1.0)), 2)
    by_key = {v: k for k, v in MODEL_KEYS.items()}
    encodings: Dict[str, Dict[float, str]] = {}
    for key, entries in (d.get("encodings") or {}).items():
        model_id = by_key.get(key)
        if model_id is None or not isinstance(entries, dict):
            continue
        for item in entries.values():
            if not isinstance(item, dict) or not item.get("encoding"):
                continue
            ie = round(float((item.get("params") or {}).get("information_extracted",
                                                            default_ie)), 2)
            encodings.setdefault(model_id, {})[ie] = item["encoding"]
    return VibeEntry(name=d.get("name") or "vibe", image_b64=d.get("image") or "",
                     encodings=encodings, strength=float(info.get("strength", 0.6)),
                     information_extracted=default_ie)


def load_vibe_file(path: str) -> List[VibeEntry]:
    """All vibes in a .naiv4vibe or .naiv4vibebundle file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("vibes"), list):   # bundle
        items = data["vibes"]
    else:
        items = [data]
    entries = [_entry_from_json(d) for d in items if isinstance(d, dict)]
    if not entries:
        raise ValueError(f"No vibes found in {os.path.basename(path)}")
    return entries


def is_vibe_file(path: str) -> bool:
    return path.lower().endswith((".naiv4vibe", ".naiv4vibebundle"))


# ---------------------------------------------------------------- encoding cache
def cache_key(png: bytes, model_id: str, information_extracted: float) -> str:
    h = hashlib.sha256(png)
    h.update(f"|{model_id}|{round(information_extracted, 2)}".encode())
    return h.hexdigest()


class VibeCache:
    """Encodings on disk under the plugin's settings folder, one file per key."""

    def __init__(self, folder: str):
        self.folder = folder

    def _path(self, key: str) -> str:
        return os.path.join(self.folder, key + ".b64")

    def get(self, key: str) -> Optional[str]:
        try:
            with open(self._path(key), encoding="ascii") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def put(self, key: str, encoding: str):
        os.makedirs(self.folder, exist_ok=True)
        tmp = self._path(key) + ".tmp"
        with open(tmp, "w", encoding="ascii") as f:
            f.write(encoding)
        os.replace(tmp, self._path(key))

    def has(self, key: str) -> bool:
        return os.path.exists(self._path(key))
