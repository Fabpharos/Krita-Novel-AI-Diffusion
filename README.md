# NovelAI plugin for Krita

A Krita docker that generates images with NovelAI, modelled on Krita AI Diffusion.

**Status:** phases 0–6 verified (live API, Krita 5.3.2): generation, characters, panels, Refine/Fill, Vibe Transfer, Character Reference (not combinable with vibes) and 2× upscale. Phases 7 (import settings from a NovelAI PNG) and 8 (Danbooru tag autocomplete) verified in Krita. Phase 9 (a panel marker shared with `manga_layout.py` and the manga-page-layout skill) verified in Krita. Phase 10 (tag presets: your own, plus NovelAI's quality tags and undesired-content presets per model) verified in Krita. V5 transparent backgrounds verified live and in Krita.

## Install (Krita 5.x or 6.x)

1. `python tools/package.py` builds `dist/nai_krita.zip`.
2. In Krita: **Tools › Scripts › Import Python Plugin from File…**, pick the ZIP, and restart Krita.
3. Enable **NovelAI Image Generation** in **Settings › Configure Krita › Python Plugin Manager** if it isn't already, then restart.
4. **Settings › Dockers › NovelAI**. Click ⚙ and paste your persistent API token (NovelAI › Account Settings › Get Persistent API Token).

Settings live in `%APPDATA%\krita\nai_krita_settings.json`. "Free generations only" is on by default.

## Phase 0: check the API (outside Krita)

```
python tools/verify_api.py
python tools/verify_api.py --generate --characters
```

The script asks for your token (input hidden) unless it finds one in the `NAI_TOKEN` environment variable or the plugin's settings. To avoid pasting it each time, set the variable for the current terminal: in PowerShell `$env:NAI_TOKEN = "pst-..."`, in cmd.exe `set NAI_TOKEN=pst-...`.

The first command only reads balance endpoints. The second sends one free-size image per model family (V4.5, V5, V4, V3) plus a two-character V4.5 image. Each request is skipped unless the free test passes. V5 uses your Opus V5 allowance. Fixtures go to `tests/fixtures/` (no token, no image data) and images go to `tools/out/`.

## Tests

```
python tests/run_tests.py
```

This covers the Krita-free modules (`api`, `request`, `cost`, `resolution`, `panels`, `settings`) and needs no pytest.

## Licence

GPL-3.0 (see [LICENSE](LICENSE)), the same as Krita AI Diffusion, whose design this plugin follows.

## Layout

| Path | What |
| --- | --- |
| `nai_krita/api.py` | urllib client, ZIP unpacking, error messages, retry once on 429 |
| `nai_krita/request.py` | Request bodies per model family, character prompts, 5×5 grid |
| `nai_krita/cost.py` | Free test, cost estimate, balance parsing |
| `nai_krita/regions.py` | Refine/Fill areas: selection padding, aspect growth |
| `nai_krita/resolution.py` | Canvas/panel rect → NovelAI size, cover-scaling back |
| `nai_krita/panels.py` | Panel groups: `nai-panel` markers, group names, shot tags |
| `manga_layout.py` | Page layout script (source of truth for the skill's copy); writes the panel markers |
| `skill/manga-page-layout/SKILL.md` | The manga-page-layout skill, updated for the plugin (`dist/manga-page-layout.zip` to upload) |
| `nai_krita/settings.py` | Token, styles, options as JSON |
| `nai_krita/characters.py` | Free positions (0–1), grid labels for V4/V4.5, layer centre → position, labelled-line import, saved sets |
| `nai_krita/vibes.py` | .naiv4vibe files, vibe encoding cache |
| `nai_krita/tags.py`, `ui/tag_completer.py` | Tag autocomplete: index and search, popup |
| `nai_krita/data/tags.tsv.gz` | Danbooru tag list, built by `tools/build_tags.py` |
| `nai_krita/presets.py`, `ui/preset_menu.py` | Tag presets: merging without duplicates, the Presets menus |
| `nai_krita/metadata.py` | Settings read back from NovelAI PNGs (text chunks, stealth alpha) |
| `nai_krita/models.py` | Model ids, character limits, quality/UC preset text |
| `nai_krita/qt.py` | PyQt5/PyQt6 shim |
| `nai_krita/jobs.py` | Worker threads, queue, autosave, balance tracking |
| `nai_krita/document.py` | Preview/apply result layers |
| `nai_krita/ui/` | Docker, settings dialog, Anlas warning, history widgets |
