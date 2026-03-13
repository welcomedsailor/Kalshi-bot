“””
quant.py
Implements the full quantitative framework from the paper:

1. Micro-Pricing Agent          r(s,q,t) = s − q·γ·σ²·(T−t)
1. Statistical Edge Detector    edge = |r(s,q,t) − s|  > threshold
1. GLFT Inventory Sizing        δ^a*(q), δ^b*(q)
1. VPIN Toxicity Monitor        VPIN = Σ|V^S − V^B| / (n·V)
1. Jump-Diffusion Execution     dx_t = μ dt + σ_b dW_t + J_t dN_t
   “””

import math
import logging
import statistics
from typing import Optional
from dataclasses import dataclass

from market_data import MarketSnapshot

log = logging.getLogger("kalshi.quant")

# ═════════════════════════════════════════════════════════════════════════════

# 1 + 2  Micro-Pricing Agent & Statistical Edge Detector

# ═════════════════════════════════════════════════════════════════════════════

class MicroPricingAgent:
“””
Computes the Avellaneda-Stoikov reservational price adapted for
binary prediction contracts.

```
    r(s, q, t) = s − q · γ · σ² · (T − t)

s   : mid-price (cents)
q   : signed inventory (+ve = long YES, -ve = short YES)
t   : fraction of contract life elapsed  [0, 1]
γ   : risk-aversion coefficient
σ²  : realised variance of price
T   : 1.0  (normalised contract life)
"""

def __init__(self, gamma: float = 0.1, T: float = 1.0,
             min_edge_cents: float = 3.0):
    self.gamma         = gamma
    self.T             = T
    self.min_edge_cents = min_edge_cents

def reservational_price(self, snap: MarketSnapshot,
                         q: float, t: float) -> Optional[float]:
    sigma = snap.volatility(20)
    if sigma is None:
        return None
    tau = max(self.T - t, 1e-6)
    return snap.mid - q * self.gamma * (sigma ** 2) * tau

def edge(self, snap: MarketSnapshot, q: float, t: float) -> Optional[float]:
    """Returns edge in cents, or None if σ not yet available."""
    r = self.reservational_price(snap, q, t)
    return abs(r - snap.mid) if r is not None else None

def has_edge(self, snap: MarketSnapshot, q: float, t: float) -> bool:
    e = self.edge(snap, q, t)
    return e is not None and e >= self.min_edge_cents
```

# ═════════════════════════════════════════════════════════════════════════════

# 3.  GLFT Inventory-Aware Sizing

# ═════════════════════════════════════════════════════════════════════════════

class GLFTSizer:
“””
Guéant-Lehalle-Fernandez-Tapia optimal spread model.

```
    δ^a*(q) = (1/κ)·ln(1 + κ/γ) + (γ/κ)·(Q − q)·f(γ,σ,κ,A)
    δ^b*(q) = (1/κ)·ln(1 + κ/γ) + (γ/κ)·(Q + q)·f(γ,σ,κ,A)

κ   : order-book depth / fill-rate parameter
γ   : risk aversion
Q   : max inventory (contracts)
A   : market order arrival intensity
"""

def __init__(self, kappa: float = 1.5, gamma: float = 0.1,
             Q: float = 50.0, A: float = 0.5):
    self.kappa = kappa
    self.gamma = gamma
    self.Q     = Q
    self.A     = A

def _f(self, sigma: float) -> float:
    return math.sqrt(self.gamma / (2 * self.A * self.kappa)) * sigma

def ask_spread_cents(self, q: float, sigma: float) -> float:
    base = (1 / self.kappa) * math.log(1 + self.kappa / self.gamma)
    adj  = (self.gamma / self.kappa) * (self.Q - q) * self._f(sigma)
    return max(base + adj, 0.5)   # minimum 0.5 cents

def bid_spread_cents(self, q: float, sigma: float) -> float:
    base = (1 / self.kappa) * math.log(1 + self.kappa / self.gamma)
    adj  = (self.gamma / self.kappa) * (self.Q + q) * self._f(sigma)
    return max(base + adj, 0.5)

def optimal_quotes(self, snap: MarketSnapshot,
                   q: float) -> Optional[tuple[int, int]]:
    """
    Returns (bid_price_cents, ask_price_cents) for limit orders.
    Returns None if σ not yet available.
    """
    sigma = snap.volatility(20)
    if sigma is None:
        return None
    mid = snap.mid
    da  = self.ask_spread_cents(q, sigma)
    db  = self.bid_spread_cents(q, sigma)
    bid = max(1,  int(round(mid - db)))
    ask = min(99, int(round(mid + da)))
    return bid, ask

def position_size(self, snap: MarketSnapshot, q: float,
                  balance: float, confidence: float) -> int:
    """
    Scales contract count by:
     - remaining inventory headroom  (Q − |q|)
     - signal confidence
     - max 5% of balance per trade
    """
    headroom = max(0, self.Q - abs(q))
    price    = snap.mid / 100          # dollars per contract
    if price <= 0:
        return 0
    max_by_balance = int((balance * 0.05) / price)
    raw = int(headroom * confidence)
    return max(1, min(raw, max_by_balance, int(self.Q)))
```

