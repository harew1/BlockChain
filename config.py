"""
config.py — Platform-wide configuration, .env loading, constants
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ─── Load .env ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# ─── General ──────────────────────────────────────────────────────────────────
APP_NAME    = "CryptoTrader Pro"
APP_VERSION = "1.0.0"
DEBUG       = os.getenv("DEBUG", "false").lower() == "true"

# ─── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/trading.db")

# ─── JWT ──────────────────────────────────────────────────────────────────────
JWT_SECRET      = os.getenv("JWT_SECRET", "change-me-in-production-secret-key-256bit")
JWT_ALGORITHM   = "HS256"
JWT_EXPIRE_MINS = int(os.getenv("JWT_EXPIRE_MINS", "60"))

# ─── Encryption ───────────────────────────────────────────────────────────────
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")  # Fernet key, 32-byte base64

# ─── Binance ──────────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET    = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
BINANCE_BASE_URL   = (
    "https://testnet.binancefuture.com" if BINANCE_TESTNET
    else "https://fapi.binance.com"
)
BINANCE_WS_URL = (
    "wss://stream.binancefuture.com/ws" if BINANCE_TESTNET
    else "wss://fstream.binance.com/ws"
)

# ─── Bybit ────────────────────────────────────────────────────────────────────
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BYBIT_TESTNET    = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
BYBIT_BASE_URL   = (
    "https://api-testnet.bybit.com" if BYBIT_TESTNET
    else "https://api.bybit.com"
)

# ─── CoinGecko ────────────────────────────────────────────────────────────────
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
COINGECKO_BASE    = "https://api.coingecko.com/api/v3"

# ─── Redis Cache ──────────────────────────────────────────────────────────────
REDIS_URL       = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL_SECS  = int(os.getenv("CACHE_TTL_SECS", "30"))
USE_REDIS       = os.getenv("USE_REDIS", "false").lower() == "true"

# ─── Trading Defaults ─────────────────────────────────────────────────────────
DEFAULT_SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL", "60"))      # seconds
DEFAULT_TOP_N_COINS     = int(os.getenv("TOP_N_COINS", "20"))
DEFAULT_RISK_PERCENT    = float(os.getenv("RISK_PERCENT", "1.0"))    # % of equity
DEFAULT_LEVERAGE        = int(os.getenv("LEVERAGE", "5"))
DEFAULT_STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT", "2.0"))
DEFAULT_TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
DEFAULT_MAX_TRADES      = int(os.getenv("MAX_TRADES", "5"))
DEFAULT_MAX_DAILY_LOSS  = float(os.getenv("MAX_DAILY_LOSS", "5.0"))  # % of equity
SIMULATION_MODE         = os.getenv("SIMULATION_MODE", "true").lower() == "true"
QUOTE_ASSET             = os.getenv("QUOTE_ASSET", "USDT")

# ─── Timeframes ───────────────────────────────────────────────────────────────
TIMEFRAMES = ["1m", "5m", "15m", "1h"]
PRIMARY_TF = "15m"

# ─── Filters ──────────────────────────────────────────────────────────────────
MIN_VOLUME_USDT     = float(os.getenv("MIN_VOLUME_USDT", "5000000"))   # 5M
MIN_LIQUIDITY_SCORE = float(os.getenv("MIN_LIQUIDITY_SCORE", "30.0"))
MIN_VOLATILITY_PCT  = float(os.getenv("MIN_VOLATILITY_PCT", "1.0"))
ARBITRAGE_THRESHOLD = float(os.getenv("ARBITRAGE_THRESHOLD", "0.3"))   # %

# ─── Notifications ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK    = os.getenv("DISCORD_WEBHOOK", "")
EMAIL_HOST         = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT         = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER         = os.getenv("EMAIL_USER", "")
EMAIL_PASS         = os.getenv("EMAIL_PASS", "")
EMAIL_TO           = os.getenv("EMAIL_TO", "")

# ─── Security ─────────────────────────────────────────────────────────────────
API_RATE_LIMIT     = int(os.getenv("API_RATE_LIMIT", "100"))   # req/min
ADMIN_IP_WHITELIST = os.getenv("ADMIN_IP_WHITELIST", "127.0.0.1,::1").split(",")
TOTP_ISSUER        = os.getenv("TOTP_ISSUER", APP_NAME)

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv("LOG_FORMAT", "json")   # json | text
LOG_FILE   = BASE_DIR / "logs" / "platform.log"

# ─── Backend ──────────────────────────────────────────────────────────────────
BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))

# ─── ML Model ─────────────────────────────────────────────────────────────────
MODEL_PATH        = BASE_DIR / "models" / "signal_model.joblib"
SCALER_PATH       = BASE_DIR / "models" / "scaler.joblib"
MIN_TRAIN_SAMPLES = 50

# Ensure directories exist
(BASE_DIR / "logs").mkdir(exist_ok=True)
(BASE_DIR / "models").mkdir(exist_ok=True)
