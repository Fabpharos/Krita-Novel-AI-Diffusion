"""Phase 0: check the NovelAI API against the plugin's assumptions, outside Krita.

    python tools/verify_api.py                  # balance endpoints only (no generation)
    python tools/verify_api.py --generate       # + one free-size image per model family
    python tools/verify_api.py --generate --characters   # + a two-character V4.5 image

Token: NAI_TOKEN environment variable, else the plugin's settings file in
%APPDATA%\\krita, else a hidden prompt. The token is never printed or written
to fixtures. (In PowerShell set the variable with $env:NAI_TOKEN = "pst-...";
`set NAI_TOKEN=...` only works in cmd.exe.)

Every generation uses free-eligible settings (832x1216, 28 steps, 1 image) and
is refused unless the free test passes, unless you add --allow-paid. V5 requests
use the Opus V5 allowance. Balance is printed before and after each request.
Fixtures (request bodies with images stripped, response PNG metadata, balance
shapes) go to tests/fixtures/; images go to tools/out/.
"""
import argparse
import dataclasses
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nai_krita import api, cost, models, request, resolution, settings, vibes  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
OUT = os.path.join(ROOT, "tools", "out")
FAMILY_MODELS = {models.V3: "nai-diffusion-3", models.V4: "nai-diffusion-4-full",
                 models.V4_5: "nai-diffusion-4-5-full", models.V5: "nai-diffusion-5-full"}
SEED = 123456789


def load_token():
    if os.environ.get("NAI_TOKEN"):
        return os.environ["NAI_TOKEN"].strip()
    folder = os.path.join(os.environ.get("APPDATA", ""), "krita")
    token = settings.Settings.load(folder).token
    if not token and sys.stdin.isatty():
        import getpass
        token = getpass.getpass("NovelAI persistent API token (input hidden): ").strip()
    return token


def shape(value, depth=0):
    """Structure of a JSON value with numbers/bools kept and strings blanked."""
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()} if depth < 4 else "{...}"
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    if isinstance(value, str):
        return "<str>"
    return value


def anonymise_subscription(sub):
    """A copy of a subscription response that keeps its shape and tier but not the
    account's expiry date or Anlas balances (sample values instead)."""
    sub = json.loads(json.dumps(sub or {}))
    if "expiresAt" in sub:
        sub["expiresAt"] = 0
    steps = sub.get("trainingStepsLeft")
    if isinstance(steps, dict):
        steps.update({k: v for k, v in (("fixedTrainingStepsLeft", 1000),
                                        ("purchasedTrainingSteps", 100)) if k in steps})
    return sub


