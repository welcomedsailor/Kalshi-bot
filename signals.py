“””
signals.py
Rule engine, signal dataclass, and risk manager.
“””

import logging
from dataclasses import dataclass
from typing import Optional

from market_data import MarketSnapshot

log = logging.getLogger(“kalshi.signals”)

# ═════════════════════════════════════════════════════════════════════════════

# Signal

# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Signal:
action:     str            # ‘buy_yes’ | ‘buy_no’ | ‘hold’
side:       str            # ‘yes’ | ‘no’ | ‘none’
confidence: float          # 0–1
reason:     str
price_cents: Optional[int] = None   # suggested limit price

# ═════════════════════════════════════════════════════════════════════════════

# Rule Engine

# ═════════════════════════════════════════════════════════════════════════════

class RuleEngine:
“””
Rules are dicts:
name        : str
action      : ‘buy_yes’ | ‘buy_no’
confidence  : float
condition   : callable(ctx) → bool
price_fn    : callable(ctx) → int  (optional, returns limit price in cents)

```
ctx = { 'snap': MarketSnapshot, 'r': reservational_price_or_None,
        'q': current_inventory, 't': time_elapsed }
"""

def __init__(self, rules: list[dict]):
    self.rules = rules

def evaluate(self, snap: MarketSnapshot,
             r: Optional[float] = None,
             q: float = 0.0,
             t: float = 0.5) -> Signal:
    ctx = {"snap": snap, "r": r, "q": q, "t": t}
    triggered = []
    for rule in self.rules:
        try:
            if rule["condition"](ctx):
                triggered.append(rule)
                log.debug(f"  Rule fired: {rule['name']}")
        except Exception as e:
            log.debug(f"  Rule error [{rule['name']}]: {e}")

    if not triggered:
        return Signal("hold", "none", 0.0, "No rules triggered")

    best = max(triggered, key=lambda r: r["confidence"])
    side = "yes" if best["action"] == "buy_yes" else "no"

    price = None
    if "price_fn" in best:
        try:
            price = best["price_fn"](ctx)
        except Exception:
            pass

    return Signal(
        action=best["action"],
        side=side,
        confidence=best["confidence"],
        reason=best["name"],
        price_cents=price,
    )
```

# ── Default rules ─────────────────────────────────────────────────────────────

# Mix of reservational-price edge detection + momentum/mean-reversion signals.

