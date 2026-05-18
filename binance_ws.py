"""binance_ws.py — CoinGecko REST polling (aucune restriction géographique)"""
from __future__ import annotations
import asyncio
import math
import time
from collections import deque
from typing import Callable
import aiohttp
from config import BINANCE_SYMBOLS, PRICE_WINDOW_SEC

# CoinGecko public API — fonctionne depuis toutes les régions GCP
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
POLL_INTERVAL = 4.0   # 4s = 15 req/min (limite free = 30/min)

# Mapping symbol Binance → CoinGecko ID
SYMBOL_TO_CG = {
    "btcusdt":  "bitcoin",
    "ethusdt":  "ethereum",
    "solusdt":  "solana",
    "xrpusdt":  "ripple",
    "bnbusdt":  "binancecoin",
    "dogeusdt": "dogecoin",
    "suiusdt":  "sui",
}
CG_TO_SYMBOL = {v: k for k, v in SYMBOL_TO_CG.items()}
CG_IDS = ",".join(SYMBOL_TO_CG[s] for s in BINANCE_SYMBOLS if s in SYMBOL_TO_CG)


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
        """CoinGecko polling — fonctionne depuis toutes régions GCP."""
        self._running = True
        params = {"ids": CG_IDS, "vs_currencies": "usd"}
        print(f"[Price Feed] CoinGecko polling {len(SYMBOL_TO_CG)} symboles toutes les {POLL_INTERVAL}s")

        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    async with session.get(COINGECKO_URL, params=params,
                                           timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            now = time.time()
                            for cg_id, values in data.items():
                                sym = CG_TO_SYMBOL.get(cg_id)
                                if not sym:
                                    continue
                                price = float(values.get("usd", 0))
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
                            if self.prices:
                                btc = self.prices.get("btcusdt", 0)
                                eth = self.prices.get("ethusdt", 0)
                                print(f"[Price Feed] ✅ BTC={btc:.0f} ETH={eth:.0f}")
                        elif resp.status == 429:
                            print("[Price Feed] ⚠️  Rate limit, attente 30s…")
                            await asyncio.sleep(30)
                        else:
                            print(f"[Price Feed] ⚠️  HTTP {resp.status}")
                except Exception as e:
                    print(f"[Price Feed] ⚠️  Erreur ({e}), retry dans 10s…")
                    await asyncio.sleep(10)
                    continue
                await asyncio.sleep(POLL_INTERVAL)

    def stop(self):
        self._running = False
