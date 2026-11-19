"""Tests for the Krita-free modules. Run: python tests/run_tests.py (or pytest)."""
import io
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nai_krita import api, characters, cost, models, panels, regions, request, resolution, settings, vibes  # noqa: E402
from nai_krita.request import Character, GenerationParams  # noqa: E402

V45 = "nai-diffusion-4-5-full"
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000005000172a7e8a20000000049454e44ae426082")


def opus(v5=5.0):
    return cost.Balance(tier=3, active=True, fixed=10000, purchased=0, v5_allowance=v5)


# ---------------------------------------------------------------- request
def test_v45_body_no_characters():
    p = GenerationParams(prompt="1girl", negative="lowres", seed=42, model=V45)
    b = request.build_body(p)
    assert b["model"] == "nai-diffusion-4-5-full" and b["action"] == "generate"
    assert b["input"] == "1girl"
    pr = b["parameters"]
    assert pr["params_version"] == 3 and pr["n_samples"] == 1
    assert pr["v4_prompt"]["caption"] == {"base_caption": "1girl", "char_captions": []}
    assert pr["v4_negative_prompt"]["caption"]["base_caption"] == "lowres"
    assert pr["characterPrompts"] == [] and pr["use_coords"] is False
    assert pr["skip_cfg_above_sigma"] is None
    assert "sm" not in pr


def test_v5_sends_v4_prompt_and_version_4():
    b = request.build_body(GenerationParams(prompt="x", model="nai-diffusion-5-full", seed=1))
    assert b["parameters"]["params_version"] == 4
    assert "v4_prompt" in b["parameters"]


def test_v3_body_is_flat():
    b = request.build_body(GenerationParams(prompt="x", negative="n", model="nai-diffusion-3",
                                            seed=1, characters=[Character("1girl")]))
    pr = b["parameters"]
    assert "v4_prompt" not in pr and "characterPrompts" not in pr
    assert pr["uc"] == "n" and pr["sm"] is False


def test_two_characters_with_positions():
    chars = [Character("1girl, red hair", uc="hat", pos=(0.1, 0.5)),
             Character("1boy, glasses", pos=(0.9, 0.5)),
             Character("disabled", enabled=False)]
    b = request.build_body(GenerationParams(prompt="2people", seed=7, model=V45, characters=chars))
    pr = b["parameters"]
    assert pr["use_coords"] is True and pr["v4_prompt"]["use_coords"] is True
    assert pr["characterPrompts"] == [
        {"prompt": "1girl, red hair", "uc": "hat", "center": {"x": 0.1, "y": 0.5}, "enabled": True},
        {"prompt": "1boy, glasses", "uc": "", "center": {"x": 0.9, "y": 0.5}, "enabled": True}]
    assert pr["v4_prompt"]["caption"]["char_captions"][1] == {
        "char_caption": "1boy, glasses", "centers": [{"x": 0.9, "y": 0.5}]}
    assert pr["v4_negative_prompt"]["caption"]["char_captions"][0]["char_caption"] == "hat"


def test_ai_choice_characters_do_not_use_coords():
    b = request.build_body(GenerationParams(prompt="x", seed=1, characters=[Character("1girl")]))
    assert b["parameters"]["use_coords"] is False
    assert b["parameters"]["characterPrompts"][0]["center"] == {"x": 0.5, "y": 0.5}


def test_character_limit():
    chars = [Character(f"c{i}") for i in range(7)]
    try:
        request.build_body(GenerationParams(prompt="x", seed=1, model=V45, characters=chars))
    except ValueError:
        pass
    else:
        raise AssertionError("7 characters on V4.5 should fail")
    request.build_body(GenerationParams(prompt="x", seed=1, model="nai-diffusion-5-full",
                                        characters=chars))


def test_grid():
    assert [request.grid_to_coord(i) for i in range(5)] == [0.1, 0.3, 0.5, 0.7, 0.9]
    assert request.coord_to_grid(0.0) == 0 and request.coord_to_grid(0.99) == 4
    assert request.coord_to_grid(0.5) == 2


def test_variety_and_infill():
    p = GenerationParams(prompt="x", seed=1, model=V45, variety=True, width=832, height=1216)
    assert abs(request.build_body(p)["parameters"]["skip_cfg_above_sigma"] - 58.0) < 0.01
    p = GenerationParams(prompt="x", seed=1, model=V45, action=request.INFILL, image_b64="AA", mask_b64="BB")
    b = request.build_body(p)
    assert b["model"] == "nai-diffusion-4-5-full-inpainting" and b["action"] == "infill"
    assert request.redact(b)["parameters"]["image"] == "<2 base64 chars>"


def test_with_seed_randomises_only_zero():
    assert GenerationParams(prompt="x", seed=5).with_seed().seed == 5
    assert GenerationParams(prompt="x").with_seed().seed > 0


def test_bad_size_rejected():
    try:
        request.build_body(GenerationParams(prompt="x", seed=1, width=830))
    except ValueError:
        return
    raise AssertionError("non-multiple of 64 should fail")


