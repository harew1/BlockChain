"""
backend.py — FastAPI REST backend.
  Endpoints : /health /register /login /logout /me /settings
              /bot/start /bot/stop /bot/status /bot/emergency
              /trade/history /portfolio /backtest /scan/latest
              /admin/users /admin/logs
  Auth      : JWT Bearer tokens + TOTP 2FA (admin)
  Security  : IP whitelist for admin, rate limiting, bcrypt passwords
"""

import datetime
import secrets
from functools import wraps
from typing import Any, Dict, List, Optional

import bcrypt
import pyotp
from fastapi import (
    Depends, FastAPI, HTTPException, Request, status,
    BackgroundTasks,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from pydantic import BaseModel, EmailStr
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backtest import backtester
from bot import registry as bot_registry
from config import (
    JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_MINS,
    ADMIN_IP_WHITELIST, API_RATE_LIMIT, ENCRYPTION_KEY, APP_VERSION,
)
from logger import get_logger
from models import (
    User, UserSettings, SystemLog, LogLevel,
    Trade, TradeStatus, UserRole, get_db, init_db,
)
from scanner import scanner as market_scanner

log = get_logger("backend")

# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="CryptoTrader Pro API", version=APP_VERSION)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

bearer_scheme = HTTPBearer(auto_error=False)


# ─────────────────────────────────────────────────────────────────────────────
# Encryption helpers
# ─────────────────────────────────────────────────────────────────────────────
def _encrypt(plaintext: str) -> str:
    if not ENCRYPTION_KEY or not plaintext:
        return plaintext
    from cryptography.fernet import Fernet
    f = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)
    return f.encrypt(plaintext.encode()).decode()

def _decrypt(ciphertext: str) -> str:
    if not ENCRYPTION_KEY or not ciphertext:
        return ciphertext
    try:
        from cryptography.fernet import Fernet
        f = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# JWT helpers
# ─────────────────────────────────────────────────────────────────────────────
def _create_token(user_id: int, role: str) -> str:
    exp = datetime.datetime.utcnow() + datetime.timedelta(minutes=JWT_EXPIRE_MINS)
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp},
                      JWT_SECRET, algorithm=JWT_ALGORITHM)

def _decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ─────────────────────────────────────────────────────────────────────────────
# Auth dependencies
# ─────────────────────────────────────────────────────────────────────────────
async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> User:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    try:
        payload  = _decode_token(creds.credentials)
        user_id  = int(payload["sub"])
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    with get_db() as db:
        user = db.query(User).filter_by(id=user_id, is_active=True).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user

async def require_admin(user: User = Depends(get_current_user),
                        request: Request = None) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    # IP whitelist
    if request:
        client_ip = request.client.host if request.client else "unknown"
        if ADMIN_IP_WHITELIST and client_ip not in ADMIN_IP_WHITELIST:
            log.warning("[Security] Admin access denied for IP: %s", client_ip)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "IP not whitelisted")
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────
class RegisterReq(BaseModel):
    username: str
    email: str
    password: str

class LoginReq(BaseModel):
    username: str
    password: str
    totp_code: Optional[str] = None

class SettingsReq(BaseModel):
    trading_enabled:  Optional[bool]  = None
    simulation_mode:  Optional[bool]  = None
    risk_percent:     Optional[float] = None
    stop_loss_pct:    Optional[float] = None
    take_profit_pct:  Optional[float] = None
    leverage:         Optional[int]   = None
    max_trades:       Optional[int]   = None
    max_daily_loss:   Optional[float] = None
    scan_interval:    Optional[int]   = None
    selected_coins:   Optional[List[str]] = None
    notify_telegram:  Optional[bool]  = None
    notify_discord:   Optional[bool]  = None
    notify_email:     Optional[bool]  = None
    binance_api_key:  Optional[str]   = None
    binance_api_secret: Optional[str] = None

class BacktestReq(BaseModel):
    symbol:   str = "BTCUSDT"
    interval: str = "1h"
    limit:    int = 500


# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    log.info("[Backend] Database initialized.")


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION,
            "ts": datetime.datetime.utcnow().isoformat()}

