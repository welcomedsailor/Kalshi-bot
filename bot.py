import os
import sys
import time
import logging
from dotenv import load_dotenv

load_dotenv()

def check_env():
    missing = []
    if not os.getenv("KALSHI_API_KEY_ID"):
    missing.append("KALSHI_API_KEY_ID")
    if not os.getenv("KALSHI_PRIVATE_KEY_CONTENT") and   
    not os.path.exists(os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi.key")):
    missing.append("KALSHI_PRIVATE_KEY_CONTENT")
    if missing:
    print("ERROR: Missing required environment variables:")
    for m in missing:
    print("  - " + m)
    sys.exit(1)

check_env()

from kalshi_client import KalshiClient
from market_data import MarketAnalyzer
from quant import MicroPricingAgent, GLFTSizer, VPINMonitor, JumpDiffusionAgent, EntropyMonitor
from signals import RuleEngine, RiskManager, Signal, DEFAULT_RULES

logging.basicConfig(
level=logging.INFO,
format="%(asctime)s [%(levelname)s] %(message)s",
datefmt="%Y-%m-%d %H:%M:%S",
stream=sys.stdout,
force=True,
)
log = logging.getLogger("kalshi.bot")

class KalshiQuantBot:
def **init**(self, watchlist, poll_interval_secs=30, dry_run=True,
rules=None, gamma=0.1, glft_Q=50.0):
self.watchlist = watchlist
self.poll_interval = poll_interval_secs
self.dry_run = dry_run
self.client = KalshiClient()
self.analyzer = MarketAnalyzer(self.client)
self.pricer = MicroPricingAgent(gamma=gamma)
self.sizer = GLFTSizer(gamma=gamma, Q=glft_Q)
self.vpin = VPINMonitor(n_buckets=10, threshold=0.65)
self.jumps = JumpDiffusionAgent(jump_threshold_cents=4.0, critical_mass=0.20)
self.entropy = EntropyMonitor(window=10, collapse_sigma=2.0)
self.engine = RuleEngine(rules or DEFAULT_RULES)
self.risk = RiskManager()
self.inventory = {t: 0.0 for t in watchlist}
self.stats = {
"ticks": 0, "trades": 0, "blocked_vpin": 0,
"blocked_jump": 0, "blocked_entropy": 0, "blocked_risk": 0,
}
log.info("=" * 65)
log.info("Kalshi Quant Bot starting")
log.info("Watchlist: " + str(watchlist))
log.info("Dry run: " + str(dry_run))
log.info("=" * 65)

```
def run(self):
    log.info("Bot running every " + str(self.poll_interval) + "s")
    while True:
        try:
            self._tick()
        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break
        except Exception as e:
            log.error("Tick error: " + str(e))
            time.sleep(5)
        time.sleep(self.poll_interval)

def _tick(self):
    self.stats["ticks"] += 1
    balance = self.client.get_balance()
    log.info("Tick " + str(self.stats["ticks"]) + " | Balance: $" + str(round(balance, 2)))
    for ticker in self.watchlist:
        try:
            self._process_market(ticker, balance)
        except Exception as e:
            log.warning("[" + ticker + "] Error: " + str(e))

def _process_market(self, ticker, balance):
    snap = self.analyzer.update(ticker)
    log.info(self.analyzer.summary(ticker))
    q = self.inventory.get(ticker, 0.0)
    t = 0.5
    r = self.pricer.reservational_price(snap, q, t)
    log.info("r=" + (str(round(r, 1)) + "c" if r else "warming up") + " q=" + str(q))
    self.entropy.update(ticker, snap)
    if self.entropy.is_collapsing(ticker):
        log.info("Entropy collapse - skipping")
        self.stats["blocked_entropy"] += 1
        return
    toxic, vpin_val = self.vpin.is_toxic(snap.trade_history)
    if toxic:
        log.info("VPIN=" + str(vpin_val) + " - toxic flow, halting")
        self.stats["blocked_vpin"] += 1
        return
    jump_prob = self.jumps.jump_probability(snap)
    if self.jumps.is_critical(snap):
        log.info("P(jump)=" + str(jump_prob) + " - too risky")
        self.stats["blocked_jump"] += 1
        return
    signal = self.engine.evaluate(snap, r=r, q=q, t=t)
    if signal.action == "hold":
        log.info("HOLD")
        return
    log.info("Signal: " + signal.action + " conf=" + str(signal.confidence) + " " + signal.reason)
    size = self.sizer.position_size(snap, q, balance, signal.confidence)
    raw_price = signal.price_cents or snap.yes_ask
    limit_price = self.jumps.adjust_price(raw_price, signal.side, jump_prob)
    approved, reason = self.risk.approve(signal, snap, vpin_val, jump_prob, q, balance)
    if not approved:
        log.info("Risk check failed: " + reason)
        self.stats["blocked_risk"] += 1
        return
    self._execute(ticker, signal, limit_price, size)

def _execute(self, ticker, signal, price, size):
    if self.dry_run:
        log.info("DRY RUN - BUY " + str(size) + "x " + signal.side.upper() + " @ " + str(price) + "c on " + ticker)
        return
    try:
        if signal.side == "yes":
            result = self.client.place_order(ticker=ticker, side="yes", count=size, yes_price=price)
        else:
            result = self.client.place_order(ticker=ticker, side="no", count=size, no_price=100 - price)
        self.inventory[ticker] += size if signal.side == "yes" else -size
        self.stats["trades"] += 1
        log.info("Order placed: " + str(result))
    except Exception as e:
        log.error("Order failed: " + str(e))
```

if **name** == "**main**":
watchlist_env = os.getenv("KALSHI_WATCHLIST", "")
WATCHLIST = [t.strip() for t in watchlist_env.split(",") if t.strip()]
if not WATCHLIST:
log.error("No markets to watch! Set KALSHI_WATCHLIST in Railway Variables.")
sys.exit(1)
dry_run = os.getenv("DRY_RUN", "true").lower() != "false"
bot = KalshiQuantBot(
watchlist=WATCHLIST,
poll_interval_secs=int(os.getenv("POLL_INTERVAL", "30")),
dry_run=dry_run,
gamma=float(os.getenv("GAMMA", "0.1")),
glft_Q=float(os.getenv("GLFT_Q", "50.0")),
)
bot.run()
