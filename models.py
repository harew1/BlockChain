"""
models.py — SQLAlchemy ORM models + DB init
Tables: users, settings, trades, logs, alerts
"""

import enum
import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, Enum, ForeignKey, JSON
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager

from config import DATABASE_URL

# ─── Engine ───────────────────────────────────────────────────────────────────
_connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
_pool_args    = {"poolclass": StaticPool}    if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, **_pool_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# ─── Context manager ──────────────────────────────────────────────────────────
@contextmanager
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ─── Enums ────────────────────────────────────────────────────────────────────
class UserRole(str, enum.Enum):
    admin = "admin"
    user  = "user"

class TradeSignal(str, enum.Enum):
    LONG  = "LONG"
    SHORT = "SHORT"
    WAIT  = "WAIT"

class TradeStatus(str, enum.Enum):
    open   = "open"
    closed = "closed"
    failed = "failed"

class LogLevel(str, enum.Enum):
    INFO    = "INFO"
    WARNING = "WARNING"
    ERROR   = "ERROR"
    TRADE   = "TRADE"
    SYSTEM  = "SYSTEM"


# ─── Models ───────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(50), unique=True, nullable=False, index=True)
    email         = Column(String(120), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    role          = Column(Enum(UserRole), default=UserRole.user, nullable=False)
    totp_secret   = Column(String(64), nullable=True)      # 2FA
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=datetime.datetime.utcnow)
    last_login    = Column(DateTime, nullable=True)

    # Encrypted exchange keys
    binance_api_key_enc    = Column(Text, nullable=True)
    binance_api_secret_enc = Column(Text, nullable=True)
    bybit_api_key_enc      = Column(Text, nullable=True)
    bybit_api_secret_enc   = Column(Text, nullable=True)

    settings = relationship("UserSettings", back_populates="user", uselist=False,
                            cascade="all, delete-orphan")
    trades   = relationship("Trade", back_populates="user", cascade="all, delete-orphan")
    logs     = relationship("SystemLog", back_populates="user", cascade="all, delete-orphan")


class UserSettings(Base):
    __tablename__ = "settings"

    id                = Column(Integer, primary_key=True)
    user_id           = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    trading_enabled   = Column(Boolean, default=False)
    simulation_mode   = Column(Boolean, default=True)
    risk_percent      = Column(Float, default=1.0)
    stop_loss_pct     = Column(Float, default=2.0)
    take_profit_pct   = Column(Float, default=4.0)
    leverage          = Column(Integer, default=5)
    max_trades        = Column(Integer, default=5)
    max_daily_loss    = Column(Float, default=5.0)
    scan_interval     = Column(Integer, default=60)
    selected_coins    = Column(JSON, default=list)
    exchange          = Column(String(20), default="binance")
    notify_telegram   = Column(Boolean, default=False)
    notify_discord    = Column(Boolean, default=False)
    notify_email      = Column(Boolean, default=False)
    updated_at        = Column(DateTime, default=datetime.datetime.utcnow,
                               onupdate=datetime.datetime.utcnow)

    user = relationship("User", back_populates="settings")


class Trade(Base):
    __tablename__ = "trades"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    symbol         = Column(String(20), nullable=False)
    signal         = Column(Enum(TradeSignal), nullable=False)
    status         = Column(Enum(TradeStatus), default=TradeStatus.open)
    simulation     = Column(Boolean, default=True)
    exchange       = Column(String(20), default="binance")

    entry_price    = Column(Float, nullable=False)
    exit_price     = Column(Float, nullable=True)
    quantity       = Column(Float, nullable=False)
    leverage       = Column(Integer, default=1)
    stop_loss      = Column(Float, nullable=True)
    take_profit    = Column(Float, nullable=True)
    trailing_stop  = Column(Float, nullable=True)

    pnl            = Column(Float, nullable=True)
    pnl_pct        = Column(Float, nullable=True)
    roi            = Column(Float, nullable=True)

    ai_score       = Column(Float, nullable=True)
    ai_features    = Column(JSON, nullable=True)

    entry_time     = Column(DateTime, default=datetime.datetime.utcnow)
    exit_time      = Column(DateTime, nullable=True)
    notes          = Column(Text, nullable=True)

    user = relationship("User", back_populates="trades")


class SystemLog(Base):
    __tablename__ = "logs"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    level      = Column(Enum(LogLevel), default=LogLevel.INFO)
    module     = Column(String(50), default="system")
    message    = Column(Text, nullable=False)
    extra      = Column(JSON, nullable=True)   # structured extra fields
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    user = relationship("User", back_populates="logs")


class AlertHistory(Base):
    """Keeps track of sent notifications for deduplication & history view."""
    __tablename__ = "alerts"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    channel    = Column(String(20))   # telegram | discord | email
    message    = Column(Text)
    sent       = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ─── Init ─────────────────────────────────────────────────────────────────────
def init_db() -> None:
    """Create all tables and seed default admin user if not present."""
    Base.metadata.create_all(bind=engine)

    import bcrypt
    from config import DEFAULT_RISK_PERCENT, DEFAULT_LEVERAGE, DEFAULT_STOP_LOSS_PCT
    from config import DEFAULT_TAKE_PROFIT_PCT, DEFAULT_MAX_TRADES, DEFAULT_MAX_DAILY_LOSS

    with get_db() as db:
        if not db.query(User).filter_by(username="admin").first():
            pw = bcrypt.hashpw(b"admin1234", bcrypt.gensalt()).decode()
            admin = User(
                username="admin",
                email="admin@cryptotrader.local",
                password_hash=pw,
                role=UserRole.admin,
                is_active=True,
            )
            db.add(admin)
            db.flush()
            db.add(UserSettings(
                user_id=admin.id,
                risk_percent=DEFAULT_RISK_PERCENT,
                leverage=DEFAULT_LEVERAGE,
                stop_loss_pct=DEFAULT_STOP_LOSS_PCT,
                take_profit_pct=DEFAULT_TAKE_PROFIT_PCT,
                max_trades=DEFAULT_MAX_TRADES,
                max_daily_loss=DEFAULT_MAX_DAILY_LOSS,
            ))
            print("[DB] Default admin user created (admin / admin1234)")


if __name__ == "__main__":
    init_db()
    print("[DB] Tables initialized.")