@app.get("/status")
async def platform_status(user: User = Depends(get_current_user)):
    return {
        "bots":    bot_registry.all_status(),
        "version": APP_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/register", status_code=201)
@limiter.limit("5/minute")
async def register(req: RegisterReq, request: Request):
    pw_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    with get_db() as db:
        if db.query(User).filter_by(username=req.username).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Username taken")
        if db.query(User).filter_by(email=req.email).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Email taken")
        user = User(username=req.username, email=req.email, password_hash=pw_hash)
        db.add(user)
        db.flush()
        db.add(UserSettings(user_id=user.id))
        uid = user.id
    return {"user_id": uid, "message": "Registered successfully"}


@app.post("/login")
@limiter.limit("10/minute")
async def login(req: LoginReq, request: Request):
    with get_db() as db:
        user = db.query(User).filter_by(username=req.username, is_active=True).first()
        if not user or not bcrypt.checkpw(req.password.encode(),
                                          user.password_hash.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
        # 2FA (admin)
        if user.totp_secret:
            if not req.totp_code:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "TOTP required")
            totp = pyotp.TOTP(user.totp_secret)
            if not totp.verify(req.totp_code):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid TOTP")
        user.last_login = datetime.datetime.utcnow()
        token = _create_token(user.id, user.role.value)
        return {"access_token": token, "token_type": "bearer"}


@app.get("/me")
async def me(user: User = Depends(get_current_user)):
    with get_db() as db:
        u = db.query(User).filter_by(id=user.id).first()
        s = u.settings if u else None
        return {
            "id":       u.id,
            "username": u.username,
            "email":    u.email,
            "role":     u.role.value,
            "settings": {
                "trading_enabled":  s.trading_enabled if s else False,
                "simulation_mode":  s.simulation_mode if s else True,
                "risk_percent":     s.risk_percent if s else 1.0,
                "stop_loss_pct":    s.stop_loss_pct if s else 2.0,
                "take_profit_pct":  s.take_profit_pct if s else 4.0,
                "leverage":         s.leverage if s else 5,
                "max_trades":       s.max_trades if s else 5,
                "scan_interval":    s.scan_interval if s else 60,
            } if s else {},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────
@app.put("/settings")
async def update_settings(req: SettingsReq, user: User = Depends(get_current_user)):
    with get_db() as db:
        s = db.query(UserSettings).filter_by(user_id=user.id).first()
        if not s:
            s = UserSettings(user_id=user.id)
            db.add(s)
        for field_name, value in req.dict(exclude_none=True).items():
            if field_name == "binance_api_key":
                u = db.query(User).filter_by(id=user.id).first()
                u.binance_api_key_enc = _encrypt(value)
            elif field_name == "binance_api_secret":
                u = db.query(User).filter_by(id=user.id).first()
                u.binance_api_secret_enc = _encrypt(value)
            elif hasattr(s, field_name):
                setattr(s, field_name, value)
    return {"message": "Settings updated"}


# ─────────────────────────────────────────────────────────────────────────────
# Bot control
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/bot/start")
async def bot_start(background: BackgroundTasks,
                    user: User = Depends(get_current_user)):
    with get_db() as db:
        settings = db.query(UserSettings).filter_by(user_id=user.id).first()
        if not settings:
            raise HTTPException(400, "Settings not configured")
        settings_copy = UserSettings(
            trading_enabled=settings.trading_enabled,
            simulation_mode=settings.simulation_mode,
            risk_percent=settings.risk_percent,
            stop_loss_pct=settings.stop_loss_pct,
            take_profit_pct=settings.take_profit_pct,
            leverage=settings.leverage,
            max_trades=settings.max_trades,
            max_daily_loss=settings.max_daily_loss,
            scan_interval=settings.scan_interval,
            notify_telegram=settings.notify_telegram,
            notify_discord=settings.notify_discord,
            notify_email=settings.notify_email,
        )
    bot = bot_registry.get(user.id) or bot_registry.create(user.id, settings_copy)
    background.add_task(bot.start)
    return {"message": "Bot started"}

@app.post("/bot/stop")
async def bot_stop(user: User = Depends(get_current_user)):
    bot_registry.stop(user.id)
    return {"message": "Bot stopped"}

@app.get("/bot/status")
async def bot_status(user: User = Depends(get_current_user)):
    bot = bot_registry.get(user.id)
    if not bot:
        return {"running": False}
    return bot.status()

@app.post("/bot/emergency")
async def bot_emergency(user: User = Depends(get_current_user)):
    bot = bot_registry.get(user.id)
    if bot:
        bot.emergency_stop()
    return {"message": "Emergency stop triggered"}


# ─────────────────────────────────────────────────────────────────────────────
# Trades & Portfolio
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/trade/history")
async def trade_history(limit: int = 50, user: User = Depends(get_current_user)):
    bot = bot_registry.get(user.id)
    if bot:
        return bot.engine.trade_history(limit)
    with get_db() as db:
        trades = db.query(Trade).filter_by(user_id=user.id)\
                   .order_by(Trade.entry_time.desc()).limit(limit).all()
        return [{"id": t.id, "symbol": t.symbol, "signal": t.signal.value,
                 "status": t.status.value, "pnl": t.pnl,
                 "entry_time": t.entry_time.isoformat() if t.entry_time else None}
                for t in trades]

@app.get("/portfolio")
async def portfolio(user: User = Depends(get_current_user)):
    bot = bot_registry.get(user.id)
    if bot:
        return await bot.engine.portfolio_snapshot()
    return {"equity": 0, "open_positions": [], "message": "Bot not running"}


# ─────────────────────────────────────────────────────────────────────────────
# Market scan
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/scan/latest")
async def scan_latest(user: User = Depends(get_current_user)):
    profiles = market_scanner.last_profiles
    return [p.to_dict() for p in profiles[:20]]

@app.post("/scan/run")
async def scan_run(background: BackgroundTasks,
                   user: User = Depends(get_current_user)):
    background.add_task(market_scanner.scan)
    return {"message": "Scan started in background"}


# ─────────────────────────────────────────────────────────────────────────────
# Backtest
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/backtest")
async def run_backtest(req: BacktestReq, user: User = Depends(get_current_user)):
    try:
        result = await backtester.run_async(req.symbol, req.interval, req.limit)
        return backtester.result_to_dict(result)
    except Exception as exc:
        log.error("[Backend] Backtest error: %s", exc)
        raise HTTPException(500, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 2FA setup (admin)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/2fa/setup")
async def setup_2fa(user: User = Depends(require_admin)):
    secret = pyotp.random_base32()
    with get_db() as db:
        u = db.query(User).filter_by(id=user.id).first()
        u.totp_secret = secret
    totp = pyotp.TOTP(secret)
    return {
        "secret":       secret,
        "otpauth_url":  totp.provisioning_uri(user.email, issuer_name="CryptoTrader"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Admin panel
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/admin/users")
async def admin_users(request: Request, admin: User = Depends(require_admin)):
    with get_db() as db:
        users = db.query(User).all()
        return [{
            "id":       u.id, "username": u.username, "email": u.email,
            "role":     u.role.value, "is_active": u.is_active,
            "created":  u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        } for u in users]

@app.put("/admin/users/{user_id}/activate")
async def admin_toggle_user(user_id: int, active: bool,
                             request: Request,
                             admin: User = Depends(require_admin)):
    with get_db() as db:
        u = db.query(User).filter_by(id=user_id).first()
        if not u:
            raise HTTPException(404, "User not found")
        u.is_active = active
    return {"message": f"User {'activated' if active else 'deactivated'}"}

@app.get("/admin/logs")
async def admin_logs(limit: int = 100, request: Request = None,
                     admin: User = Depends(require_admin)):
    with get_db() as db:
        logs_q = db.query(SystemLog).order_by(SystemLog.created_at.desc()).limit(limit).all()
        return [{
            "id":      l.id, "level": l.level.value, "module": l.module,
            "message": l.message, "ts": l.created_at.isoformat(),
        } for l in logs_q]

@app.get("/admin/bots")
async def admin_bots(request: Request, admin: User = Depends(require_admin)):
    return bot_registry.all_status()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    from config import BACKEND_HOST, BACKEND_PORT
    uvicorn.run("backend:app", host=BACKEND_HOST, port=BACKEND_PORT, reload=True)
