"""poly_client.py — Polymarket CLOB + Gamma API wrapper"""
from __future__ import annotations
import asyncio
import aiohttp
from typing import Any
from config import (
    POLY_HOST, GAMMA_API,
    POLY_API_KEY, POLY_SECRET, POLY_PASSPHRASE, POLY_PRIVATE_KEY,
    DEPOSIT_WALLET_ADDRESS, MIN_LIQUIDITY_USD, IS_LIVE,
)


# ── Structures ────────────────────────────────────────────────────────────

class PolyMarket:
    def __init__(self, data: dict):
        self.id: str         = data.get("id", "")
        self.question: str   = data.get("question", "")
        self.volume: float   = float(data.get("volume", 0) or 0)
        self.liquidity: float= float(data.get("liquidity", 0) or 0)
        self.end_date: str   = data.get("endDate", "")
        # YES price from outcomePrices
        import json as _json
        try:
            prices = _json.loads(data.get("outcomePrices", "[]"))
            self.yes_price = float(prices[0]) if prices else 0.5
        except Exception:
            self.yes_price = 0.5
        self.active: bool = data.get("active", False)

    @property
    def symbol(self) -> str:
        q = self.question.upper()
        if "SOLANA" in q or " SOL " in q or "SOL)" in q:
            return "solusdt"
        if "ETHEREUM" in q or " ETH " in q or "ETH)" in q:
            return "ethusdt"
        if "BITCOIN" in q or " BTC " in q or "BTC)" in q:
            return "btcusdt"
        if " XRP" in q or "RIPPLE" in q:
            return "xrpusdt"
        if " BNB" in q or "BINANCE COIN" in q:
            return "bnbusdt"
        if "DOGE" in q or "DOGECOIN" in q:
            return "dogeusdt"
        if " SUI" in q:
            return "suiusdt"
        return ""

    def parse_price_target(self) -> tuple[float, str] | None:
        """
        Extrait le prix cible et la direction depuis la question.
        Ex: "Will BTC be above $80,000 on May 16?" → (80000.0, "above")
        Ex: "Will ETH be below $2,500 by end of week?" → (2500.0, "below")
        Retourne None si pas de cible détectable.
        """
        import re
        q = self.question
        # Cherche patterns: above/below/higher/lower/over/under $X,XXX
        patterns = [
            (r'(?:above|over|higher than|greater than|exceed)\s*\$?([\d,]+(?:\.\d+)?)', "above"),
            (r'(?:below|under|lower than|less than|dip to|dip below|drop to|fall to)\s*\$?([\d,]+(?:\.\d+)?)', "below"),
            (r'hit\s+\((?:HIGH|LOW)\)\s*\$?([\d,]+(?:\.\d+)?)', "above"),  # "hit (HIGH) $225"
        ]
        for pattern, direction in patterns:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                try:
                    price = float(m.group(1).replace(",", ""))
                    return (price, direction)
                except ValueError:
                    pass
        return None

    def __repr__(self):
        return f"<Market {self.id[:8]}… yes={self.yes_price:.3f} liq=${self.liquidity:.0f} '{self.question[:50]}'>"


# ── Gamma API (lecture marchés) ────────────────────────────────────────────

