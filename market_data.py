"""
market_data.py
MarketSnapshot dataclass + MarketAnalyzer that keeps rolling price/trade history.
"""

import math
import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("kalshi.market_data")

@dataclass
class OrderBookLevel:
price: int   # cents
delta: int   # size at this level

@dataclass
class MarketSnapshot:
ticker:       str
series:       str
yes_bid:      int    # cents  (best bid for YES)
yes_ask:      int    # cents  (best ask for YES)
last_price:   int    # cents
volume:       int    # total contracts traded
open_interest:int

```
bids: list[OrderBookLevel] = field(default_factory=list)
asks: list[OrderBookLevel] = field(default_factory=list)

price_history: list[int]  = field(default_factory=list)   # yes_bid per tick
trade_history: list[dict] = field(default_factory=list)   # raw trade objects

# ── Derived ──────────────────────────────────────────────────────────────

@property
def mid(self) -> float:
    return (self.yes_bid + self.yes_ask) / 2

@property
def spread(self) -> int:
    return self.yes_ask - self.yes_bid

@property
def bid_depth(self) -> int:
    return sum(lv.delta for lv in self.bids)

@property
def ask_depth(self) -> int:
    return sum(lv.delta for lv in self.asks)

def price_change_pct(self, n: int = 5) -> Optional[float]:
    if len(self.price_history) < n + 1:
        return None
    old = self.price_history[-n - 1]
    new = self.price_history[-1]
    return (new - old) / old * 100 if old else None

def moving_average(self, n: int = 5) -> Optional[float]:
    if len(self.price_history) < n:
        return None
    return statistics.mean(self.price_history[-n:])

def volatility(self, n: int = 20) -> Optional[float]:
    """Annualised-style realised vol from log-returns of price_history."""
    if len(self.price_history) < n + 1:
        return None
    window = self.price_history[-n - 1:]
    returns = []
    for i in range(1, len(window)):
        if window[i] > 0 and window[i - 1] > 0:
            returns.append(math.log(window[i] / window[i - 1]))
    return statistics.stdev(returns) if len(returns) > 1 else None

def order_book_entropy(self) -> Optional[float]:
    """
    Shannon entropy H(t) on LOB density — section 7 of framework.
    High entropy = diffuse book (normal).
    Sudden collapse in entropy signals information event / jump.
    """
    levels = self.bids + self.asks
    total  = sum(lv.delta for lv in levels)
    if total == 0 or len(levels) < 2:
        return None
    probs = [lv.delta / total for lv in levels if lv.delta > 0]
    return -sum(p * math.log(p) for p in probs)

def imbalance(self) -> Optional[float]:
    """
    Signed order imbalance I^S − I^B (used in GP kernel, section 6).
    Positive = more ask-side pressure → price likely to fall.
    """
    total = self.bid_depth + self.ask_depth
    if total == 0:
        return None
    return (self.ask_depth - self.bid_depth) / total
```

class MarketAnalyzer:
“”“Fetches and maintains rolling snapshots for a set of tickers.”””

```
def __init__(self, client, history_size: int = 200):
    self.client       = client
    self.history_size = history_size
    self.snapshots:  dict[str, MarketSnapshot] = {}

def update(self, ticker: str) -> MarketSnapshot:
    market = self.client.get_market(ticker)
    book   = self.client.get_orderbook(ticker, depth=10)
    trades = self.client.get_trades(ticker, limit=100)

    bids = [OrderBookLevel(int(b.price), int(b.delta))
            for b in (book.yes or [])]
    asks = [OrderBookLevel(int(a.price), int(a.delta))
            for a in (book.no  or [])]

    yes_bid = bids[0].price if bids else int(getattr(market, "yes_bid", 1))
    yes_ask = asks[0].price if asks else int(getattr(market, "yes_ask", 99))

    raw_trades = []
    for t in trades:
        raw_trades.append({
            "price": int(getattr(t, "yes_price", 50)),
            "count": int(getattr(t, "count", 0)),
            "taker_side": getattr(t, "taker_side", "yes"),
        })

    if ticker not in self.snapshots:
        # extract series ticker from market object or ticker string
        series = getattr(market, "series_ticker",
                         ticker.rsplit("-", 2)[0] if "-" in ticker else ticker)
        self.snapshots[ticker] = MarketSnapshot(
            ticker=ticker,
            series=series,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            last_price=int(getattr(market, "last_price", 50)),
            volume=int(getattr(market, "volume", 0)),
            open_interest=int(getattr(market, "open_interest", 0)),
        )

    snap = self.snapshots[ticker]
    snap.yes_bid      = yes_bid
    snap.yes_ask      = yes_ask
    snap.last_price   = int(getattr(market, "last_price", snap.last_price))
    snap.volume       = int(getattr(market, "volume", snap.volume))
    snap.open_interest= int(getattr(market, "open_interest", snap.open_interest))
    snap.bids         = bids
    snap.asks         = asks
    snap.trade_history= raw_trades

    snap.price_history.append(yes_bid)
    if len(snap.price_history) > self.history_size:
        snap.price_history.pop(0)

    return snap

def summary(self, ticker: str) -> str:
    s = self.snapshots.get(ticker)
    if not s:
        return f"{ticker}: no data"
    chg = s.price_change_pct()
    ent = s.order_book_entropy()
    imb = s.imbalance()
    return (
        f"{ticker} | "
        f"bid={s.yes_bid}¢  ask={s.yes_ask}¢  spread={s.spread}¢  "
        f"vol={s.volume}  "
        f"Δ5={f'{chg:+.1f}%' if chg is not None else 'n/a'}  "
        f"H={f'{ent:.2f}' if ent is not None else 'n/a'}  "
        f"imb={f'{imb:+.2f}' if imb is not None else 'n/a'}"
    )
```
