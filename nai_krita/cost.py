"""Anlas guard logic: free test, cost estimate, balance parsing. Krita-free.

NovelAI does not publish its pricing formula. `estimate_cost` is a widely
reported approximation of the website's calculation, with a V5 multiplier
fitted to two reported data points. Always show it as "estimated" and
calibrate it with before/after balances.
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional

from . import models
from .request import GenerationParams
from .resolution import FREE_PIXELS

OPUS_TIER = 3
FREE_MAX_STEPS = 28
V5_MULTIPLIER = 1.5  # measured live 2026-11-13 (tests/fixtures/cost_v5_check.json): V5 Full
                     # costs 1.5x V4.5 Full's rounded cost at 512x512 and 832x1216, 29 steps
# (caru-ini/novelai-sdk applies no V5 multiplier; calibrate with a paid V5 request)
CHAR_REF_COST = 5    # per character reference per image, Opus included (reported)
VIBE_ENCODE_COST = 2 # per new vibe encoding (reported)
MAX_FREE_VIBES = 4   # vibes beyond this cost VIBE_ENCODE_COST each (reported)


@dataclass
class Balance:
    tier: int = -1                 # 0 Paper, 1 Tablet, 2 Scroll, 3 Opus; -1 unknown
    active: bool = False
    fixed: int = 0                 # subscription Anlas
    purchased: int = 0             # paid Anlas
    v5_allowance: Optional[float] = None   # remaining V5 usage, if reported
    v5_allowance_raw: object = None        # whatever the server sent, for display/debug

    @property
    def total(self) -> int:
        return self.fixed + self.purchased

    @property
    def is_opus(self) -> bool:
        return self.active and self.tier >= OPUS_TIER

    @property
    def tier_name(self) -> str:
        return {0: "Paper", 1: "Tablet", 2: "Scroll", 3: "Opus"}.get(self.tier, "Unknown")


def parse_balance(data: dict) -> Balance:
    """Accepts /user/data (with a 'subscription' key) or /user/subscription."""
    sub = data.get("subscription", data) if isinstance(data, dict) else {}
    steps = sub.get("trainingStepsLeft") or {}
    if isinstance(steps, (int, float)):   # older shape: a single number
        steps = {"fixedTrainingStepsLeft": steps}
    b = Balance(
        tier=int(sub.get("tier", -1)),
        active=bool(sub.get("active", False)),
        fixed=int(steps.get("fixedTrainingStepsLeft", 0) or 0),
        purchased=int(steps.get("purchasedTrainingSteps", 0) or 0),
    )
    usage = sub.get("usage", data.get("usage") if isinstance(data, dict) else None)
    b.v5_allowance_raw = usage
    b.v5_allowance = _find_remaining(usage)
    return b


def _find_remaining(usage) -> Optional[float]:
    """Remaining V5 allowance in percent.

    Verified 2026-11-13: /user/subscription has
    "usage": {"percent": 100, "purchasedPercent": 0, "isNegative": false,
              "timeUntilNextPercent": 7888}
    where percent is the allowance left (100 = full). isNegative is treated as
    used up. The other branches are fallbacks for shape changes.
    """
    if isinstance(usage, dict) and isinstance(usage.get("percent"), (int, float)):
        if usage.get("isNegative"):
            return 0.0
        return float(usage["percent"])
    if isinstance(usage, (int, float)):
        return float(usage)
    if isinstance(usage, dict):
        for key in ("remaining", "left", "available"):
            if isinstance(usage.get(key), (int, float)):
                return float(usage[key])
        if isinstance(usage.get("limit"), (int, float)) and isinstance(usage.get("used"), (int, float)):
            return float(usage["limit"] - usage["used"])
        for v in usage.values():
            found = _find_remaining(v)
            if found is not None:
                return found
    return None


@dataclass
class Verdict:
    free: bool
    reasons: List[str] = field(default_factory=list)  # why it is paid, in plain words
    estimate: int = 0                                  # estimated Anlas, 0 when free

    def describe(self) -> str:
        if self.free:
            return "Free (Opus)"
        return "; ".join(self.reasons)


def judge(p: GenerationParams, balance: Optional[Balance]) -> Verdict:
    """Apply the free test (Opus: one image, <= 1 MP, <= 28 steps, V5 allowance left). Anything not provably free is paid."""
    reasons = []
    if balance is None or balance.tier < 0:
        reasons.append("Subscription tier unknown (balance not loaded)")
    elif not balance.is_opus:
        reasons.append(f"{balance.tier_name} tier pays for every image")
    if p.n_samples > 1:
        reasons.append(f"Batch of {p.n_samples} images in one request")
    # Refine (img2img) and Fill (infill) are NOT extra conditions: measured 2026-11-13
    # on Opus, V4.5 and V5 at 832x1216 / 28 steps both cost 0 Anlas.
    if p.width * p.height > FREE_PIXELS:
        reasons.append(f"{p.width}x{p.height} is above the free 1024x1024 pixel limit")
    if p.steps > FREE_MAX_STEPS:
        reasons.append(f"{p.steps} steps is above the free limit of {FREE_MAX_STEPS}")
    if p.info.family == models.V5 and balance is not None and balance.is_opus:
        if balance.v5_allowance is None:
            reasons.append("V5 allowance unknown")
        elif balance.v5_allowance <= 0:
            reasons.append("V5 Opus allowance is used up")
    free_slot = balance is not None and balance.is_opus
    if p.info.family == models.V5 and free_slot and not (balance.v5_allowance or 0) > 0:
        free_slot = False
    estimate = estimate_cost(p, free_slot) if reasons else 0
    # Reference extras are charged on top, Opus included. Measured live 2026-11-13:
    # character reference 5 per image, vibe encode 2; a stored vibe encoding is free.
    if p.char_refs:
        extra = CHAR_REF_COST * len(p.char_refs) * p.n_samples
        reasons.append(f"Character reference: {CHAR_REF_COST} Anlas per reference per image "
                       "(charged on Opus too)")
        estimate += extra
    if p.vibe_encodes:
        reasons.append(f"Encoding {p.vibe_encodes} new vibe(s), about {VIBE_ENCODE_COST} each "
                       "(cached afterwards)")
        estimate += VIBE_ENCODE_COST * p.vibe_encodes
    if len(p.vibes) > MAX_FREE_VIBES:
        over = len(p.vibes) - MAX_FREE_VIBES
        reasons.append(f"{len(p.vibes)} vibes: each beyond {MAX_FREE_VIBES} costs about "
                       f"{VIBE_ENCODE_COST}")
        estimate += VIBE_ENCODE_COST * over
    return Verdict(free=not reasons, reasons=reasons, estimate=estimate)


QUEUE, BATCH = "queue", "batch"
MAX_IMAGES = 8
MAX_BATCH = 4   # VERIFY NovelAI's n_samples cap before allowing more


def plan(base: GenerationParams, count: int, mode: str, balance: Optional[Balance]):
    """Split one Generate click into requests.

    Queue: `count` requests with n_samples=1, each judged on its own; seeds are
    random, or base.seed, base.seed+1, ... when a seed is set.
    Batch: one request with n_samples=count (always paid, even on Opus).

    Returns (list of (params, verdict), combined verdict for the whole click).
    """
    if not 1 <= count <= MAX_IMAGES:
        raise ValueError(f"image count must be 1..{MAX_IMAGES}")
    if mode == BATCH and count > MAX_BATCH:
        raise ValueError(f"Batch mode allows at most {MAX_BATCH} images per request")
    if mode == BATCH or count == 1:
        p = base.with_seed()
        p.n_samples = count
        jobs = [(p, judge(p, balance))]
    else:
        jobs = []
        for i in range(count):
            p = base.with_seed() if not base.seed else _seeded(base, base.seed + i)
            p.n_samples = 1
            if i > 0:
                p.vibe_encodes = 0   # the first request encodes; later ones hit the cache
            jobs.append((p, judge(p, balance)))
    reasons = []
    for _, v in jobs:
        for r in v.reasons:
            if r not in reasons:
                reasons.append(r)
    paid = [v for _, v in jobs if not v.free]
    if paid and len(jobs) > 1:
        reasons.append(f"{len(paid)} of {len(jobs)} requests cost Anlas")
    combined = Verdict(free=not paid, reasons=reasons if paid else [],
                       estimate=sum(v.estimate for v in paid))
    return jobs, combined


def _seeded(base: GenerationParams, seed: int) -> GenerationParams:
    p = base.with_seed()
    p.seed = (seed - 1) % 4294967295 + 1
    return p


def estimate_cost(p: GenerationParams, is_opus: bool) -> int:
    """Estimated Anlas for one request (all n_samples)."""
    pixels = max(p.width * p.height, 65536)
    per_sample = 2.951823174884865e-6 * pixels + 5.753298233447344e-7 * pixels * p.steps
    if p.action == "img2img":
        per_sample *= max(0.01, min(1.0, p.strength))
    per_sample = max(2, math.ceil(per_sample))
    if p.info.family == models.V5:
        # applied to the rounded base cost: 512x512 at 29 steps is 6 on V4.5 and 9 on
        # V5 (measured 2026-11-13), not ceil(5.15 * 1.5) = 8
        per_sample = math.ceil(per_sample * V5_MULTIPLIER)
    charged = p.n_samples
    if is_opus and p.steps <= FREE_MAX_STEPS and p.width * p.height <= FREE_PIXELS:
        charged -= 1  # Opus: one image per request is free at free sizes
    return per_sample * max(0, charged)