# ---------------------------------------------------------------- cost
FREE_CASES = [
    # (description, params kwargs, balance, expected free)
    ("opus v4.5 832x1216 28 steps", {}, opus(), True),
    ("opus v4.5 1024x1024", dict(width=1024, height=1024), opus(), True),
    ("opus 1024x1536 tall", dict(width=1024, height=1536), opus(), False),
    ("opus 29 steps", dict(steps=29), opus(), False),
    ("opus batch of 2", dict(n_samples=2), opus(), False),
    # measured live 2026-11-13: Refine and Fill cost 0 on Opus at free sizes (V4.5 and V5)
    ("opus img2img", dict(action="img2img", image_b64="A"), opus(), True),
    ("opus infill", dict(action="infill", image_b64="A", mask_b64="B"), opus(), True),
    ("opus v5 infill", dict(action="infill", image_b64="A", mask_b64="B",
                            model="nai-diffusion-5-full"), opus(), True),
    ("opus img2img too large", dict(action="img2img", image_b64="A", width=1024, height=1536),
     opus(), False),
    ("scroll img2img", dict(action="img2img", image_b64="A"),
     cost.Balance(tier=2, active=True, fixed=1000), False),
    ("scroll tier", {}, cost.Balance(tier=2, active=True, fixed=1000), False),
    ("inactive opus", {}, cost.Balance(tier=3, active=False), False),
    ("unknown balance", {}, None, False),
    ("opus v5 allowance left", dict(model="nai-diffusion-5-full"), opus(5), True),
    ("opus v5 allowance empty", dict(model="nai-diffusion-5-full"), opus(0), False),
    ("opus v5 allowance unknown", dict(model="nai-diffusion-5-full"), opus(None), False),
]


def test_free_table():
    for desc, kw, bal, expected in FREE_CASES:
        kw = {"model": V45, **kw}
        v = cost.judge(GenerationParams(prompt="x", seed=1, **kw), bal)
        assert v.free is expected, f"{desc}: got free={v.free} reasons={v.reasons}"
        assert (v.estimate == 0) is expected or not expected, desc
        if not expected:
            assert v.reasons, desc


def test_paid_reasons_in_plain_words():
    v = cost.judge(GenerationParams(prompt="x", seed=1, model=V45, width=1024, height=1536), opus())
    assert v.reasons == ["1024x1536 is above the free 1024x1024 pixel limit"]


def test_estimate_calibration_points():
    # V5 Full 1024x1536 reported at 45 Anlas on Opus.
    p = GenerationParams(prompt="x", model="nai-diffusion-5-full", width=1024, height=1536)
    assert cost.estimate_cost(p, is_opus=True) == 45
    # Two-image batch reported at 30 on Opus (one image free) - matches V5 at 1 MP.
    p = GenerationParams(prompt="x", model="nai-diffusion-5-full", width=1024, height=1024,
                         n_samples=2)
    assert cost.estimate_cost(p, is_opus=True) == 30
    # Non-Opus pays for every image.
    p = GenerationParams(prompt="x", model=V45, width=1024, height=1024)
    assert cost.estimate_cost(p, is_opus=False) == 20


def test_parse_balance_shapes():
    sub = {"tier": 3, "active": True,
           "trainingStepsLeft": {"fixedTrainingStepsLeft": 9500, "purchasedTrainingSteps": 120},
           "usage": {"limit": 100, "used": 40}}
    b = cost.parse_balance(sub)
    assert (b.tier, b.fixed, b.purchased, b.total, b.is_opus) == (3, 9500, 120, 9620, True)
    assert b.v5_allowance == 60
    b = cost.parse_balance({"subscription": sub})
    assert b.fixed == 9500
    b = cost.parse_balance({"subscription": {"tier": 1, "active": True, "trainingStepsLeft": 77}})
    assert b.fixed == 77 and not b.is_opus and b.v5_allowance is None


# ---------------------------------------------------------------- resolution
def test_nearest_size_shapes():
    for w, h in [(1000, 1000), (2591, 3624), (3624, 2591), (1306, 1250), (4000, 600),
                 (600, 4000), (1, 1)]:
        gw, gh = resolution.nearest_size(w, h)
        assert gw % 64 == 0 and gh % 64 == 0, (w, h)
        assert gw * gh <= resolution.FREE_PIXELS, (w, h, gw, gh)
        assert resolution.MIN_SIDE <= gw <= resolution.MAX_SIDE
    assert resolution.nearest_size(1000, 1000) == (1024, 1024)
    assert resolution.nearest_size(832, 1216) == (832, 1216)
    assert resolution.nearest_size(1216, 832) == (1216, 832)
    # B5 manga page (2591x3624, aspect .715) -> a tall size near that aspect
    gw, gh = resolution.nearest_size(2591, 3624)
    assert abs(gw / gh - 2591 / 3624) < 0.03 and gh > gw
    # ...and uses most of the pixel budget rather than a tiny exact match
    for w, h in [(2591, 3624), (1306, 1250), (1, 1)]:
        gw, gh = resolution.nearest_size(w, h)
        assert gw * gh >= 0.85 * 1024 * 1024 * 0.95, (w, h, gw, gh)
    # very thin strips can go below 512 now (384 accepted live) while keeping most of the
    # pixel budget, and a shape turned 90° gets the mirrored size
    assert resolution.nearest_size(4000, 600) == (2048, 448)
    assert resolution.nearest_size(600, 4000) == (448, 2048)
    for w, h in [(1000, 1000), (2591, 3624), (1306, 1250), (2067, 977), (727, 2283),
                 (4000, 600), (300, 2400), (5000, 300)]:
        assert resolution.nearest_size(h, w) == resolution.nearest_size(w, h)[::-1], (w, h)
    # ordinary panels are unaffected by the lower minimum
    assert resolution.nearest_size(2067, 977) == (1472, 704)
    assert resolution.nearest_size(727, 2283) == (576, 1792)


