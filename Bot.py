“””
bot.py
Main trading loop — orchestrates all quant components.
Configured for Railway deployment.

Usage:
python bot.py

Set dry_run=True (default) to simulate without placing real orders.
Set dry_run=False only when you’re ready to trade live.
“””

import os
import time
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from kalshi_client import KalshiClient
from market_data   import MarketAnalyzer
from quant         import MicroPricingAgent, GLFTSizer, VPINMonitor, JumpDiffusionAgent, EntropyMonitor
from signals       import RuleEngine, RiskManager, Signal, DEFAULT_RULES

logging.basicConfig(
level=logging.INFO,
format=”%(asctime)s [%(levelname)s] %(message)s”,
datefmt=”%Y-%m-%d %H:%M:%S”,
)
log = logging.getLogger(“kalshi.bot”)

class KalshiQuantBot:
“””
Full pipeline every tick:
1. Fetch & update market snapshot
2. Compute reservational price r(s,q,t)
3. Detect statistical edge
4. Check VPIN toxicity
5. Check jump-diffusion risk
6. Check order-book entropy collapse
7. Evaluate rule engine → Signal
8. GLFT-size the position
9. Risk-manager gate
10. Place / log order
11. Backtest metrics update
“””

```
def __init__(
    self,
    watchlist:            list[str],
    poll_interval_secs:   int   = 30,
    dry_run:              bool  = True,
    rules:                list  = None,
    gamma:                float = 0.1,
    glft_Q:               float = 50.0,
):
    self.watchlist     = watchlist
    self.poll_interval = poll_interval_secs
    self.dry_run       = dry_run

    # Clients & data
    self.client   = KalshiClient()
    self.analyzer = MarketAnalyzer(self.client)

    # Quant components
    self.pricer   = MicroPricingAgent(gamma=gamma)
    self.sizer    = GLFTSizer(gamma=gamma, Q=glft_Q)
    self.vpin     = VPINMonitor(n_buckets=10, threshold=0.65)
    self.jumps    = JumpDiffusionAgent(jump_threshold_cents=4.0, critical_mass=0.20)
    self.entropy  = EntropyMonitor(window=10, collapse_sigma=2.0)

    # Signals & risk
    self.engine   = RuleEngine(rules or DEFAULT_RULES)
    self.risk     = RiskManager()

    # Inventory tracker  { ticker → signed contract count }
    self.inventory: dict[str, float] = {t: 0.0 for t in watchlist}

    # Performance tracker
    self.stats = {
        "ticks": 0, "trades": 0, "blocked_vpin": 0,
        "blocked_jump": 0, "blocked_entropy": 0, "blocked_risk": 0,
    }

    log.info("=" * 65)
    log.info("Kalshi Quant Bot initialised")
    log.info(f"  Watchlist : {watchlist}")
    log.info(f"  Dry run   : {dry_run}")
    log.info(f"  γ         : {gamma}   Q_max={glft_Q}")
    log.info(f"  Poll      : {poll_interval_secs}s")
    log.info("=" * 65)

# ── Main loop ─────────────────────────────────────────────────────────────

def run(self):
    log.info("Bot started — press Ctrl+C to stop")
    while True:
        try:
            self._tick()
        except KeyboardInterrupt:
            self._print_stats()
            log.info("Bot stopped.")
            break
        except Exception as e:
            log.error(f"Tick error: {e}", exc_info=True)
        time.sleep(self.poll_interval)

def _tick(self):
    self.stats["ticks"] += 1
    balance = self.client.get_balance()
    log.info(f"── Tick {self.stats['ticks']} │ Balance: ${balance:.2f} ─────────")

    for ticker in self.watchlist:
        try:
            self._process_market(ticker, balance)
        except Exception as e:
            log.warning(f"  [{ticker}] Error: {e}")

# ── Per-market pipeline ───────────────────────────────────────────────────

def _process_market(self, ticker: str, balance: float):
    # 1. Update snapshot
    snap = self.analyzer.update(ticker)
    log.info(f"  {self.analyzer.summary(ticker)}")

    q = self.inventory.get(ticker, 0.0)
    t = 0.5   # TODO: compute from market close_time if needed

    # 2. Reservational price
    r = self.pricer.reservational_price(snap, q, t)
    r_str = f"{r:.1f}¢" if r is not None else "n/a (warming up)"
    log.info(f"    r(s,q,t)={r_str}  q={q}")

    # 3. Entropy collapse check
    self.entropy.update(ticker, snap)
    if self.entropy.is_collapsing(ticker):
        log.info(f"    ⚠ Entropy collapse detected — skipping {ticker}")
        self.stats["blocked_entropy"] += 1
        return

    # 4. VPIN check
    toxic, vpin_val = self.vpin.is_toxic(snap.trade_history)
    log.info(f"    VPIN={vpin_val:.3f}  toxic={toxic}")
    if toxic:
        log.info(f"    ✗ VPIN too high — halting on {ticker}")
        self.stats["blocked_vpin"] += 1
        return

    # 5. Jump-diffusion check
    jump_prob = self.jumps.jump_probability(snap)
    log.info(f"    P(jump)={jump_prob:.3f}")
    if self.jumps.is_critical(snap):
        log.info(f"    ✗ Jump risk critical — halting on {ticker}")
        self.stats["blocked_jump"] += 1
        return

    # 6. Evaluate signals
    signal: Signal = self.engine.evaluate(snap, r=r, q=q, t=t)
    if signal.action == "hold":
        log.info(f"    → HOLD ({signal.reason})")
        return
    log.info(f"    → Signal: {signal.action} | conf={signal.confidence:.2f} | {signal.reason}")

    # 7. GLFT optimal quotes
    quotes = self.sizer.optimal_quotes(snap, q)
    if quotes:
        log.info(f"    GLFT quotes: bid={quotes[0]}¢  ask={quotes[1]}¢")

    # 8. Size
    size = self.sizer.position_size(snap, q, balance, signal.confidence)

    # 9. Jump-adjust limit price
    raw_price = signal.price_cents or snap.yes_ask
    limit_price = self.jumps.adjust_price(raw_price, signal.side, jump_prob)

    # 10. Risk gate
    approved, reason = self.risk.approve(
        signal, snap, vpin_val, jump_prob, q, balance
    )
    if not approved:
        log.info(f"    ✗ Risk check: {reason}")
        self.stats["blocked_risk"] += 1
        return

    # 11. Execute
    self._execute(ticker, signal, limit_price, size)

def _execute(self, ticker: str, signal: Signal,
             price: int, size: int):
    if self.dry_run:
        log.info(
            f"    [DRY RUN] BUY {size}x {signal.side.upper()} "
            f"@ {price}¢ on {ticker}"
        )
        return

    try:
        if signal.side == "yes":
            result = self.client.place_order(
                ticker=ticker, side="yes", count=size, yes_price=price
            )
        else:
            no_price = 100 - price
            result = self.client.place_order(
                ticker=ticker, side="no", count=size, no_price=no_price
            )
        self.inventory[ticker] = self.inventory.get(ticker, 0) + (
            size if signal.side == "yes" else -size
        )
        self.stats["trades"] += 1
        log.info(f"    ✓ Order placed: {result}")
    except Exception as e:
        log.error(f"    Order failed: {e}")

def _print_stats(self):
    log.info("─" * 50)
    log.info("Session summary:")
    for k, v in self.stats.items():
        log.info(f"  {k:<20}: {v}")
    log.info("─" * 50)
```

# ── Entry point ───────────────────────────────────────────────────────────────

if **name** == “**main**”:

```
# ┌─────────────────────────────────────────────────────────────┐
# │  Add market tickers here.                                   │
# │  Run `python discover.py` to browse open markets.           │
# └─────────────────────────────────────────────────────────────┘
WATCHLIST = [
    # "KXINXD-26MAR13-T5800",   # S&P 500 example
    # "KXFED-26MAR20-B450",     # Fed rate example
]

if not WATCHLIST:
    log.warning("WATCHLIST is empty — add tickers from `python discover.py`")
else:
    bot = KalshiQuantBot(
        watchlist=WATCHLIST,
        poll_interval_secs=30,
        dry_run=True,        # ← flip to False for live trading
        gamma=0.1,
        glft_Q=50.0,
    )
    bot.run()
```