# ═════════════════════════════════════════════════════════════════════════════

# 4.  VPIN Toxicity Monitor

# ═════════════════════════════════════════════════════════════════════════════

class VPINMonitor:
“””
Volume-synchronised Probability of Informed Trading.

```
    VPIN = (1/n) · Σ_{τ=1}^{n}  |V_τ^S − V_τ^B| / V

Buckets trades by volume, classifies each bucket's buy/sell imbalance.
VPIN > threshold → toxic flow → suspend trading.
"""

def __init__(self, n_buckets: int = 10, threshold: float = 0.65):
    self.n         = n_buckets
    self.threshold = threshold

def compute(self, trades: list[dict]) -> Optional[float]:
    if len(trades) < self.n:
        return None

    total_v = sum(t["count"] for t in trades)
    if total_v == 0:
        return None

    bucket_v = total_v / self.n
    imbalances = []
    buy_v = sell_v = running = 0.0

    for t in trades:
        size = float(t["count"])
        # taker_side == 'yes' → buyer-initiated
        if t.get("taker_side", "yes") == "yes":
            buy_v += size
        else:
            sell_v += size
        running += size

        if running >= bucket_v:
            imbalances.append(abs(buy_v - sell_v))
            buy_v = sell_v = running = 0.0

    if not imbalances:
        return None

    return sum(imbalances) / (len(imbalances) * bucket_v)

def is_toxic(self, trades: list[dict]) -> tuple[bool, float]:
    vpin = self.compute(trades)
    if vpin is None:
        return False, 0.0
    return vpin > self.threshold, round(vpin, 3)
```

# ═════════════════════════════════════════════════════════════════════════════

# 5.  Jump-Diffusion Execution Agent

# ═════════════════════════════════════════════════════════════════════════════

class JumpDiffusionAgent:
“””
Models price as:  dx_t = μ(x_t) dt + σ_b dW_t + J_t dN_t

```
Estimates jump intensity λ and adjusts limit prices to avoid
being adversely filled into a jump (widen quotes when P(jump) is high).
"""

def __init__(self, jump_threshold_cents: float = 4.0,
             window: int = 30,
             critical_mass: float = 0.20):
    self.jump_threshold  = jump_threshold_cents
    self.window          = window
    self.critical_mass   = critical_mass   # halt if P(jump) > this

def jump_probability(self, snap: MarketSnapshot) -> float:
    h = snap.price_history
    if len(h) < self.window:
        return 0.0
    recent = h[-self.window:]
    jumps  = sum(
        1 for i in range(1, len(recent))
        if abs(recent[i] - recent[i - 1]) >= self.jump_threshold
    )
    return jumps / max(len(recent) - 1, 1)

def adjust_price(self, base_price: int, side: str,
                 jump_prob: float) -> int:
    """
    Buffer limit price away from fair value proportional to jump risk.
    side: 'yes' (buying) → lower bid, 'no' (selling) → raise ask.
    """
    buffer = int(round(jump_prob * 5))   # up to 5 cents buffer
    if side == "yes":
        return max(1,  base_price - buffer)
    else:
        return min(99, base_price + buffer)

def is_critical(self, snap: MarketSnapshot) -> bool:
    """True if jump risk is too high to trade safely."""
    return self.jump_probability(snap) > self.critical_mass
```

# ═════════════════════════════════════════════════════════════════════════════

# 6.  Order-Book Entropy Collapse Detector  (Section 7 of framework)

# ═════════════════════════════════════════════════════════════════════════════

class EntropyMonitor:
“””
Tracks rolling order-book entropy H(t).
A sudden collapse (large drop from recent average) may signal a
strategic withdrawal of market-makers or an imminent deterministic jump.
“””

```
def __init__(self, window: int = 10, collapse_sigma: float = 2.0):
    self.window         = window
    self.collapse_sigma = collapse_sigma
    self.history:  dict[str, list[float]] = {}

def update(self, ticker: str, snap: MarketSnapshot) -> Optional[float]:
    h = snap.order_book_entropy()
    if h is None:
        return None
    self.history.setdefault(ticker, []).append(h)
    if len(self.history[ticker]) > self.window * 3:
        self.history[ticker].pop(0)
    return h

def is_collapsing(self, ticker: str) -> bool:
    hist = self.history.get(ticker, [])
    if len(hist) < self.window:
        return False
    recent   = hist[-1]
    baseline = statistics.mean(hist[-self.window - 1:-1])
    std      = statistics.stdev(hist[-self.window - 1:-1]) if len(hist) > 2 else 0
    if std == 0:
        return False
    z = (baseline - recent) / std
    return z > self.collapse_sigma
```