def test_large_cap():
    gw, gh = resolution.nearest_size(2, 3, resolution.LARGE_PIXELS)
    assert (gw, gh) == (1024, 1536)


def test_cover_rect_round_trip():
    for src, dst in [((832, 1216), (0, 0, 2591, 3624)), ((1024, 1024), (100, 50, 1306, 1250)),
                     ((1216, 832), (0, 0, 500, 2000))]:
        sw, sh, ox, oy = resolution.cover_rect(*src, *dst)
        x, y, w, h = dst
        assert sw >= w and sh >= h
        assert ox <= x and oy <= y and ox + sw >= x + w and oy + sh >= y + h
        assert abs(sw / sh - src[0] / src[1]) < 0.01


# ---------------------------------------------------------------- panels
def test_parse_generated_names():
    i = panels.parse_panel_name("P3 – 1 char, close – 1306x1250")
    assert (i.id, i.chars, i.shot, i.size) == (3, 1, "close", (1306, 1250))
    assert i.shot_tags == "close-up, portrait"
    i = panels.parse_panel_name("P12 – 0 char, establishing – 2000x900")
    assert (i.id, i.chars, i.shot) == (12, 0, "establishing")
    i = panels.parse_panel_name("P1 – 640x480")       # no chars or shot
    assert (i.id, i.chars, i.shot, i.size) == (1, None, "", (640, 480))


def test_parse_hand_edited_names():
    assert panels.parse_panel_name("P4 - 2 chars, medium").chars == 2
    assert panels.parse_panel_name("P4 - 2 chars, medium").shot == "medium"
    assert panels.parse_panel_name("P5").id == 5
    assert panels.parse_panel_name("P5 — Cowboy, rooftop").shot == "cowboy"
    for bad in ("Panel borders", "Paper", "P3 base (paste above, Inherit Alpha)", "PX – 1 char",
                "Pose 3"):
        assert panels.parse_panel_name(bad) is None, bad
    assert panels.is_base_layer_name("P3 base (paste above, Inherit Alpha)")
    assert not panels.is_base_layer_name("P3 – 1 char")


class FakeNode:
    def __init__(self, name, kind="paintlayer", parent=None, children=()):
        self._name, self._type, self._parent, self._children = name, kind, parent, list(children)
        for c in self._children:
            c._parent = self

    def name(self): return self._name
    def type(self): return self._type
    def parentNode(self): return self._parent
    def childNodes(self): return self._children


def test_find_panel_group():
    base = FakeNode("P2 base (paste above, Inherit Alpha)")
    art = FakeNode("sketch")
    inner = FakeNode("details", "grouplayer", children=[art])
    group = FakeNode("P2 – 2 char, medium – 900x700", "grouplayer", children=[base, inner])
    FakeNode("root", "grouplayer", children=[group, FakeNode("Paper")])
    g, info = panels.find_panel_group(art)
    assert g is group and info.id == 2
    assert panels.find_base_layer(group) is base
    assert panels.find_panel_group(FakeNode("loose")) == (None, None)


# ---------------------------------------------------------------- api
def test_unpack_zip_and_png():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("image_1.png", PNG_1PX)
        z.writestr("image_0.png", PNG_1PX + b"x")
    images = api.unpack_images(buf.getvalue())
    assert len(images) == 2 and images[0].endswith(b"x")   # sorted by name
    assert api.unpack_images(PNG_1PX) == [PNG_1PX]
    try:
        api.unpack_images(b'{"error": 1}')
    except api.NaiError:
        pass
    else:
        raise AssertionError


def test_error_messages():
    msg = api._error_message(402, json.dumps({"statusCode": 402, "message": "Insufficient"}).encode())
    assert "Insufficient" in msg and "Anlas" in msg
    assert "token" in api._error_message(401, b"")
    assert api._error_message(500, b"boom") == "boom"


def test_client_requires_token():
    try:
        api.Client("")
    except api.NaiError as e:
        assert "token" in str(e)
    else:
        raise AssertionError


def test_png_text_chunks():
    import struct
    import zlib
    chunk = b"Comment\0" + b'{"seed": 1}'
    t = struct.pack(">I", len(chunk)) + b"tEXt" + chunk + struct.pack(">I", zlib.crc32(b"tEXt" + chunk))
    png = PNG_1PX[:33] + t + PNG_1PX[33:]
    assert api.png_text_chunks(png) == {"Comment": '{"seed": 1}'}


