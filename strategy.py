"""strategy.py — Détection d'edge + Kelly criterion sizing"""
from __future__ import annotations
import math
from dataclasses import dataclass
from config import MIN_EDGE_PCT, KELLY_FRACTION, MAX_POSITION_PCT, INITIAL_CAPITAL

POLY_FEE = 0.02  # 2% sur les profits (frais Polymarket standard)


@dataclass
class TradeSignal:
    market_id: str
    question: str
    symbol: str           # BTCUSDT / ETHUSDT
    side: str             # "YES" | "NO"
    poly_price: float     # prix actuel sur Polymarket (0-1)
    implied_prob: float   # probabilité implicite Binance
    edge: float           # edge en fraction (ex: 0.12 = 12%)
    kelly_size_usd: float # taille position recommandée en USD
    confidence: str       # "HIGH" | "MEDIUM" | "LOW"


def compute_edge(poly_price: float, implied_prob: float) -> tuple[float, str]:
    """
    Calcule l'edge et le côté à jouer.

    Règle :
      - Si implied_prob > poly_price → acheter YES (sous-évalué)
      - Si implied_prob < poly_price → acheter NO (YES surévalué)

    Retourne (edge, side)
    """
    if implied_prob > poly_price:
        edge = implied_prob - poly_price
        side = "YES"
    else:
        edge = poly_price - implied_prob
        side = "NO"
        # Prix du NO = 1 - poly_price
        # On achète NO à (1 - poly_price) avec prob implicite (1 - implied_prob)

    return edge, side


def kelly_size(prob_win: float, price: float, capital: float) -> float:
    """
    Kelly criterion fractionnel avec frais Polymarket (2% sur profit).

    f* = (p*b_net - q) / b_net
    où b_net = (1/price - 1) * (1 - POLY_FEE)  — cote après frais
    """
    if price <= 0 or price >= 1:
        return 0.0

    b_gross = (1.0 / price) - 1.0          # cote brute
    b = b_gross * (1.0 - POLY_FEE)         # cote nette après frais 2%
    q = 1.0 - prob_win
    f_full = (prob_win * b - q) / b

    if f_full <= 0:
        return 0.0  # pas de trade si Kelly négatif

    f_fractional = f_full * KELLY_FRACTION
    size = capital * min(f_fractional, MAX_POSITION_PCT)
    return round(max(size, 0.0), 2)


def build_signal(
    market_id: str,
    question: str,
    symbol: str,
    poly_yes_price: float,
    implied_prob: float,
    capital: float,
) -> TradeSignal | None:
    """
    Construit un signal de trade si l'edge dépasse le minimum.
    Retourne None si pas d'opportunité.
    """
    edge, side = compute_edge(poly_yes_price, implied_prob)

    if edge < MIN_EDGE_PCT:
        return None  # edge insuffisant

    # Prix effectif selon le côté
    trade_price = poly_yes_price if side == "YES" else (1.0 - poly_yes_price)
    win_prob    = implied_prob    if side == "YES" else (1.0 - implied_prob)

    size = kelly_size(win_prob, trade_price, capital)
    if size < 1.0:
        return None  # taille trop petite, skip

    confidence = "HIGH" if edge > 0.15 else "MEDIUM" if edge > 0.10 else "LOW"

    return TradeSignal(
        market_id=market_id,
        question=question,
        symbol=symbol,
        side=side,
        poly_price=trade_price,
        implied_prob=win_prob,
        edge=edge,
        kelly_size_usd=size,
        confidence=confidence,
    )


def format_signal(sig: TradeSignal) -> str:
    """Affichage lisible d'un signal."""
    return (
        f"[SIGNAL] {sig.confidence} edge={sig.edge*100:.1f}% | "
        f"{sig.symbol} {sig.side} @ ${sig.poly_price:.3f} | "
        f"implied={sig.implied_prob*100:.1f}% | "
        f"size=${sig.kelly_size_usd:.2f} | "
        f"{sig.question[:60]}"
    )