def save(name, data):
    os.makedirs(FIXTURES, exist_ok=True)
    with open(os.path.join(FIXTURES, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  saved tests/fixtures/{name}")


def raw_get(client, url):
    try:
        status, body = client._request("GET", url)
        return status, json.loads(body.decode("utf-8"))
    except api.NaiError as e:
        return e.status, e.message


def check_balance_endpoints(client):
    print("== Balance endpoints")
    found = {}
    for host in (api.IMAGE_HOST, api.API_HOST):
        for path in ("/user/subscription", "/user/data"):
            status, data = raw_get(client, host + path)
            print(f"  GET {host}{path}: {status if isinstance(data, str) else 'OK'}"
                  + (f" ({data[:120]})" if isinstance(data, str) else ""))
            if not isinstance(data, str):
                found[host + path] = data
    for url, data in found.items():
        key = url.split("//")[1].replace("/", "_").replace(".", "_")
        # Only structure, never values from /user/data (it holds account data); the
        # subscription's expiry date and balances are replaced with sample numbers so
        # the fixtures can be published.
        if url.endswith("/user/subscription"):
            sub = anonymise_subscription(data)
            save(f"balance_{key}.json", {"shape": shape(sub),
                                          "parsed": cost.parse_balance(sub).__dict__})
        else:
            save(f"balance_{key}.json",
                 {"shape": shape(anonymise_subscription(data.get("subscription", {}))),
                  "top_level_keys": sorted(data)})
    b = cost.parse_balance(client.subscription())
    print(f"  Parsed: tier={b.tier_name} active={b.active} fixed={b.fixed} "
          f"purchased={b.purchased} v5_allowance={b.v5_allowance} raw_usage={b.v5_allowance_raw}")
    return b


def generate_one(client, params, tag, allow_paid, mutate=None):
    """Send one request and report the balance change. `mutate(body)` may edit the
    body before it is sent (to try what the plugin itself refuses or doesn't send)."""
    before = cost.parse_balance(client.subscription())
    verdict = cost.judge(params, before)
    print(f"== {tag}: {params.info.label} {params.width}x{params.height} "
          f"{'FREE' if verdict.free else 'PAID ~' + str(verdict.estimate)}")
    if not verdict.free:
        print("   reasons:", "; ".join(verdict.reasons))
        if not allow_paid:
            print("   skipped (add --allow-paid to send it)")
            return None
    body = request.build_body(params)
    if mutate:
        mutate(body)
    save(f"request_{tag}.json" if not mutate else f"probe_{tag}.json", request.redact(body))
    t0 = time.time()
    try:
        images = client.generate(body)
    except api.NaiError as e:
        print(f"   FAILED {e.status}: {e.message}")
        save(f"error_{tag}.json", {"status": e.status, "message": e.message})
        return None
    after = cost.parse_balance(client.subscription())
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{tag}_{params.seed}.png")
    with open(path, "wb") as f:
        f.write(images[0])
    meta = api.png_text_chunks(images[0])
    for k in ("Comment",):
        if k in meta:
            try:
                meta[k] = json.loads(meta[k])
            except ValueError:
                pass
    save(f"response_{tag}.json" if not mutate else f"probe_response_{tag}.json",
         {"image_count": len(images), "png_text": meta})
    sent_chars = body["parameters"].get("characterPrompts") or []
    if sent_chars:
        echoed = (meta.get("Comment") or {}).get("v4_prompt", {}).get("caption", {}) \
            .get("char_captions", []) if isinstance(meta.get("Comment"), dict) else []
        print("   positions sent:  ", [c["center"] for c in sent_chars])
        print("   positions echoed:", [c.get("centers") for c in echoed])
    spent = before.total - after.total
    print(f"   {len(images)} image(s) in {time.time() - t0:.1f}s -> {path}")
    print(f"   balance {before.total} -> {after.total} (spent {spent}); "
          f"V5 allowance {before.v5_allowance} -> {after.v5_allowance}")
    if verdict.free and spent > 0:
        print("   !! judged free but cost Anlas: the free test is wrong")
    used = None
    if before.v5_allowance is not None and after.v5_allowance is not None:
        used = round(before.v5_allowance - after.v5_allowance, 2)
    return spent, used


def png_rgba(w, h, pixel):
    """Minimal PNG writer (stdlib only): pixel(x, y) -> 4 bytes RGBA."""
    import struct
    import zlib
    rows = b"".join(b"\x00" + b"".join(pixel(x, y) for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def source_image():
    """An earlier V5 result (832x1216) from --generate, as the img2img/inpaint source."""
    import base64
    path = os.path.join(OUT, f"v5_{SEED}.png")
    if not os.path.exists(path):
        sys.exit(f"Run --generate first: {path} is the source image for --refine/--inpaint.")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def paid_checks(client, a):
    """Refine (img2img) and Fill (inpaint) on V4.5 and V5. These cost Anlas, so
    they are skipped unless --allow-paid; the balance change calibrates the estimate."""
    import base64
    image = source_image()
    W, H = 832, 1216
    white, clear = b"\xff\xff\xff\xff", b"\x00\x00\x00\x00"
    # inpaint the middle third (aligned to 8 px cells): replace the face/upper body
    mask = base64.b64encode(png_rgba(W, H, lambda x, y: white if 280 <= x < 552 and 160 <= y < 560
                                     else clear)).decode()
    for fam in ("v4.5", "v5"):
        model = FAMILY_MODELS[fam]
        tags = models.QUALITY_TAGS[models.get(model).family]
        if a.refine:
            p = request.GenerationParams(
                prompt="1girl, black hair, school uniform, classroom, smile, watercolor, " + tags,
                negative="lowres", model=model, width=W, height=H, seed=SEED,
                action=request.IMG2IMG, image_b64=image, strength=0.5)
            generate_one(client, p, fam.replace(".", "_") + "_refine_50", a.allow_paid)
            time.sleep(2)
        if a.inpaint:
            p = request.GenerationParams(
                prompt="1girl, black hair, school uniform, classroom, surprised, open mouth, " + tags,
                negative="lowres", model=model, width=W, height=H, seed=SEED,
                action=request.INFILL, image_b64=image, mask_b64=mask)
            generate_one(client, p, fam.replace(".", "_") + "_inpaint", a.allow_paid)
            time.sleep(2)


def inpaint_strength_check(client, a):
    """Free on Opus: the same V5 face inpaint at strength 1.0 and 0.4. If NovelAI
    honours inpaintImg2ImgStrength, the 0.4 face stays closer to the original and
    the echo shows the strength; otherwise both look fully repainted."""
    import base64
    image = source_image()
    W, H = 832, 1216
    white, clear = b"\xff\xff\xff\xff", b"\x00\x00\x00\x00"
    mask = base64.b64encode(png_rgba(W, H, lambda x, y: white if 280 <= x < 552 and 160 <= y < 560
                                     else clear)).decode()
    for s_ in (1.0, 0.4):
        p = request.GenerationParams(
            prompt="1girl, black hair, school uniform, classroom, angry, frown, "
                   + models.QUALITY_TAGS[models.V5],
            negative="lowres", model="nai-diffusion-5-full", width=W, height=H, seed=SEED,
            action=request.INFILL, image_b64=image, mask_b64=mask, inpaint_strength=s_)
        tag = f"v5_inpaint_strength_{int(s_ * 100)}"
        generate_one(client, p, tag, a.allow_paid)
        try:
            meta = json.load(open(os.path.join(FIXTURES, f"response_{tag}.json"),
                                  encoding="utf-8"))["png_text"]["Comment"]
            echo = {k: v for k, v in meta.items()
                    if any(t in k.lower() for t in ("strength", "noise", "request_type", "inpaint"))
                    and "reference" not in k}
            print("   echoed:", echo)
        except Exception as e:
            print("   (no metadata echo:", e, ")")
        time.sleep(2)


def _balance(client):
    return cost.parse_balance(client.subscription())


def reference_checks(client, a):
    """Phase 6: Vibe Transfer, Character Reference and upscale, each with balance
    before/after. Only --vibe-file is expected to be free."""
    import base64
    V45 = "nai-diffusion-4-5-full"
    tags = models.QUALITY_TAGS[models.V4_5]
    prompt = "1girl, black hair, school uniform, classroom, smile, " + tags
    if a.vibe_file:
        entries = vibes.load_vibe_file(a.vibe_file)
        e = entries[0]
        enc = e.encoding_for(V45, e.information_extracted)
        if not enc:
            print(f"== vibe file: {e.name} has no V4.5 Full encoding (has: {e.models})")
        else:
            p = request.GenerationParams(prompt=prompt, negative="lowres", model=V45, seed=SEED,
                                         vibes=[request.VibeRef(enc, e.strength, e.name)])
            generate_one(client, p, "v4_5_vibe_file", a.allow_paid)
            time.sleep(2)
    if not (a.vibe or a.charref or a.upscale):
        return
    if not a.allow_paid:
        print("== --vibe/--charref/--upscale cost Anlas: add --allow-paid to run them")
        return
    src_png = base64.b64decode(source_image())
    if a.vibe:
        before = _balance(client)
        enc = client.encode_vibe(src_png, 1.0, V45)
        after = _balance(client)
        print(f"== encode-vibe ({len(enc)} base64 chars): balance {before.total} -> {after.total} "
              f"(spent {before.total - after.total}; estimate {vibes.ENCODE_COST})")
        save("encode_vibe.json", {"encoding_chars": len(enc),
                                  "spent": before.total - after.total})
        p = request.GenerationParams(prompt="1girl, red hair, park, " + tags, negative="lowres",
                                     model=V45, seed=SEED, vibes=[request.VibeRef(enc, 0.6)])
        generate_one(client, p, "v4_5_vibe_encoded", a.allow_paid)
        time.sleep(2)
    if a.charref:
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(src_png)).convert("RGB")
            canvas = Image.new("RGB", (1024, 1536), (0, 0, 0))
            im.thumbnail((1024, 1536))
            canvas.paste(im, ((1024 - im.width) // 2, (1536 - im.height) // 2))
            buf = io.BytesIO(); canvas.save(buf, "PNG"); ref = buf.getvalue()
        except ImportError:
            ref = src_png
        p = request.GenerationParams(
            prompt="1girl, black hair, park, sitting on bench, " + tags, negative="lowres",
            model=V45, seed=SEED,
            char_refs=[request.CharRef(base64.b64encode(ref).decode(), "character&style")])
        generate_one(client, p, "v4_5_charref", a.allow_paid)
        time.sleep(2)
    if a.upscale:
        before = _balance(client)
        out = client.upscale(src_png)
        after = _balance(client)
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, f"v5_{SEED}_upscaled.png")
        with open(path, "wb") as f:
            f.write(out[0])
        size = (int.from_bytes(out[0][16:20], "big"), int.from_bytes(out[0][20:24], "big"))
        print(f"== upscale: {size[0]}x{size[1]} -> {path}; balance {before.total} -> "
              f"{after.total} (spent {before.total - after.total})")
        save("upscale.json", {"size": size, "spent": before.total - after.total})


V5_COST_CASES = [   # (width, height, steps): 29 steps is the cheapest way to be paid
    (512, 512, 29),
    (832, 1216, 29),
]


def v5_cost_check(client, a):
    """Same paid requests on V4.5 Full (control for the base formula) and V5 Full,
    to settle cost.V5_MULTIPLIER. One image each; about 64 Anlas in total."""
    if not a.allow_paid:
        print("== --v5-cost sends paid requests (about 64 Anlas): add --allow-paid")
        return
    rows = []
    for w, h, steps in V5_COST_CASES:
        for model in ("nai-diffusion-4-5-full", "nai-diffusion-5-full"):
            fam = models.get(model).family
            p = request.GenerationParams(
                prompt="1girl, black hair, school uniform, classroom, smile, "
                       + models.NAI_QUALITY[model], negative="lowres", model=model,
                width=w, height=h, steps=steps, seed=SEED)
            est = cost.estimate_cost(p, is_opus=True)
            base = cost.estimate_cost(dataclasses.replace(p, model="nai-diffusion-4-5-full"),
                                      is_opus=True)
            tag = f"cost_{fam.replace('.', '_')}_{w}x{h}_{steps}"
            result = generate_one(client, p, tag, a.allow_paid)
            spent, used = result if result else (None, None)
            rows.append({"model": model, "width": w, "height": h, "steps": steps,
                         "estimate": est, "base_formula": base, "spent": spent,
                         "v5_allowance_used": used})
            time.sleep(2)
    print("\n== V5 cost check")
    print(f"   {'model':24} {'size':>10} {'steps':>5} {'base':>5} {'estimate':>8} {'spent':>6} "
          f"{'allowance used':>15}")
    for r in rows:
        print(f"   {r['model']:24} {r['width']}x{r['height']:<5} {r['steps']:>5} "
              f"{r['base_formula']:>5} {r['estimate']:>8} {str(r['spent']):>6} "
              f"{str(r['v5_allowance_used']):>15}")
    for w, h, steps in V5_COST_CASES:
        pair = [r for r in rows if (r["width"], r["height"], r["steps"]) == (w, h, steps)]
        if len(pair) == 2 and all(r["spent"] for r in pair):
            print(f"   {w}x{h}: V5/V4.5 spent ratio {pair[1]['spent'] / pair[0]['spent']:.2f} "
                  f"(plugin assumes {cost.V5_MULTIPLIER})")
    save("cost_v5_check.json", rows)


def transparent_check(client, a):
    """V5 Full with straight_alpha + tag_hint_transparent_background. Expected free
    (normal size, 28 steps, one image); reports whether the PNG really has alpha."""
    p = request.GenerationParams(
        prompt="1girl, standing, full body, transparent background, "
               + models.NAI_QUALITY["nai-diffusion-5-full"],
        negative="lowres", model="nai-diffusion-5-full", seed=SEED, transparent=True)
    result = generate_one(client, p, "v5_transparent", a.allow_paid)
    if result is None:
        return
    path = os.path.join(OUT, f"v5_transparent_{SEED}.png")
    with open(path, "rb") as f:
        png = f.read()
    colour_type = png[25] if len(png) > 25 else None
    kinds = {2: "RGB (no alpha)", 6: "RGBA", 4: "grey + alpha", 0: "grey", 3: "palette"}
    print(f"   PNG colour type: {kinds.get(colour_type, colour_type)}")
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(png))
        if "A" in im.getbands():
            alpha = im.getchannel("A")
            hist = alpha.histogram()
            total = im.width * im.height
            clear, solid = hist[0], hist[255]
            print(f"   alpha: {100 * clear / total:.1f}% fully transparent, "
                  f"{100 * solid / total:.1f}% opaque, "
                  f"{100 * (total - clear - solid) / total:.1f}% partly")
        else:
            print("   no alpha channel in the image")
    except ImportError:
        print("   (install Pillow to measure how much of the image is transparent)")


CHAR_PROMPTS = ["girl, red hair", "girl, blue hair", "girl, green hair", "girl, black hair",
                "girl, white hair", "girl, pink hair", "girl, purple hair", "girl, orange hair",
                "girl, grey hair", "girl, brown hair", "girl, blonde hair", "girl, aqua hair"]


def _add_characters(n_extra):
    """mutate(): append characters beyond what the plugin allows."""
    def go(body):
        pr = body["parameters"]
        have = len(pr["characterPrompts"])
        for i in range(n_extra):
            text = CHAR_PROMPTS[(have + i) % len(CHAR_PROMPTS)]
            centre = {"x": 0.5, "y": 0.5}
            pr["characterPrompts"].append({"prompt": text, "uc": "", "center": centre,
                                           "enabled": True})
            pr["v4_prompt"]["caption"]["char_captions"].append(
                {"char_caption": text, "centers": [centre]})
            pr["v4_negative_prompt"]["caption"]["char_captions"].append(
                {"char_caption": "", "centers": [centre]})
    return go


def _image(tag):
    path = os.path.join(OUT, f"{tag}_{SEED}.png")
    return path if os.path.exists(path) else None


def _mean_diff(a, b):
    """Mean absolute pixel difference (0-255) between two saved images, or None."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return 0.0 if fa.read() == fb.read() else None
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return None
    return round(sum(ImageStat.Stat(ImageChops.difference(ia, ib)).mean) / 3, 2)


def _echo(tag):
    path = os.path.join(FIXTURES, f"probe_response_{tag}.json")
    if not os.path.exists(path):
        path = os.path.join(FIXTURES, f"response_{tag}.json")
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)["png_text"].get("Comment") or {}
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError, KeyError):
        return {}


def limits_check(client, a):
    """Free checks of assumptions the plugin still makes.
    Every request is one image at <= 1 MP and 28 steps, so free on Opus."""
    results = []

    def run(tag, params, mutate=None):
        r = generate_one(client, params, tag, a.allow_paid, mutate)
        ok = r is not None
        results.append((tag, "accepted" if ok else "REFUSED", r[0] if ok else None))
        time.sleep(2)
        return ok

    # 1. character limits: the plugin's maximum, then one more
    for model, n in (("nai-diffusion-4-5-full", 6), ("nai-diffusion-5-full", 22)):
        fam = models.get(model).family.replace(".", "_")
        chars = [request.Character(CHAR_PROMPTS[i % len(CHAR_PROMPTS)]) for i in range(n)]
        base = request.GenerationParams(prompt="6+girls, crowd, " + models.NAI_QUALITY[model],
                                        negative="lowres", model=model, seed=SEED,
                                        characters=chars)
        run(f"limit_{fam}_{n}_chars", base)
        run(f"limit_{fam}_{n + 1}_chars", base, _add_characters(1))

    # 2. side lengths: 2048 (the plugin's MAX_SIDE, over the SDK's 1600), and below MIN_SIDE
    for w, h in ((512, 2048), (2048, 512), (384, 1024)):
        p = request.GenerationParams(prompt="scenery, sky, " + models.NAI_QUALITY[
            "nai-diffusion-5-full"], negative="lowres", model="nai-diffusion-5-full",
            width=w, height=h, seed=SEED)
        if w < resolution.MIN_SIDE or h < resolution.MIN_SIDE:
            # build_body doesn't check sides; the plugin's size picker never goes below MIN_SIDE
            pass
        run(f"side_v5_{w}x{h}", p)

    # 3. V5 tag hints: same seed (a) as the plugin sends now, (b) plus the hints NovelAI's
    # client sends, (c) hints only, without the quality/UC text typed in
    model = "nai-diffusion-5-full"
    light = models.NAI_UC[model]["Light"]
    typed = request.GenerationParams(prompt="1girl, smile, classroom, " + models.NAI_QUALITY[model],
                                     negative=light, model=model, seed=SEED)
    bare = dataclasses.replace(typed, prompt="1girl, smile, classroom", negative="")

    def hints(body):
        body["parameters"]["tag_hint_qt"] = 1           # standard quality tags
        body["parameters"]["tag_hint_uc_preset"] = 3    # Light
    run("hints_v5_a_typed", typed)
    run("hints_v5_b_typed_plus_hints", typed, hints)
    run("hints_v5_c_hints_only", bare, hints)

    print("\n== Limits check")
    for tag, outcome, spent in results:
        print(f"   {tag:34} {outcome:9} spent {spent}")
    a_img, b_img, c_img = (_image(t) for t in ("hints_v5_a_typed", "hints_v5_b_typed_plus_hints",
                                                "hints_v5_c_hints_only"))
    if a_img and b_img:
        print(f"   V5 hints: typed vs typed+hints mean pixel difference {_mean_diff(a_img, b_img)} "
              "(0 = the hints change nothing)")
    if a_img and c_img:
        print(f"   V5 hints: typed vs hints-only mean pixel difference {_mean_diff(a_img, c_img)}")
    c = _echo("hints_v5_c_hints_only")
    if c:
        print(f"   hints-only echoed prompt: {c.get('prompt')!r}")
        print(f"   hints-only echoed uc:     {c.get('uc')!r}")
        print(f"   echoed tag_hint_qt={c.get('tag_hint_qt')} tag_hint_uc_preset={c.get('tag_hint_uc_preset')}")
    save("limits_check.json", [{"tag": t, "outcome": o, "spent": sp} for t, o, sp in results])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generate", action="store_true", help="one image per model family")
    ap.add_argument("--families", default="v4.5,v5,v4,v3")
    ap.add_argument("--characters", action="store_true", help="two-character test per family")
    ap.add_argument("--free-positions", action="store_true",
                    help="V5 two-character test with off-grid positions")
    ap.add_argument("--refine", action="store_true",
                    help="img2img at 50%% on V4.5 and V5 (PAID: needs --allow-paid)")
    ap.add_argument("--inpaint", action="store_true",
                    help="inpaint a face area on V4.5 and V5 (PAID: needs --allow-paid)")
    ap.add_argument("--inpaint-strength", action="store_true",
                    help="V5 inpaint at strength 1.0 and 0.4 (free on Opus): checks partial inpaint")
    ap.add_argument("--vibe-file", help="V4.5 generation using a .naiv4vibe file (no encode)")
    ap.add_argument("--vibe", action="store_true",
                    help="encode a vibe on V4.5 and generate with it (PAID: ~2 Anlas)")
    ap.add_argument("--charref", action="store_true",
                    help="V4.5 generation with a character reference (PAID: ~5 Anlas)")
    ap.add_argument("--upscale", action="store_true", help="2x upscale (PAID: cost unknown)")
    ap.add_argument("--v5-cost", action="store_true",
                    help="same paid requests on V4.5 and V5 to measure V5's cost "
                         "(PAID: about 64 Anlas)")
    ap.add_argument("--transparent", action="store_true",
                    help="V5 Full with a transparent background (expected free)")
    ap.add_argument("--limits", action="store_true",
                    help="free checks: character limits, long sides, V5 tag hints")
    ap.add_argument("--allow-paid", action="store_true")
    a = ap.parse_args()

    token = load_token()
    if not token:
        sys.exit("No token: set NAI_TOKEN or save it in the plugin's settings first.")
    client = api.Client(token, timeout=180)
    check_balance_endpoints(client)

    if a.generate:
        for fam in a.families.split(","):
            model = FAMILY_MODELS[fam.strip()]
            st = settings.Style(name=fam, model=model,
                                noise_schedule="native" if fam == "v3" else "karras",
                                quality_tags=models.QUALITY_TAGS[models.get(model).family])
            p = request.GenerationParams(
                prompt=st.compose_prompt("1girl, black hair, school uniform, classroom, smile"),
                negative="lowres", model=model, width=832, height=1216, steps=28, seed=SEED,
                noise_schedule=st.noise_schedule)
            generate_one(client, p, fam.replace(".", "_"), a.allow_paid)
            time.sleep(2)
    if a.characters:
        for fam in a.families.split(","):
            model = FAMILY_MODELS[fam.strip()]
            if not models.get(model).has_characters:
                continue
            p = request.GenerationParams(
                prompt="2girls, park, bench, " + models.QUALITY_TAGS[models.get(model).family],
                negative="lowres", seed=SEED, model=model,
                characters=[request.Character("girl, red hair, red dress", pos=(0.3, 0.5)),
                            request.Character("girl, blue hair, blue dress", pos=(0.7, 0.5))])
            generate_one(client, p, fam.replace(".", "_") + "_two_characters", a.allow_paid)
            time.sleep(2)
    if a.transparent:
        transparent_check(client, a)
    if a.limits:
        limits_check(client, a)
    if a.v5_cost:
        v5_cost_check(client, a)
    if a.vibe_file or a.vibe or a.charref or a.upscale:
        reference_checks(client, a)
    if a.inpaint_strength:
        inpaint_strength_check(client, a)
    if a.refine or a.inpaint:
        paid_checks(client, a)
    if a.free_positions:
        # V5 free placement: off-grid points (a grid model would get 0.1/0.3/... only).
        # Red small in the top-left third, blue large in the bottom-right.
        p = request.GenerationParams(
            prompt="2girls, park, " + models.QUALITY_TAGS[models.V5], negative="lowres",
            seed=SEED, model="nai-diffusion-5-full",
            characters=[request.Character("girl, red hair, red dress", pos=(0.22, 0.35)),
                        request.Character("girl, blue hair, blue dress", pos=(0.78, 0.62))])
        generate_one(client, p, "v5_free_positions", a.allow_paid)


if __name__ == "__main__":
    main()
