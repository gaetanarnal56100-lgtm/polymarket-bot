"""risk.py — Gestionnaire de risque + kill switch"""
from __future__ import annotations
import time
from config import MAX_DAILY_LOSS_PCT, INITIAL_CAPITAL, POSITION_TTL_SEC
from dataclasses import dataclass, field


@dataclass
class Position:
    market_id: str
    question: str
    symbol: str
    side: str
    entry_price: float
    size_usd: float
    opened_at: float = field(default_factory=time.time)
    closed: bool = False
    pnl: float = 0.0

    @property
    def age_sec(self) -> float:
        return time.time() - self.opened_at

    @property
    def is_expired(self) -> bool:
        return self.age_sec > POSITION_TTL_SEC


class RiskManager:
    def __init__(self, capital: float = INITIAL_CAPITAL):
        self.capital         = capital
        self.start_capital   = capital
        self.daily_start     = capital
        self.positions: list[Position] = []
        self.trade_log: list[dict]     = []
        self._killed         = False
        self._day_start_ts   = time.time()

    # ── Kill switch ───────────────────────────────────────────────────────

    @property
    def is_killed(self) -> bool:
        return self._killed

    def check_kill_switch(self) -> bool:
        """Vérifie si la perte journalière dépasse le max. Tue le bot si oui."""
        if self._killed:
            return True
        daily_loss_pct = (self.capital - self.daily_start) / self.daily_start
        if daily_loss_pct < -MAX_DAILY_LOSS_PCT:
            print(f"\n🛑 KILL SWITCH — perte journalière {daily_loss_pct*100:.1f}% > max {MAX_DAILY_LOSS_PCT*100:.0f}%")
            print("   Le bot s'arrête. Relance manuel requis.")
            self._killed = True
        return self._killed

    def reset_daily(self):
        """Réinitialiser le compteur journalier (minuit)."""
        self.daily_start = self.capital
        self._day_start_ts = time.time()

    # ── Positions ─────────────────────────────────────────────────────────

    def can_open(self, size_usd: float) -> bool:
        """Vérifie si on peut ouvrir une nouvelle position."""
        if self._killed:
            return False
        open_exposure = sum(p.size_usd for p in self.positions if not p.closed)
        # Max 30% du capital en positions ouvertes simultanément
        return (open_exposure + size_usd) <= self.capital * 0.30

    def open_position(self, market_id: str, question: str, symbol: str,
                      side: str, price: float, size_usd: float) -> Position:
        pos = Position(market_id=market_id, question=question, symbol=symbol,
                       side=side, entry_price=price, size_usd=size_usd)
        self.positions.append(pos)
        self.capital -= size_usd  # capital engagé
        return pos

    def close_position(self, pos: Position, exit_price: float, won: bool):
        """
        Fermer une position.
        won=True  → on a gagné : récupère size_usd / entry_price (payout binaire)
        won=False → on a perdu : size_usd perdu
        """
        if pos.closed:
            return
        if won:
            payout = pos.size_usd / pos.entry_price   # payout binaire $1 par contrat
            pos.pnl = payout - pos.size_usd
            self.capital += payout
        else:
            pos.pnl = -pos.size_usd
            # capital déjà déduit à l'ouverture

        pos.closed = True
        self.check_kill_switch()
        self.trade_log.append({
            "market": pos.market_id,
            "question": pos.question[:60],
            "side": pos.side,
            "entry": pos.entry_price,
            "exit": exit_price,
            "size": pos.size_usd,
            "pnl": round(pos.pnl, 4),
            "won": won,
            "duration_sec": round(pos.age_sec),
        })

    def get_open_positions(self) -> list[Position]:
        return [p for p in self.positions if not p.closed]

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        closed = [t for t in self.trade_log]
        wins   = [t for t in closed if t["won"]]
        losses = [t for t in closed if not t["won"]]
        total_pnl = sum(t["pnl"] for t in closed)
        win_rate  = len(wins) / len(closed) * 100 if closed else 0
        return {
            "capital": round(self.capital, 2),
            "pnl_total": round(total_pnl, 2),
            "pnl_pct": round((self.capital - self.start_capital) / self.start_capital * 100, 2),
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 4) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 4) if losses else 0,
            "open_positions": len(self.get_open_positions()),
        }

    def print_stats(self):
        s = self.stats()
        print(
            f"\n{'='*60}\n"
            f"  Capital : ${s['capital']:,.2f}  (PnL: {s['pnl_pct']:+.2f}%)\n"
            f"  Trades  : {s['trades']} | Win: {s['win_rate']:.0f}% "
            f"({s['wins']}W / {s['losses']}L)\n"
            f"  Avg Win : ${s['avg_win']:+.4f}  |  Avg Loss : ${s['avg_loss']:+.4f}\n"
            f"  Positions ouvertes : {s['open_positions']}\n"
            f"{'='*60}"
        )
