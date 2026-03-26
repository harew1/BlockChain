"""Initial migration — create all tables.

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # users
    op.create_table(
        "users",
        sa.Column("id",            sa.Integer,     primary_key=True),
        sa.Column("username",      sa.String(50),  unique=True, nullable=False),
        sa.Column("email",         sa.String(120), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role",          sa.String(10),  default="user"),
        sa.Column("totp_secret",   sa.String(64),  nullable=True),
        sa.Column("is_active",     sa.Boolean,     default=True),
        sa.Column("created_at",    sa.DateTime),
        sa.Column("last_login",    sa.DateTime,    nullable=True),
        sa.Column("binance_api_key_enc",    sa.Text, nullable=True),
        sa.Column("binance_api_secret_enc", sa.Text, nullable=True),
        sa.Column("bybit_api_key_enc",      sa.Text, nullable=True),
        sa.Column("bybit_api_secret_enc",   sa.Text, nullable=True),
    )
    # settings
    op.create_table(
        "settings",
        sa.Column("id",              sa.Integer, primary_key=True),
        sa.Column("user_id",         sa.Integer, sa.ForeignKey("users.id"), unique=True),
        sa.Column("trading_enabled", sa.Boolean, default=False),
        sa.Column("simulation_mode", sa.Boolean, default=True),
        sa.Column("risk_percent",    sa.Float,   default=1.0),
        sa.Column("stop_loss_pct",   sa.Float,   default=2.0),
        sa.Column("take_profit_pct", sa.Float,   default=4.0),
        sa.Column("leverage",        sa.Integer, default=5),
        sa.Column("max_trades",      sa.Integer, default=5),
        sa.Column("max_daily_loss",  sa.Float,   default=5.0),
        sa.Column("scan_interval",   sa.Integer, default=60),
        sa.Column("selected_coins",  sa.JSON),
        sa.Column("exchange",        sa.String(20), default="binance"),
        sa.Column("notify_telegram", sa.Boolean, default=False),
        sa.Column("notify_discord",  sa.Boolean, default=False),
        sa.Column("notify_email",    sa.Boolean, default=False),
        sa.Column("updated_at",      sa.DateTime),
    )
    # trades
    op.create_table(
        "trades",
        sa.Column("id",            sa.Integer, primary_key=True),
        sa.Column("user_id",       sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("symbol",        sa.String(20)),
        sa.Column("signal",        sa.String(10)),
        sa.Column("status",        sa.String(10), default="open"),
        sa.Column("simulation",    sa.Boolean, default=True),
        sa.Column("exchange",      sa.String(20), default="binance"),
        sa.Column("entry_price",   sa.Float),
        sa.Column("exit_price",    sa.Float, nullable=True),
        sa.Column("quantity",      sa.Float),
        sa.Column("leverage",      sa.Integer, default=1),
        sa.Column("stop_loss",     sa.Float, nullable=True),
        sa.Column("take_profit",   sa.Float, nullable=True),
        sa.Column("trailing_stop", sa.Float, nullable=True),
        sa.Column("pnl",           sa.Float, nullable=True),
        sa.Column("pnl_pct",       sa.Float, nullable=True),
        sa.Column("roi",           sa.Float, nullable=True),
        sa.Column("ai_score",      sa.Float, nullable=True),
        sa.Column("ai_features",   sa.JSON, nullable=True),
        sa.Column("entry_time",    sa.DateTime),
        sa.Column("exit_time",     sa.DateTime, nullable=True),
        sa.Column("notes",         sa.Text, nullable=True),
    )
    # logs
    op.create_table(
        "logs",
        sa.Column("id",         sa.Integer, primary_key=True),
        sa.Column("user_id",    sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("level",      sa.String(10), default="INFO"),
        sa.Column("module",     sa.String(50), default="system"),
        sa.Column("message",    sa.Text),
        sa.Column("extra",      sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime),
    )
    # alerts
    op.create_table(
        "alerts",
        sa.Column("id",         sa.Integer, primary_key=True),
        sa.Column("user_id",    sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("channel",    sa.String(20)),
        sa.Column("message",    sa.Text),
        sa.Column("sent",       sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime),
    )


def downgrade():
    op.drop_table("alerts")
    op.drop_table("logs")
    op.drop_table("trades")
    op.drop_table("settings")
    op.drop_table("users")