# ---------------------------------------------------------------- settings
def test_settings_round_trip_and_redaction():
    with tempfile.TemporaryDirectory() as d:
        s = settings.Settings(token="pst-secret", free_only=False)
        s.styles[0].steps = 23
        s.save(d)
        t = settings.Settings.load(d)
        assert t.token == "pst-secret" and t.free_only is False and t.styles[0].steps == 23
        assert "pst-secret" not in json.dumps(t.redacted())
        # unknown keys from a newer version are ignored
        path = os.path.join(d, settings.SETTINGS_FILE)
        data = json.load(open(path, encoding="utf-8"))
        data["future_option"] = 1
        data["styles"][0]["future_field"] = 2
        json.dump(data, open(path, "w", encoding="utf-8"))
        assert settings.Settings.load(d).styles[0].steps == 23


def test_defaults_and_style_composition():
    s = settings.Settings()
    assert s.free_only is True and s.read_panel_names is False
    names = [st.name for st in s.styles]
    assert "V4.5 Full" in names and "V5 Full" in names
    assert s.default_style == "V5 Full" and names[0] == "V5 Full"
    assert models.DEFAULT_MODEL == "nai-diffusion-5-full"
    assert request.GenerationParams(prompt="x").model == "nai-diffusion-5-full"
    st = s.style("V4.5 Full")
    assert st.compose_prompt("1girl, smile") == "1girl, smile, very aesthetic, masterpiece, no text"
    assert st.compose_negative("") == st.negative_preset
    assert st.compose_negative("hat").endswith(", hat")
    assert s.style("missing").name == names[0]
    for m in models.MODELS:
        assert models.get(m.id) is m


# ---------------------------------------------------------------- live fixtures
# Bodies NovelAI accepted on 2026-11-13 (tools/verify_api.py). Rebuilding each one
# from its parameters must give the identical body, so any change to the request
# shape shows up here before it reaches the API.
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _params_from_fixture(body):
    pr = body["parameters"]
    chars = []
    for c in pr.get("characterPrompts", []):
        pos = (c["center"]["x"], c["center"]["y"]) if pr.get("use_coords") else None
        chars.append(Character(c["prompt"], uc=c["uc"], pos=pos))
    action = body.get("action", "generate")
    return GenerationParams(
        action=action, image_b64="x" if action != "generate" else "",
        mask_b64="x" if action == "infill" else "",
        strength=pr.get("strength", 0.7), noise=pr.get("noise", 0.0),
        inpaint_strength=pr.get("inpaintImg2ImgStrength", 1.0),
        vibes=[request.VibeRef("x", st) for st in pr.get("reference_strength_multiple", [])],
        char_refs=[request.CharRef("x", d["caption"]["base_caption"], st, round(1 - sec, 2))
                   for d, st, sec in zip(pr.get("director_reference_descriptions", []),
                                         pr.get("director_reference_strength_values", []),
                                         pr.get("director_reference_secondary_strength_values", []))],
        add_original_image=pr.get("add_original_image", True),
        prompt=body["input"], negative=pr["negative_prompt"],
        model=body["model"].replace("-inpainting", ""),
        width=pr["width"], height=pr["height"], steps=pr["steps"], scale=pr["scale"],
        cfg_rescale=pr["cfg_rescale"], sampler=pr["sampler"], noise_schedule=pr["noise_schedule"],
        seed=pr["seed"], n_samples=pr["n_samples"], quality_toggle=pr["qualityToggle"],
        uc_preset=pr["ucPreset"], variety=pr["skip_cfg_above_sigma"] is not None,
        transparent=bool(pr.get("straight_alpha")), characters=chars)


def test_request_bodies_match_live_fixtures():
    names = sorted(f for f in os.listdir(FIXTURES) if f.startswith("request_"))
    assert len(names) >= 5, names
    families = set()
    for name in names:
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
            saved = json.load(f)
        rebuilt = request.build_body(_params_from_fixture(saved))
        for body in (rebuilt, saved):   # image data is redacted in fixtures
            for key in ("image", "mask", "reference_image_multiple", "director_reference_images"):
                body["parameters"].pop(key, None)
        assert rebuilt == saved, name
        families.add(models.get(saved["model"].replace("-inpainting", "")).family)
    assert families == {models.V3, models.V4, models.V4_5, models.V5}


def test_live_balance_fixture_parses():
    with open(os.path.join(FIXTURES, "balance_image_novelai_net_user_subscription.json"),
              encoding="utf-8") as f:
        sub = json.load(f)["shape"]
    b = cost.parse_balance(sub)
    # the fixture is re-saved by every verify_api.py run, so compare with its own values
    steps = sub["trainingStepsLeft"]
    assert (b.tier_name, b.is_opus) == ("Opus", True)
    assert (b.fixed, b.purchased) == (steps["fixedTrainingStepsLeft"],
                                      steps["purchasedTrainingSteps"])
    assert b.v5_allowance == float(sub["usage"]["percent"])
    sub["usage"] = dict(sub["usage"], percent=0)
    assert cost.parse_balance(sub).v5_allowance == 0.0
    sub["usage"] = dict(sub["usage"], percent=12, isNegative=True)
    assert cost.parse_balance(sub).v5_allowance == 0.0


