"""config.py — Paramètres centraux du bot"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Mode ──────────────────────────────────────────────────────────────────
TRADING_MODE       = os.getenv("TRADING_MODE", "paper")   # "paper" | "live"
IS_LIVE            = TRADING_MODE == "live"

# ── Capital ───────────────────────────────────────────────────────────────
INITIAL_CAPITAL    = float(os.getenv("INITIAL_CAPITAL", 1000))
MAX_POSITION_PCT   = float(os.getenv("MAX_POSITION_PCT", 0.05))   # 5%
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", 0.10)) # -10% → kill

# ── Stratégie ─────────────────────────────────────────────────────────────
MIN_EDGE_PCT       = float(os.getenv("MIN_EDGE_PCT", 0.12))        # 12% edge min (après frais 2%)
MIN_LIQUIDITY_USD  = float(os.getenv("MIN_LIQUIDITY_USD", 100))
KELLY_FRACTION     = 0.25   # Kelly fractionnel (25% du Kelly complet — plus sûr)

# ── Polymarket credentials ─────────────────────────────────────────────────
POLY_API_KEY           = os.getenv("POLY_API_KEY", "")
POLY_SECRET            = os.getenv("POLY_SECRET", "")
POLY_PASSPHRASE        = os.getenv("POLY_PASSPHRASE", "")
POLY_PRIVATE_KEY       = os.getenv("POLY_PRIVATE_KEY", "")
DEPOSIT_WALLET_ADDRESS = os.getenv("DEPOSIT_WALLET_ADDRESS", "")
POLY_HOST              = "https://clob.polymarket.com"
GAMMA_API              = "https://gamma-api.polymarket.com"

# ── Binance WebSocket ──────────────────────────────────────────────────────
BINANCE_WS_URL     = "wss://stream.binance.com:9443/stream"
BINANCE_SYMBOLS    = ["btcusdt", "ethusdt", "solusdt", "xrpusdt", "bnbusdt", "dogeusdt", "suiusdt"]   # paires suivies
PRICE_WINDOW_SEC   = 30    # fenêtre glissante pour calculer le mouvement

# ── Timeouts / intervals ───────────────────────────────────────────────────
POLY_POLL_SEC      = 2     # fréquence refresh marchés Polymarket (secondes)
POSITION_TTL_SEC   = 86400  # fermer position après 24h (paper simulation)
