"""Tag presets and NovelAI's defaults (Krita-free)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nai_krita import models, presets, settings  # noqa: E402


def test_merge_adds_only_missing_tags():
    q = "very aesthetic, masterpiece, no text"
    assert presets.merge("1girl, smile", q) == "1girl, smile, very aesthetic, masterpiece, no text"
    # already there (even weighted or with underscores) → skipped
    assert presets.merge("1girl, {{masterpiece}}, very_aesthetic", q) == \
        "1girl, {{masterpiece}}, very_aesthetic, no text"
    assert presets.merge("1girl, " + q, q) == "1girl, " + q          # nothing to add: unchanged
    assert presets.merge("", q) == q
    assert presets.merge("1girl, smile, ", "blush") == "1girl, smile, blush"
    # undesired-content presets go first, like NovelAI's site
    assert presets.merge("bad hands", "lowres, bad hands, worst quality", at_start=True) == \
        "lowres, worst quality, bad hands"
    # weights in the preset are kept as written
    assert presets.merge("1girl", "-0.8::feet::, rating:general") == "1girl, -0.8::feet::, rating:general"
    # text is kept as typed, newlines included
    assert presets.merge("A girl in the rain.\n1girl", "1girl, rain") == "A girl in the rain.\n1girl, rain"


def test_store():
    assert presets.normalize_store(None) == {"positive": {}, "negative": {}}
    st = presets.normalize_store({"positive": {"Manga": "monochrome, greyscale", " ": "x"},
                                  "negative": "junk", "other": {}})
    assert st == {"positive": {"Manga": "monochrome, greyscale"}, "negative": {}}
    assert presets.sorted_names({"positive": {"b": "", "A": "", "c": ""}}, "positive") == ["A", "b", "c"]


def test_settings_roundtrip(tmp=None):
    import tempfile
    d = tempfile.mkdtemp()
    s = settings.Settings()
    s.tag_presets = {"positive": {"Manga": "monochrome, greyscale, screentone"},
                     "negative": {"No colour": "colorful"}}
    s.preset_replace = True
    s.save(d)
    t = settings.Settings.load(d)
    assert t.tag_presets == s.tag_presets and t.preset_replace


def test_styles_dont_double_tags():
    st = next(x for x in settings.default_styles() if x.model == "nai-diffusion-4-5-full")
    # the style adds its quality tags only where missing
    assert st.compose_prompt("1girl, masterpiece") == "1girl, masterpiece, very aesthetic, no text"
    assert st.compose_prompt("1girl") == "1girl, very aesthetic, masterpiece, no text"
    # and its UC preset (Light) in front, minus what's already there
    neg = st.compose_negative("lowres, bad hands")
    assert neg.startswith("artistic error, ") and neg.endswith(", lowres, bad hands")
    assert neg.count("lowres") == 1
    plain = settings.Style(name="x")
    assert plain.compose_prompt(" 1girl, ") == "1girl" and plain.compose_negative("") == ""


def test_nai_tables_cover_every_model():
    assert set(models.NAI_QUALITY) == set(models.BY_ID) == set(models.NAI_UC)
    for m in models.BY_ID:
        assert presets.nai_quality(m)
        assert "Heavy" in presets.nai_uc_presets(m) and "Light" in presets.nai_uc_presets(m)
    assert presets.nai_quality("nai-diffusion-5-full") == "very aesthetic, masterpiece, no text"
    assert "rating:general" in presets.nai_quality("nai-diffusion-4-5-curated")
    v5 = presets.nai_uc_presets("nai-diffusion-5-full")
    assert set(v5) == {"Heavy", "Light", "Human Focus", "Furry Focus"}
    assert v5["Light"].endswith("0::ai-generated::")
    # the built-in styles still match what they used before (V4.5 Light)
    assert models.UC_PRESETS[models.V4_5]["Light"].startswith("lowres, artistic error, scan artifacts")
    assert presets.nai_quality("unknown-model") == "" and presets.nai_uc_presets("unknown") == {}