async def _fetch_markets_raw(session: aiohttp.ClientSession, order: str, ascending: str) -> list[dict]:
    params = {
        "active": "true",
        "closed": "false",
        "limit": "100",
        "order": order,
        "ascending": ascending,
    }
    try:
        async with session.get(f"{GAMMA_API}/markets", params=params, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if not r.ok:
                return []
            return await r.json()
    except Exception as e:
        print(f"[PolyClient] fetch error ({order}): {e}")
        return []


async def _fetch_updown_markets(session: aiohttp.ClientSession, keyword: str) -> list[dict]:
    """Fetch des marchés 'Up or Down' 5-15 min spécifiquement."""
    params = {
        "active": "true",
        "closed": "false",
        "limit": "100",
        "order": "endDate",
        "ascending": "true",   # les plus proches d'expirer d'abord
        "tag_slug": "crypto",
    }
    try:
        async with session.get(f"{GAMMA_API}/markets", params=params, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if not r.ok:
                return []
            data = await r.json()
            if not isinstance(data, list):
                return []
            # Filtre local sur le keyword
            return [m for m in data if keyword.lower() in m.get("question", "").lower()]
    except Exception as e:
        print(f"[PolyClient] fetch updown error: {e}")
        return []


async def _fetch_daily_series_markets(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch les marchés daily Up or Down BTC/ETH via les séries Polymarket."""
    series_slugs = [
        "btc-up-or-down-daily",
        "eth-up-or-down-daily",
    ]
    results = []
    for slug in series_slugs:
        try:
            params = {"active": "true", "closed": "false", "limit": "5", "order": "endDate", "ascending": "false"}
            async with session.get(
                f"{GAMMA_API}/events",
                params={**params, "seriesSlug": slug},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if not r.ok:
                    continue
                events = await r.json()
                if not isinstance(events, list):
                    continue
                for event in events:
                    for m in event.get("markets", []):
                        if m.get("active") and not m.get("closed"):
                            results.append(m)
        except Exception as e:
            print(f"[PolyClient] fetch daily series error ({slug}): {e}")
    return results


async def _fetch_5m_markets(session: aiohttp.ClientSession) -> list[dict]:
    """
    Fetch les marchés rolling 5-min (BTC/ETH/SOL/XRP Up or Down).
    Ces marchés sont `restricted: true` → invisibles dans les listings normaux.
    Mais fetchables par slug direct : {sym}-updown-5m-{unix_end_timestamp}.
    On génère les timestamps des 3 prochaines fenêtres de 5 min.
    """
    import time as _time
    now = int(_time.time())
    # Slot courant (arrondi au 5 min inférieur) + 3 prochains
    base_slot = (now // 300) * 300
    symbols = ["btc", "eth", "sol", "xrp"]

    # Fetch en parallèle
    slugs_to_fetch = []
    for offset in range(4):   # fenêtre actuelle + 3 suivantes
        slot = base_slot + offset * 300
        for sym in symbols:
            slugs_to_fetch.append(f"{sym}-updown-5m-{slot}")

    async def _fetch_one(slug: str) -> list[dict]:
        try:
            async with session.get(
                f"{GAMMA_API}/events",
                params={"slug": slug},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if not r.ok:
                    return []
                events = await r.json()
                if not isinstance(events, list):
                    return []
                out = []
                for event in events:
                    for m in event.get("markets", []):
                        if m.get("active") and not m.get("closed"):
                            out.append(m)
                return out
        except Exception:
            return []

    batches = await asyncio.gather(*[_fetch_one(s) for s in slugs_to_fetch])
    results = []
    seen: set[str] = set()
    for batch in batches:
        for m in batch:
            mid = m.get("id", "")
            if mid and mid not in seen:
                seen.add(mid)
                results.append(m)
    if results:
        print(f"[PolyClient] 5m markets trouvés : {len(results)}")
    return results


async def fetch_active_markets(session: aiohttp.ClientSession) -> list[PolyMarket]:
    """
    Récupère les marchés crypto actifs avec liquidité suffisante.
    Triple fetch : top volume + top liquidité + marchés Up/Down 5-15 min.
    """
    results = await asyncio.gather(
        _fetch_markets_raw(session, "volume", "false"),
        _fetch_markets_raw(session, "liquidity", "false"),
        _fetch_updown_markets(session, "up or down"),
        _fetch_daily_series_markets(session),
        _fetch_5m_markets(session),
    )
    # Dédupliquer par id
    seen: set[str] = set()
    data: list[dict] = []
    for batch in results:
        for raw in batch:
            rid = raw.get("id", "")
            if rid and rid not in seen:
                seen.add(rid)
                data.append(raw)

    markets = []
    for raw in data:
        m = PolyMarket(raw)
        if not m.symbol:
            continue
        if m.liquidity < MIN_LIQUIDITY_USD:
            continue
        # Filtre marchés prix (questions avec cible de prix)
        q = m.question.lower()
        price_keywords = [
            "above", "below", "higher", "lower", "dip to", "reach",
            "hit (", "exceed", "minute", "15 min", "5 min",
            "price of", "settle at", "between $", "dip below", "rise above",
            "up or down",  # marchés momentum 5-15 min
        ]
        if any(kw in q for kw in price_keywords):
            markets.append(m)

    return markets


# ── CLOB client (exécution ordres) ────────────────────────────────────────

def get_clob_client():
    """
    Retourne le client CLOB py-clob-client-v2.
    Seulement utilisé en mode LIVE.
    signature_type=3 (POLY_1271) → pour nouveaux utilisateurs API avec deposit wallet.
    """
    if not IS_LIVE:
        return None
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        creds = ApiCreds(
            api_key=POLY_API_KEY,
            api_secret=POLY_SECRET,
            api_passphrase=POLY_PASSPHRASE,
        )
        client = ClobClient(
            host=POLY_HOST,
            chain_id=137,
            key=POLY_PRIVATE_KEY,
            creds=creds,
            signature_type=3,       # POLY_1271 — nouveaux utilisateurs API
            funder=DEPOSIT_WALLET_ADDRESS,
        )
        return client
    except ImportError:
        print("[PolyClient] ⚠️  py-clob-client non installé. Lance : pip install py-clob-client")
        return None
    except Exception as e:
        print(f"[PolyClient] CLOB init error: {e}")
        return None


async def place_order(
    clob,
    market_id: str,
    side: str,        # "YES" | "NO"
    price: float,     # 0–1
    size_usd: float,
) -> dict | None:
    """
    Place un ordre market/limit sur Polymarket CLOB.
    side = "YES" → acheter le token YES
    side = "NO"  → acheter le token NO

    Retourne le résultat de l'ordre ou None si erreur.
    """
    if not IS_LIVE or clob is None:
        return {"paper": True, "market_id": market_id, "side": side,
                "price": price, "size_usd": size_usd}

    try:
        from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY

        # Quantité = size_usd / price
        quantity = round(size_usd / price, 2)

        order_args = OrderArgs(
            token_id=market_id,
            price=price,
            size=quantity,
            side=BUY,
        )
        options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: clob.create_and_post_order(order_args, options)
        )
        return result

    except Exception as e:
        print(f"[PolyClient] place_order error: {e}")
        return None


async def get_balance(clob) -> float:
    """Solde USDC disponible sur le compte Polymarket."""
    if not IS_LIVE or clob is None:
        return 0.0
    try:
        loop = asyncio.get_event_loop()
        balance = await loop.run_in_executor(None, clob.get_balance)
        return float(balance)
    except Exception:
        return 0.0