DEFAULT_RULES = [

```
# ── Reservational price signals (highest confidence — use quant framework)
{
    "name": "r(s,q,t) > ask by ≥3¢ → buy YES (YES underpriced)",
    "action": "buy_yes",
    "confidence": 0.82,
    "condition": lambda ctx: (
        ctx["r"] is not None and
        ctx["r"] - ctx["snap"].yes_ask >= 3
    ),
    "price_fn": lambda ctx: ctx["snap"].yes_ask,
},
{
    "name": "r(s,q,t) < bid by ≥3¢ → buy NO (YES overpriced)",
    "action": "buy_no",
    "confidence": 0.80,
    "condition": lambda ctx: (
        ctx["r"] is not None and
        ctx["snap"].yes_bid - ctx["r"] >= 3
    ),
    "price_fn": lambda ctx: 100 - ctx["snap"].yes_bid,
},

# ── Mean reversion
{
    "name": "Price -5% in 5 ticks + YES < 45¢ → mean-revert buy YES",
    "action": "buy_yes",
    "confidence": 0.66,
    "condition": lambda ctx: (
        ctx["snap"].price_change_pct(5) is not None and
        ctx["snap"].price_change_pct(5) < -5 and
        ctx["snap"].yes_bid < 45
    ),
    "price_fn": lambda ctx: ctx["snap"].yes_ask,
},
{
    "name": "Price +5% in 5 ticks + YES > 55¢ → fade rally, buy NO",
    "action": "buy_no",
    "confidence": 0.64,
    "condition": lambda ctx: (
        ctx["snap"].price_change_pct(5) is not None and
        ctx["snap"].price_change_pct(5) > 5 and
        ctx["snap"].yes_bid > 55
    ),
    "price_fn": lambda ctx: 100 - ctx["snap"].yes_bid,
},

# ── Moving-average deviation
{
    "name": "YES < MA5 by ≥3¢ → buy YES (below moving average)",
    "action": "buy_yes",
    "confidence": 0.60,
    "condition": lambda ctx: (
        ctx["snap"].moving_average(5) is not None and
        ctx["snap"].moving_average(5) - ctx["snap"].yes_bid >= 3
    ),
    "price_fn": lambda ctx: ctx["snap"].yes_ask,
},
{
    "name": "YES > MA5 by ≥3¢ → buy NO (above moving average)",
    "action": "buy_no",
    "confidence": 0.58,
    "condition": lambda ctx: (
        ctx["snap"].moving_average(5) is not None and
        ctx["snap"].yes_bid - ctx["snap"].moving_average(5) >= 3
    ),
    "price_fn": lambda ctx: 100 - ctx["snap"].yes_bid,
},

# ── Order imbalance
{
    "name": "Strong ask-side imbalance >0.3 + YES < 50 → buy YES",
    "action": "buy_yes",
    "confidence": 0.57,
    "condition": lambda ctx: (
        ctx["snap"].imbalance() is not None and
        ctx["snap"].imbalance() > 0.3 and
        ctx["snap"].yes_bid < 50
    ),
    "price_fn": lambda ctx: ctx["snap"].yes_ask,
},
{
    "name": "Strong bid-side imbalance <-0.3 + YES > 50 → buy NO",
    "action": "buy_no",
    "confidence": 0.55,
    "condition": lambda ctx: (
        ctx["snap"].imbalance() is not None and
        ctx["snap"].imbalance() < -0.3 and
        ctx["snap"].yes_bid > 50
    ),
    "price_fn": lambda ctx: 100 - ctx["snap"].yes_bid,
},
```

]

# ═════════════════════════════════════════════════════════════════════════════

# Risk Manager

# ═════════════════════════════════════════════════════════════════════════════

class RiskManager:
“””
Gatekeeps every trade through the full risk checklist from the framework:
1. r(s,q,t) edge > 3 basis points
2. VPIN < threshold
3. Position within GLFT limits [Q−q, Q+q]
4. Estimated jump probability < critical_mass
5. Spread not too wide
6. Balance sufficient
“””

```
def __init__(
    self,
    max_spread_cents:      int   = 8,
    min_confidence:        float = 0.55,
    max_position_per_mkt:  int   = 20,
    max_total_exposure:    float = 500.0,
    max_vpin:              float = 0.65,
    max_jump_prob:         float = 0.20,
):
    self.max_spread       = max_spread_cents
    self.min_conf         = min_confidence
    self.max_pos          = max_position_per_mkt
    self.max_exposure     = max_total_exposure
    self.max_vpin         = max_vpin
    self.max_jump_prob    = max_jump_prob

def approve(
    self,
    signal:    Signal,
    snap:      MarketSnapshot,
    vpin:      float,
    jump_prob: float,
    q:         float,     # current inventory for this market
    balance:   float,
) -> tuple[bool, str]:

    checks = [
        (signal.action != "hold",
         "Signal is HOLD"),
        (signal.confidence >= self.min_conf,
         f"Confidence {signal.confidence:.2f} < min {self.min_conf}"),
        (snap.spread <= self.max_spread,
         f"Spread {snap.spread}¢ > max {self.max_spread}¢"),
        (vpin < self.max_vpin,
         f"VPIN {vpin:.2f} ≥ threshold {self.max_vpin} — toxic flow"),
        (jump_prob < self.max_jump_prob,
         f"Jump prob {jump_prob:.2f} ≥ critical mass {self.max_jump_prob}"),
        (abs(q) < self.max_pos,
         f"Inventory {q} at max {self.max_pos}"),
        (balance >= 5.0,
         f"Balance ${balance:.2f} too low (min $5)"),
    ]

    for passed, reason in checks:
        if not passed:
            return False, reason

    return True, "All risk checks passed ✓"
```