def test_live_responses_echo_prompt_unchanged():
    # The server adds no quality tags or presets: the PNG metadata prompt is what we sent.
    for name in sorted(os.listdir(FIXTURES)):
        if not name.startswith("response_"):
            continue
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
            meta = json.load(f)["png_text"]["Comment"]
        with open(os.path.join(FIXTURES, name.replace("response_", "request_")), encoding="utf-8") as f:
            sent = json.load(f)
        assert meta["prompt"] == sent["input"], name
        assert meta["uc"] == sent["parameters"]["negative_prompt"], name
        assert meta["seed"] == sent["parameters"]["seed"], name


# ---------------------------------------------------------------- phase 2: queue / batch
def test_plan_queue_four_free_on_opus():
    jobs_, v = cost.plan(GenerationParams(prompt="x", model=V45), 4, cost.QUEUE, opus())
    assert len(jobs_) == 4 and v.free and v.estimate == 0
    assert all(p.n_samples == 1 and verdict.free for p, verdict in jobs_)
    assert len({p.seed for p, _ in jobs_}) == 4          # random, distinct seeds


def test_plan_queue_fixed_seed_increments():
    jobs_, _ = cost.plan(GenerationParams(prompt="x", seed=100), 3, cost.QUEUE, opus())
    assert [p.seed for p, _ in jobs_] == [100, 101, 102]
    jobs_, _ = cost.plan(GenerationParams(prompt="x", seed=4294967295), 2, cost.QUEUE, opus())
    assert [p.seed for p, _ in jobs_] == [4294967295, 1]


def test_plan_batch_is_one_paid_request():
    jobs_, v = cost.plan(GenerationParams(prompt="x", model=V45), 2, cost.BATCH, opus())
    assert len(jobs_) == 1 and jobs_[0][0].n_samples == 2
    assert not v.free and v.estimate == 20                 # one of two images free on Opus
    assert "Batch of 2 images in one request" in v.reasons


def test_plan_queue_paid_sums_estimates():
    base = GenerationParams(prompt="x", model=V45, width=1024, height=1536)
    jobs_, v = cost.plan(base, 3, cost.QUEUE, opus())
    one = cost.judge(jobs_[0][0], opus()).estimate
    assert not v.free and v.estimate == 3 * one
    assert "3 of 3 requests cost Anlas" in v.reasons
    assert sum(r.startswith("1024x1536") for r in v.reasons) == 1   # deduplicated


def test_plan_limits():
    for count, mode in ((0, cost.QUEUE), (9, cost.QUEUE), (5, cost.BATCH)):
        try:
            cost.plan(GenerationParams(prompt="x"), count, mode, opus())
        except ValueError:
            continue
        raise AssertionError((count, mode))
    jobs_, _ = cost.plan(GenerationParams(prompt="x"), 1, cost.BATCH, opus())
    assert jobs_[0][0].n_samples == 1


