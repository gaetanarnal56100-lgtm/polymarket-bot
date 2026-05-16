"""binance_ws.py — WebSocket Binance <50ms pour BTC/ETH prix en temps réel"""
from __future__ import annotations
import asyncio
import json
import time
from collections import deque
from typing import Callable
import websockets
from config import BINANCE_WS_URL, BINANCE_SYMBOLS, PRICE_WINDOW_SEC


class BinanceFeed:
    """
    WebSocket Binance — latence <50ms.
    Fournit :
      - prix actuel par symbole
      - historique des prix (fenêtre glissante PRICE_WINDOW_SEC)
      - vitesse de mouvement (pct change / seconde)
    """

    def __init__(self):
        self.prices: dict[str, float]           = {}
        self.history: dict[str, deque]          = {s: deque() for s in BINANCE_SYMBOLS}
        self._callbacks: list[Callable]         = []
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────

    def on_price(self, fn: Callable):
        """Enregistrer un callback appelé à chaque mise à jour de prix."""
        self._callbacks.append(fn)

    def get_price(self, symbol: str) -> float | None:
        return self.prices.get(symbol.lower())

    def get_momentum(self, symbol: str, window_sec: int = 10) -> float:
        """
        Retourne le % de changement de prix sur les N dernières secondes.
        Positif = hausse, négatif = baisse.
        """
        sym = symbol.lower()
        now = time.time()
        hist = self.history.get(sym)
        if not hist or len(hist) < 2:
            return 0.0
        # Prix le plus ancien dans la fenêtre
        cutoff = now - window_sec
        old_entry = next(((ts, p) for ts, p in hist if ts >= cutoff), None)
        if old_entry is None:
            old_entry = hist[0]
        current = self.prices.get(sym, 0)
        if old_entry[1] == 0:
            return 0.0
        return (current - old_entry[1]) / old_entry[1] * 100

    def get_implied_probability(self, symbol: str, direction: str) -> float:
        """
        Probabilité implicite que le prix soit PLUS HAUT (ou PLUS BAS)
        dans les 5-15 prochaines minutes, basée sur le momentum actuel.

        Modèle simplifié :
          - Momentum fort positif → prob hausse élevée
          - Utilise une sigmoid sur le momentum pour normaliser en [0,1]
        """
        momentum_10s = self.get_momentum(symbol, 10)
        momentum_30s = self.get_momentum(symbol, 30)

        # Score composite pondéré
        score = momentum_10s * 0.6 + momentum_30s * 0.4

        # Sigmoid : prob = 1 / (1 + exp(-k * score))
        import math
        k = 15  # sensibilité
        raw_prob = 1.0 / (1.0 + math.exp(-k * (score / 100)))

        if direction.upper() == "YES":
            return raw_prob    # prob que ça monte
        else:
            return 1.0 - raw_prob  # prob que ça baisse

    async def run(self):
        """Lancer le WebSocket — tourne en permanence."""
        self._running = True
        streams = "/".join(f"{s}@miniTicker" for s in BINANCE_SYMBOLS)
        url = f"{BINANCE_WS_URL}?streams={streams}"
        print(f"[Binance WS] Connexion à {url}")

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    print("[Binance WS] ✅ Connecté")
                    async for raw in ws:
                        self._handle(raw)
            except Exception as e:
                print(f"[Binance WS] ⚠️  Déconnecté ({e}), reconnexion dans 2s…")
                await asyncio.sleep(2)

    def stop(self):
        self._running = False

    # ── Interne ───────────────────────────────────────────────────────────

    def _handle(self, raw: str):
        try:
            msg = json.loads(raw)
            data = msg.get("data", msg)
            sym = data.get("s", "").lower()
            price = float(data.get("c", 0))  # "c" = close/current price
            if not sym or not price:
                return

            now = time.time()
            self.prices[sym] = price

            # Historique glissant
            hist = self.history.setdefault(sym, deque())
            hist.append((now, price))
            cutoff = now - PRICE_WINDOW_SEC
            while hist and hist[0][0] < cutoff:
                hist.popleft()

            # Callbacks
            for fn in self._callbacks:
                try:
                    fn(sym, price)
                except Exception:
                    pass

        except Exception:
            pass
