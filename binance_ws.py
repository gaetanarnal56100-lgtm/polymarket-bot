"""binance_ws.py — REST polling Binance (fallback when WS blocked by GCP US IPs)"""
from __future__ import annotations
import asyncio
import math
import time
from collections import deque
from typing import Callable
import aiohttp
from config import BINANCE_SYMBOLS, PRICE_WINDOW_SEC

BINANCE_REST_URL = "https://api.binance.com/api/v3/ticker/price"
POLL_INTERVAL    = 2.0   # secondes entre chaque fetch REST


class BinanceFeed:
    """
    REST polling Binance — latence ~2s (acceptable pour marchés 5min+).
    Interface identique à la version WebSocket.
    """

    def __init__(self):
        self.prices: dict[str, float]  = {}
        self.history: dict[str, deque] = {s: deque() for s in BINANCE_SYMBOLS}
        self._callbacks: list[Callable] = []
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────

    def on_price(self, fn: Callable):
        self._callbacks.append(fn)

    def get_price(self, symbol: str) -> float | None:
        return self.prices.get(symbol.lower())

    def get_momentum(self, symbol: str, window_sec: int = 10) -> float:
        """% changement prix sur N dernières secondes. Positif = hausse."""
        sym = symbol.lower()
        now = time.time()
        hist = self.history.get(sym)
        if not hist or len(hist) < 2:
            return 0.0
        cutoff = now - window_sec
        old_entry = next(((ts, p) for ts, p in hist if ts >= cutoff), None)
        if old_entry is None:
            old_entry = hist[0]
        current = self.prices.get(sym, 0)
        if old_entry[1] == 0:
            return 0.0
        return (current - old_entry[1]) / old_entry[1] * 100

    def get_implied_probability(self, symbol: str, direction: str) -> float:
        """Probabilité implicite basée sur momentum (sigmoid k=150)."""
        momentum_10s = self.get_momentum(symbol, 10)
        momentum_30s = self.get_momentum(symbol, 30)
        score = momentum_10s * 0.6 + momentum_30s * 0.4
        k = 150
        raw_prob = 1.0 / (1.0 + math.exp(-k * (score / 100)))
        if direction.upper() == "YES":
            return raw_prob
        else:
            return 1.0 - raw_prob

    async def run(self):
        """REST polling loop — remplace WebSocket."""
        self._running = True
        symbols_param = str([s.upper() for s in BINANCE_SYMBOLS]).replace("'", '"').replace(" ", "")
        url = f"{BINANCE_REST_URL}?symbols={symbols_param}"
        print(f"[Binance REST] Démarrage polling {len(BINANCE_SYMBOLS)} symboles toutes les {POLL_INTERVAL}s")

        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            now = time.time()
                            for item in data:
                                sym = item["symbol"].lower()
                                price = float(item["price"])
                                if not price:
                                    continue
                                self.prices[sym] = price
                                hist = self.history.setdefault(sym, deque())
                                hist.append((now, price))
                                cutoff = now - PRICE_WINDOW_SEC
                                while hist and hist[0][0] < cutoff:
                                    hist.popleft()
                                for fn in self._callbacks:
                                    try:
                                        fn(sym, price)
                                    except Exception:
                                        pass
                        else:
                            print(f"[Binance REST] ⚠️  HTTP {resp.status}")
                except Exception as e:
                    print(f"[Binance REST] ⚠️  Erreur ({e}), retry dans 5s…")
                    await asyncio.sleep(5)
                    continue
                await asyncio.sleep(POLL_INTERVAL)

    def stop(self):
        self._running = False
