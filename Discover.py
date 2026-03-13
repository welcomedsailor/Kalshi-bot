“””
discover.py  —  Browse open Kalshi markets to find tickers for your watchlist.
No auth required.

Usage:
python discover.py
python discover.py –search “fed”
python discover.py –search “S&P”
python discover.py –limit 200
“””

import argparse
import requests

BASE = “https://api.elections.kalshi.com/trade-api/v2”

def get_markets(limit=200, status=“open”):
r = requests.get(f”{BASE}/markets”, params={“status”: status, “limit”: limit}, timeout=10)
r.raise_for_status()
return r.json().get(“markets”, [])

def display(markets, search=None):
search = search.lower() if search else None
shown  = 0
print(f”\n  {‘TICKER’:<42} {‘BID’:>4} {‘ASK’:>4}  {‘VOL’:>8}  CLOSES      TITLE”)
print(”  “ + “─” * 95)
for m in markets:
title  = m.get(“title”, “”)
ticker = m.get(“ticker”, “”)
if search and search not in title.lower() and search not in ticker.lower():
continue
bid    = m.get(“yes_bid”, “?”)
ask    = m.get(“yes_ask”, “?”)
vol    = m.get(“volume”, 0)
closes = (m.get(“close_time”) or “?”)[:10]
print(f”  {ticker:<42} {str(bid):>4}¢ {str(ask):>4}¢  {vol:>8}  {closes}  {title[:50]}”)
shown += 1
print(f”\n  {shown} markets shown.\n”)

if **name** == “**main**”:
p = argparse.ArgumentParser(description=“Browse open Kalshi markets”)
p.add_argument(”–search”, default=None, help=“Filter by keyword”)
p.add_argument(”–limit”,  default=200, type=int)
args = p.parse_args()

```
print(f"Fetching up to {args.limit} open markets …")
markets = get_markets(limit=args.limit)
display(markets, search=args.search)
print("Copy a ticker into the WATCHLIST in bot.py")
```
