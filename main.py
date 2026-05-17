"""
main.py — Polymarket Latency Arbitrage Bot
==========================================

Mode par défaut : PAPER TRADING (aucun argent réel)
Pour passer en live : TRADING_MODE=live dans .env

Stratégie : détecter le lag entre Binance et Polymarket
  1. WebSocket Binance → prix BTC/ETH en <50ms
  2. Calcul probabilité implicite (momentum 10s/30s)
  3. Comparer à prix YES sur marchés Polymarket actifs
  4. Si edge > MIN_EDGE_PCT → Kelly size → ordre

Usage :
  python main.py              # paper trading
  TRADING_MODE=live python main.py  # live (clé privée requise)
"""
from __future__ import annotations

import asyncio
import math
import time
import signal
import sys
import aiohttp

from config import IS_LIVE, INITIAL_CAPITAL, POLY_POLL_SEC, TRADING_MODE
from binance_ws import BinanceFeed
from poly_client import fetch_active_markets, get_clob_client, place_order
from strategy import build_signal, format_signal
from risk import RiskManager


# ── Bot principal ─────────────────────────────────────────────────────────

class ArbitrageBot:

    def __init__(self):
        self.feed    = BinanceFeed()
        self.risk    = RiskManager(INITIAL_CAPITAL)
        self.clob    = None
        self.markets = []
        self._stop   = False
        self._trade_count = 0
        self._traded_ids: set[str] = set()  # évite re-entrée sur même marché

        print(f"\n{'='*60}")
        print(f"  🤖 Polymarket Latency Arbitrage Bot")
        print(f"  Mode     : {'🔴 LIVE (argent réel)' if IS_LIVE else '🟡 PAPER TRADING'}")
        print(f"  Capital  : ${INITIAL_CAPITAL:,.2f}")
        print(f"{'='*60}\n")

        if IS_LIVE and not self._check_live_config():
            print("❌ Config LIVE incomplète. Vérifie .env (POLY_API_KEY, POLY_PRIVATE_KEY).")
            sys.exit(1)

    def _check_live_config(self) -> bool:
        from config import POLY_API_KEY, POLY_PRIVATE_KEY
        return bool(POLY_API_KEY and POLY_PRIVATE_KEY)

    # ── Loop principale ───────────────────────────────────────────────────

    async def run(self):
        # Initialiser CLOB client (live seulement)
        if IS_LIVE:
            self.clob = get_clob_client()
            if self.clob is None:
                print("❌ Impossible d'initialiser le client CLOB.")
                return

        async with aiohttp.ClientSession() as session:
            # Lancer WebSocket Binance en background
            ws_task = asyncio.create_task(self.feed.run())

            # Attendre quelques secondes pour avoir des prix
            print("[Bot] Attente données Binance…")
            await asyncio.sleep(3)

            print("[Bot] ✅ Démarrage du scan Polymarket")
            stats_ts = time.time()

            while not self._stop:
                if self.risk.is_killed:
                    print("[Bot] 🛑 Kill switch actif — arrêt.")
                    break

                try:
                    await self._scan_cycle(session)
                except Exception as e:
                    print(f"[Bot] Erreur cycle: {e}")

                # Stats toutes les 60s
                if time.time() - stats_ts > 60:
                    self.risk.print_stats()
                    stats_ts = time.time()

                await asyncio.sleep(POLY_POLL_SEC)

            ws_task.cancel()
            self.risk.print_stats()
            print("\n[Bot] Arrêté.")

    async def _scan_cycle(self, session: aiohttp.ClientSession):
        """Un cycle de scan : fetch marchés → chercher edges → trader."""
        # Refresh marchés Polymarket toutes les 10 cycles (20s) pour ne pas spammer l'API
        if self._trade_count % 10 == 0:
            self.markets = await fetch_active_markets(session)
            if not self.markets:
                print("[Bot] ⚠️  Aucun marché crypto actif trouvé sur Polymarket (réessai dans 20s)")
                return
            print(f"[Bot] 🔍 {len(self.markets)} marchés actifs trouvés")

        # Pour chaque marché actif
        for market in self.markets:
            sym = market.symbol
            if not sym:
                continue

            # Prix Binance actuel
            binance_price = self.feed.get_price(sym)
            if binance_price is None:
                continue

            # Vérifier si déjà tradé ce marché (ouvert OU fermé)
            if market.id in self._traded_ids:
                continue

            # Filtrer marchés quasi-résolus (prix extrêmes = plus d'edge possible)
            if market.yes_price < 0.04 or market.yes_price > 0.96:
                continue

            # ── Probabilité implicite ──────────────────────────────────────
            # Stratégie 1 : marché avec cible de prix explicite ("above $80k")
            #   → comparer directement le prix Binance à la cible
            # Stratégie 2 : marché momentum ("higher/lower in 5 min")
            #   → utiliser le momentum Binance (sigmoid)

            price_target = market.parse_price_target()

            if price_target is not None:
                target_price, target_dir = price_target
                # Distance relative du prix actuel à la cible
                dist = (binance_price - target_price) / target_price
                # sigmoid(dist * 15) : ±10% → prob ≈ 0.98/0.02, ±3% → 0.80/0.20
                if target_dir == "above":
                    implied_prob = 1.0 / (1.0 + math.exp(-dist * 15))
                else:  # "below"
                    implied_prob = 1.0 / (1.0 + math.exp(dist * 15))

                # Si marché déjà quasi-résolu côté YES (prix poly extrême)
                # → pas besoin du filtre implied_prob, le filtre yes_price 0.04/0.96 suffit
                # On garde seulement le filtre haute probabilité pour éviter d'acheter YES quasi-certain
                if implied_prob > 0.95 and market.yes_price > 0.90:
                    continue
            else:
                # Marché momentum : utiliser le signal Binance (10s/30s)
                direction = "YES"
                if any(kw in market.question.lower() for kw in ["lower", "below", "down", "bearish"]):
                    direction = "NO"
                implied_prob = self.feed.get_implied_probability(sym, direction)

            # Construire signal
            signal = build_signal(
                market_id=market.id,
                question=market.question,
                symbol=sym,
                poly_yes_price=market.yes_price,
                implied_prob=implied_prob,
                capital=self.risk.capital,
            )

            if signal is None:
                continue

            # Vérification risk
            if not self.risk.can_open(signal.kelly_size_usd):
                continue

            # ── Exécution ──────────────────────────────────────────────
            print(f"\n{format_signal(signal)}")

            result = await place_order(
                clob=self.clob,
                market_id=market.id,
                side=signal.side,
                price=signal.poly_price,
                size_usd=signal.kelly_size_usd,
            )

            if result is not None:
                pos = self.risk.open_position(
                    market_id=market.id,
                    question=market.question,
                    symbol=sym,
                    side=signal.side,
                    price=signal.poly_price,
                    size_usd=signal.kelly_size_usd,
                )
                self._traded_ids.add(market.id)  # bloquer re-entrée
                mode_tag = "[PAPER]" if not IS_LIVE else "[LIVE]"
                print(f"  {mode_tag} Ordre ouvert — ${signal.kelly_size_usd:.2f} {signal.side} @ {signal.poly_price:.3f}")
                self._trade_count += 1

        # Nettoyer les positions expirées (paper trading = fermer aléatoirement selon prob)
        await self._cleanup_expired_positions()

    async def _cleanup_expired_positions(self):
        """Fermer les positions expirées. En paper : simuler le résultat."""
        for pos in self.risk.get_open_positions():
            if not pos.is_expired:
                continue
            # Paper trading : simuler win/loss selon la probabilité implicite
            if not IS_LIVE:
                import random
                # Prob de win basée sur l'edge (meilleure prob → plus souvent gagné)
                # Simplification : on gagne avec prob = poly_price (le marché avait raison)
                won = random.random() < (pos.entry_price + 0.05)  # léger avantage edge
                self.risk.close_position(pos, exit_price=1.0 if won else 0.0, won=won)
                result = f"✅ +${pos.pnl:.2f}" if won else f"❌ -${pos.size_usd:.2f}"
                print(f"  [PAPER] Position fermée {result} | {pos.question[:50]}")
            # En live : Polymarket résout automatiquement les marchés
            # Les positions se closent via webhook ou polling des résolutions

    def stop(self):
        self._stop = True


# ── Entrée principale ─────────────────────────────────────────────────────

async def main():
    bot = ArbitrageBot()

    # Graceful shutdown sur Ctrl+C
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, bot.stop)

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