# ---------------------------------------------------------------- phase 3: characters
def test_cell_labels():
    assert characters.cell_label(None) == "AI"
    assert characters.cell_label((0, 0)) == "A1" and characters.cell_label((2, 2)) == "C3"
    assert characters.cell_label((4, 4)) == "E5"
    for c in [(0, 0), (1, 3), (4, 2)]:
        assert characters.parse_cell(characters.cell_label(c)) == c
    assert characters.parse_cell("ai") is None and characters.parse_cell(" b2 ") == (1, 1)
    for bad in ("F1", "A6", "A0", "33"):
        try:
            characters.parse_cell(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_pos_from_point_relative_to_area():
    area = (100, 200, 1000, 500)
    assert characters.pos_from_point(150, 250, area) == (0.05, 0.1)
    assert characters.pos_from_point(600, 450, area) == (0.5, 0.5)
    assert characters.pos_from_point(-500, 5000, area) == (0.0, 1.0)     # clamped
    assert characters.cell_of((0.05, 0.1)) == (0, 0) and characters.cell_of((0.99, 0.5)) == (4, 2)
    assert characters.bounds_center((10, 20, 100, 50)) == (60, 45)
    assert characters.bounds_center((0, 0, 0, 0)) is None


def test_pos_labels():
    assert characters.pos_label(None, True) == "–"
    assert characters.pos_label((0.22, 0.35), True) == "22%, 35%"
    assert characters.pos_label((0.22, 0.35), False) == "B2"


def test_free_positions_on_v5_snapped_on_grid_models():
    c = [Character("girl, red", pos=(0.22, 0.35)), Character("girl, blue", pos=(0.78, 0.62))]
    v5 = request.build_body(GenerationParams(prompt="2girls", seed=1, model="nai-diffusion-5-full",
                                             characters=c))["parameters"]
    assert [x["center"] for x in v5["characterPrompts"]] == [{"x": 0.22, "y": 0.35},
                                                             {"x": 0.78, "y": 0.62}]
    assert v5["v4_prompt"]["caption"]["char_captions"][1]["centers"] == [{"x": 0.78, "y": 0.62}]
    v45 = request.build_body(GenerationParams(prompt="2girls", seed=1, model=V45,
                                              characters=c))["parameters"]
    assert [x["center"] for x in v45["characterPrompts"]] == [{"x": 0.3, "y": 0.3},
                                                              {"x": 0.7, "y": 0.7}]
    assert v45["v4_negative_prompt"]["caption"]["char_captions"][0]["centers"] == [{"x": 0.3, "y": 0.3}]
    # out-of-range input is clamped, not sent as-is
    out = request.build_body(GenerationParams(prompt="x", seed=1, model="nai-diffusion-5-full",
                                              characters=[Character("a", pos=(1.4, -0.2))]))
    assert out["parameters"]["characterPrompts"][0]["center"] == {"x": 1.0, "y": 0.0}


def test_parse_labelled_lines():
    text = ("Mika: 1girl, red hair, school uniform\n\n"
            "Ren: 1boy, glasses\n"
            "artist:foo, 1girl, smile\n"
            "1.2::blue eyes ::, 1girl\n"
            "girl, sitting")
    cs = characters.parse_labelled(text)
    assert [(c.name, c.prompt) for c in cs] == [
        ("Mika", "1girl, red hair, school uniform"), ("Ren", "1boy, glasses"),
        ("", "artist:foo, 1girl, smile"), ("", "1.2::blue eyes ::, 1girl"), ("", "girl, sitting")]
    assert all(c.pos is None for c in cs)


def test_character_dict_round_trip_drops_layer_link():
    c = Character("1girl", uc="hat", name="Mika", pos=(0.42, 0.6), layer_id="{abc}", enabled=False)
    d = characters.to_dict(c)
    assert "layer_id" not in d and d["pos"] == [0.42, 0.6]
    back = characters.from_dict(json.loads(json.dumps(d)))
    assert (back.prompt, back.uc, back.name, back.pos, back.enabled, back.layer_id) == \
        ("1girl", "hat", "Mika", (0.42, 0.6), False, "")
    # sets saved before free positions used grid cells
    assert characters.from_dict({"prompt": "x", "cell": [9, -3]}).pos == (0.9, 0.1)
    assert characters.from_dict({"prompt": "x", "pos": [2, 0.5]}).pos == (1.0, 0.5)


def test_positions_are_all_or_none():
    mixed = [Character("a", pos=(0.2, 0.5)), Character("b")]
    try:
        request.build_body(GenerationParams(prompt="x", seed=1, characters=mixed))
    except ValueError as e:
        assert "every character" in str(e)
    else:
        raise AssertionError("a mix of placed and unplaced characters must be refused")
    # a disabled unplaced character does not count
    ok = [Character("a", pos=(0.2, 0.5)), Character("b", enabled=False)]
    assert request.build_body(GenerationParams(prompt="x", seed=1, characters=ok))[
        "parameters"]["use_coords"] is True


def test_default_positions_spread():
    assert [characters.default_position(i, 3) for i in range(3)] == [(0.25, 0.5), (0.5, 0.5), (0.75, 0.5)]
    items = characters.fill_positions([Character("a", pos=(0.9, 0.9)), Character("b")])
    assert items[0].pos == (0.9, 0.9) and items[1].pos == (0.667, 0.5)


# ---------------------------------------------------------------- phase 4: panels
def test_default_panel_prompt_from_name():
    info = panels.parse_panel_name("P1 – 0 char, wide – 2067x977")
    assert panels.default_panel_prompt(info) == "no humans, wide shot"
    info = panels.parse_panel_name("P2 – 2 char, medium – 1416x1987")
    assert panels.default_panel_prompt(info) == "upper body"
    assert panels.default_panel_prompt(panels.parse_panel_name("P9")) == ""


def test_join_prompts():
    assert panels.join_prompts("1girl, smile, ", "", " upper body") == "1girl, smile, upper body"
    assert panels.join_prompts("", "  ") == ""


def test_result_layer_names():
    assert panels.is_result_layer_name("NovelAI 123456")
    for name in ("NovelAI 12 (mine)", "[NovelAI preview]", "P3 base (paste above, Inherit Alpha)",
                 "NovelAI sketch"):
        assert not panels.is_result_layer_name(name), name


def test_target():
    t = panels.Target(rect=(0, 0, 10, 10))
    assert not t.is_panel
    assert panels.Target(rect=(1, 2, 3, 4), group_id="{g}", label="P3").is_panel


def test_character_state_keeps_links_only_when_asked():
    c = Character("1girl", pos=(0.2, 0.3), layer_id="{abc}")
    assert "layer_id" not in characters.to_dict(c)
    d = characters.to_dict(c, keep_link=True)
    assert d["layer_id"] == "{abc}" and characters.from_dict(d).layer_id == "{abc}"


# ---------------------------------------------------------------- phase 5: regions
def test_intersect_and_contains():
    assert regions.intersect((0, 0, 10, 10), (5, 5, 10, 10)) == (5, 5, 5, 5)
    assert regions.intersect((0, 0, 10, 10), (10, 0, 5, 5)) is None
    assert regions.contains((0, 0, 100, 100), (10, 10, 20, 20))
    assert not regions.contains((0, 0, 100, 100), (90, 90, 20, 20))


def test_pad_adds_context_inside_bounds():
    canvas = (0, 0, 2000, 3000)
    assert regions.pad((1000, 1000, 200, 100), canvas) == (900, 900, 400, 300)   # 50% of 200
    assert regions.pad((10, 10, 40, 40), canvas) == (0, 0, 114, 114)           # min 64, clamped
    panel = (500, 500, 600, 600)
    r = regions.pad((520, 520, 100, 100), panel)
    assert regions.contains(panel, r) and r[:2] == (500, 500)


def test_grow_to_aspect():
    bounds = (0, 0, 2000, 2000)
    r = regions.grow_to_aspect((900, 900, 200, 400), 832 / 1216, bounds)
    assert r[3] == 400 and abs(r[2] / r[3] - 832 / 1216) < 0.01 and r[0] < 900
    r = regions.grow_to_aspect((900, 900, 400, 200), 1, bounds)
    assert r == (900, 800, 400, 400)
    r = regions.grow_to_aspect((0, 0, 100, 1900), 1, (0, 0, 500, 2000))     # can't reach aspect
    assert r == (0, 0, 500, 1900)
    r = regions.grow_to_aspect((1950, 0, 50, 100), 1, bounds)                # pushed inside
    assert r == (1900, 0, 100, 100) and regions.contains(bounds, r)


def test_partial_inpaint_body():
    base = dict(prompt="x", seed=1, model="nai-diffusion-5-full", action=request.INFILL,
                image_b64="A", mask_b64="B")
    full = request.build_body(GenerationParams(**base))["parameters"]
    assert "img2img" not in full and "inpaintImg2ImgStrength" not in full
    part = request.build_body(GenerationParams(inpaint_strength=0.4, **base))
    pr = part["parameters"]
    assert part["model"] == "nai-diffusion-5-full-inpainting" and part["action"] == "infill"
    assert pr["inpaintImg2ImgStrength"] == 0.4
    assert pr["img2img"] == {"strength": 0.4, "color_correct": True}
    assert "strength" not in pr   # the ignored top-level shape is gone


def test_inpainting_model_ids():
    def model_for(m):
        return request.build_body(GenerationParams(prompt="x", seed=1, model=m, action=request.INFILL,
                                                   image_b64="A", mask_b64="B"))["model"]
    assert model_for("nai-diffusion-5-full") == "nai-diffusion-5-full-inpainting"
    assert model_for("nai-diffusion-4-5-full") == "nai-diffusion-4-5-full-inpainting"
    assert model_for("nai-diffusion-5-curated") == "nai-diffusion-4-5-curated-inpainting"


# ---------------------------------------------------------------- phase 6: references
def _write_vibe(d, name, data):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def test_vibe_files_both_shapes():
    with tempfile.TemporaryDirectory() as d:
        enc_only = _write_vibe(d, "a.naiv4vibe", {
            "identifier": "novelai-vibe-transfer", "version": 1, "type": "encoding",
            "name": "Candy", "encodings": {"v4-5full": {"unknown": {"encoding": "ENC45"}}},
            "importInfo": {"model": "nai-diffusion-4-5-full", "information_extracted": 0.6,
                           "strength": 0.7}})
        with_image = _write_vibe(d, "b.naiv4vibe", {
            "identifier": "novelai-vibe-transfer", "version": 1, "type": "image",
            "name": "Girl", "image": "IMG",
            "encodings": {"v4-5full": {"h1": {"encoding": "E07", "params": {"information_extracted": 0.7}}},
                          "v4curated": {"h2": {"encoding": "EC", "params": {"information_extracted": 1}}}},
            "importInfo": {"model": "nai-diffusion-4-5-full", "information_extracted": 0.7,
                           "strength": 0.5}})
        bundle = _write_vibe(d, "c.naiv4vibebundle", {"vibes": [json.load(open(enc_only)),
                                                                 json.load(open(with_image))]})
        (a,) = vibes.load_vibe_file(enc_only)
        assert (a.name, a.strength, a.information_extracted) == ("Candy", 0.7, 0.6)
        assert a.encoding_for("nai-diffusion-4-5-full", 1.0) == "ENC45"   # only what it has
        assert a.encoding_for("nai-diffusion-4-full", 1.0) is None
        (b,) = vibes.load_vibe_file(with_image)
        assert b.models == ["nai-diffusion-4-5-full", "nai-diffusion-4-curated-preview"]
        assert b.encoding_for("nai-diffusion-4-5-full", 0.7) == "E07"
        assert b.encoding_for("nai-diffusion-4-5-full", 0.3) is None      # has image: re-encode
        assert len(vibes.load_vibe_file(bundle)) == 2
        assert vibes.is_vibe_file(bundle) and not vibes.is_vibe_file("x.png")


def test_vibe_cache():
    with tempfile.TemporaryDirectory() as d:
        c = vibes.VibeCache(os.path.join(d, "vibes"))
        k = vibes.cache_key(b"png", "nai-diffusion-4-5-full", 0.7)
        assert k != vibes.cache_key(b"png", "nai-diffusion-4-5-full", 0.6)
        assert c.get(k) is None and not c.has(k)
        c.put(k, "ENCODED")
        assert c.get(k) == "ENCODED" and c.has(k)


def test_reference_bodies_and_model_gating():
    v = [request.VibeRef("E1", 0.6), request.VibeRef("E2", 0.45)]
    cr = [request.CharRef("IMG", "character", strength=0.9, fidelity=0.75)]
    pr = request.build_body(GenerationParams(prompt="x", seed=1, model=V45, vibes=v))["parameters"]
    assert pr["reference_image_multiple"] == ["E1", "E2"]
    assert pr["reference_strength_multiple"] == [0.6, 0.45]
    assert "director_reference_images" not in pr
    pr = request.build_body(GenerationParams(prompt="x", seed=1, model=V45,
                                             char_refs=cr))["parameters"]
    assert "reference_image_multiple" not in pr
    assert pr["director_reference_images"] == ["IMG"]
    assert pr["director_reference_descriptions"] == [
        {"caption": {"base_caption": "character", "char_captions": []}, "legacy_uc": False}]
    assert pr["director_reference_strength_values"] == [0.9]
    assert pr["director_reference_secondary_strength_values"] == [0.25]
    assert pr["director_reference_information_extracted"] == [1.0]
    for model, kw, msg in (("nai-diffusion-5-full", dict(vibes=v), "V4 and V4.5"),
                           ("nai-diffusion-4-full", dict(char_refs=cr), "V4.5 only"),
                           ("nai-diffusion-5-full", dict(char_refs=cr), "V4.5 only"),
                           (V45, dict(vibes=v, char_refs=cr), "can't be used together")):
        try:
            request.build_body(GenerationParams(prompt="x", seed=1, model=model, **kw))
        except ValueError as e:
            assert msg in str(e), e
        else:
            raise AssertionError(model)
    # with_seed keeps references as dataclasses
    p = GenerationParams(prompt="x", model=V45, vibes=v, char_refs=cr).with_seed()
    assert isinstance(p.vibes[0], request.VibeRef) and isinstance(p.char_refs[0], request.CharRef)


def test_reference_costs():
    v = [request.VibeRef("E")]
    assert cost.judge(GenerationParams(prompt="x", seed=1, model=V45, vibes=v), opus()).free
    enc = cost.judge(GenerationParams(prompt="x", seed=1, model=V45, vibes=v, vibe_encodes=1), opus())
    assert not enc.free and enc.estimate == 2
    five = [request.VibeRef("E")] * 5
    assert cost.judge(GenerationParams(prompt="x", seed=1, model=V45, vibes=five), opus()).estimate == 2
    cr = cost.judge(GenerationParams(prompt="x", seed=1, model=V45,
                                     char_refs=[request.CharRef("I")]), opus())
    assert not cr.free and cr.estimate == 5 and "Opus too" in cr.reasons[0]
    # queue: only the first request encodes
    jobs_, total = cost.plan(GenerationParams(prompt="x", model=V45, vibes=v, vibe_encodes=1),
                             3, cost.QUEUE, opus())
    assert [p.vibe_encodes for p, _ in jobs_] == [1, 0, 0] and total.estimate == 2


def test_costs_match_live_measurements():
    """Paid requests measured on Opus (tests/fixtures/cost_v5_check.json): the base
    formula on V4.5 and V5's 1.5x on the rounded base cost."""
    with open(os.path.join(FIXTURES, "cost_v5_check.json"), encoding="utf-8") as f:
        rows = json.load(f)
    assert len(rows) == 4
    for r in rows:
        p = GenerationParams(prompt="x", model=r["model"], width=r["width"],
                             height=r["height"], steps=r["steps"], seed=1)
        assert cost.estimate_cost(p, is_opus=True) == r["spent"], r
        assert r["v5_allowance_used"] in (0, 0.0, None)   # paid V5 uses Anlas, not allowance


def test_transparent_background_body():
    p = GenerationParams(prompt="1girl, transparent background", model="nai-diffusion-5-full",
                         seed=1, transparent=True)
    pr = request.build_body(p)["parameters"]
    assert pr["straight_alpha"] is True and pr["tag_hint_transparent_background"] is True
    off = request.build_body(GenerationParams(prompt="x", model="nai-diffusion-5-full", seed=1))
    assert "straight_alpha" not in off["parameters"]
    for kw, msg in ((dict(model="nai-diffusion-4-5-full"), "V5 only"),
                    (dict(model="nai-diffusion-5-full", action=request.IMG2IMG,
                          image_b64="x"), "new images")):
        try:
            request.build_body(GenerationParams(prompt="x", seed=1, transparent=True, **kw))
        except ValueError as e:
            assert msg in str(e), e
        else:
            raise AssertionError(kw)
    # transparency doesn't change the cost estimate (free at normal sizes on Opus)
    assert cost.estimate_cost(p, is_opus=True) == 0
