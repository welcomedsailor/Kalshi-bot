“””
kalshi_client.py
Supports reading private key from file OR from environment variable
(KALSHI_PRIVATE_KEY_CONTENT) for Railway deployment.
“””

import os
import tempfile
import logging
import kalshi_python

log = logging.getLogger(“kalshi.client”)

PROD_HOST = “https://api.elections.kalshi.com/trade-api/v2”
DEMO_HOST = “https://demo-api.kalshi.co/trade-api/v2”

def _get_private_key_pem() -> str:
# Railway: paste key content as env var
key_content = os.getenv(“KALSHI_PRIVATE_KEY_CONTENT”, “”).strip()
if key_content:
log.info(“Private key loaded from KALSHI_PRIVATE_KEY_CONTENT”)
return key_content
# Local: read from file
key_path = os.getenv(“KALSHI_PRIVATE_KEY_PATH”, “kalshi.key”)
if not os.path.exists(key_path):
raise FileNotFoundError(
f”Private key not found at ‘{key_path}’.\n”
“For Railway: set KALSHI_PRIVATE_KEY_CONTENT in Variables.\n”
“For local: set KALSHI_PRIVATE_KEY_PATH to your .pem file.”
)
log.info(f”Private key loaded from {key_path}”)
return open(key_path).read()

class KalshiClient:
def **init**(self):
env  = os.getenv(“KALSHI_ENV”, “demo”).lower()
host = PROD_HOST if env == “prod” else DEMO_HOST

```
    cfg = kalshi_python.Configuration(host=host)
    cfg.private_key_pem = _get_private_key_pem()
    cfg.api_key_id      = os.getenv("KALSHI_API_KEY_ID", "")

    self._api          = kalshi_python.KalshiClient(cfg)
    self.markets_api   = kalshi_python.MarketsApi(self._api)
    self.portfolio_api = kalshi_python.PortfolioApi(self._api)
    log.info(f"KalshiClient ready | env={env}")

def get_balance(self) -> float:
    return self.portfolio_api.get_balance().balance / 100

def get_positions(self) -> list:
    return self.portfolio_api.get_positions().market_positions or []

def get_fills(self, ticker=None, limit=100) -> list:
    return self.portfolio_api.get_fills(ticker=ticker, limit=limit).fills or []

def get_markets(self, status="open", limit=100, **kwargs) -> list:
    return self.markets_api.get_markets(status=status, limit=limit, **kwargs).markets or []

def get_market(self, ticker: str):
    return self.markets_api.get_market(ticker).market

def get_orderbook(self, ticker: str, depth=10):
    return self.markets_api.get_market_order_book(ticker, depth=depth).orderbook

def get_trades(self, ticker: str, limit=100) -> list:
    return self.markets_api.get_trades(ticker=ticker, limit=limit).trades or []

def place_order(self, ticker, side, count, yes_price=None, no_price=None, order_type="limit"):
    body = kalshi_python.CreateOrderRequest(
        ticker=ticker, action="buy", side=side, type=order_type,
        count=count, yes_price=yes_price, no_price=no_price,
    )
    return self.portfolio_api.create_order(body)

def cancel_order(self, order_id: str):
    return self.portfolio_api.cancel_order(order_id)

def get_open_orders(self) -> list:
    return self.portfolio_api.get_orders(status="resting").orders or []
```
