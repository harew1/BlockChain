"""
tests/test_platform.py — Unit test suite.
Covers: analysis indicators, risk engine, AI heuristic,
        cache layer, trade position calc, backtest metrics.
"""

import asyncio
import os
import sys
import math

import pytest
import numpy as np

# ─── Env setup before any local imports ───────────────────────────────────────
os.environ.setdefault("DATABASE_URL",    "sqlite:///./test_trading.db")
os.environ.setdefault("JWT_SECRET",      "test-secret-key")
os.environ.setdefault("SIMULATION_MODE", "true")
os.environ.setdefault("USE_REDIS",       "false")
os.environ.setdefault("LOG_FORMAT",      "text")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Analysis ─────────────────────────────────────────────────────────────────
class TestIndicators:
    def _closes(self, n=100, trend=0.0):
        """Generate synthetic close prices with optional trend."""
        rng = np.random.default_rng(42)
        prices = 100.0 + np.cumsum(rng.normal(trend, 1.0, n))
        return np.clip(prices, 1, None)

    def test_ema_length(self):
        from analysis import ema
        c = self._closes(100)
        result = ema(c, 9)
        assert len(result) == 100

    def test_ema_trend_up(self):
        from analysis import ema
        c = self._closes(100, trend=0.5)
        result = ema(c, 9)
        # EMA should be increasing on average for uptrend
        valid = result[~np.isnan(result)]
        assert valid[-1] > valid[0]

    def test_rsi_range(self):
        from analysis import rsi
        c = self._closes(100)
        result = rsi(c, 14)
        valid = result[~np.isnan(result)]
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_overbought(self):
        from analysis import rsi
        # Strongly uptrending series → RSI should be high
        c = np.array([100.0 + i * 2 for i in range(60)], dtype=float)
        result = rsi(c, 14)
        valid = result[~np.isnan(result)]
        assert valid[-1] > 70

    def test_macd_returns_three_arrays(self):
        from analysis import macd
        c = self._closes(100)
        line, signal, hist = macd(c)
        assert len(line) == len(signal) == len(hist) == 100

    def test_bollinger_bands(self):
        from analysis import bollinger
        c = self._closes(100)
        lo, mid, hi = bollinger(c, 20)
        valid_idx = ~(np.isnan(lo) | np.isnan(hi))
        assert (hi[valid_idx] > mid[valid_idx]).all()
        assert (mid[valid_idx] > lo[valid_idx]).all()

    def test_momentum_length(self):
        from analysis import momentum
        c = self._closes(100)
        result = momentum(c, 10)
        assert len(result) == 100
        # first `period` entries should be NaN
        assert np.isnan(result[:10]).all()


# ─── Liquidity score ──────────────────────────────────────────────────────────
class TestLiquidityScore:
    def _make_depth(self, bid_qty=100.0, ask_qty=100.0, price=50000.0):
        from api import OrderBookDepth
        bids = [[price - i * 10, bid_qty] for i in range(20)]
        asks = [[price + i * 10, ask_qty] for i in range(20)]
        return OrderBookDepth(symbol="BTCUSDT", bids=bids, asks=asks)

    def test_balanced_book_high_score(self):
        from analysis import liquidity_score
        depth = self._make_depth(bid_qty=100, ask_qty=100)
        score = liquidity_score(depth)
        assert score > 50

    def test_empty_book_zero(self):
        from api import OrderBookDepth
        from analysis import liquidity_score
        depth = OrderBookDepth("X", bids=[], asks=[])
        assert liquidity_score(depth) == 0.0

    def test_score_range(self):
        from analysis import liquidity_score
        depth = self._make_depth()
        s = liquidity_score(depth)
        assert 0.0 <= s <= 100.0


# ─── Risk ─────────────────────────────────────────────────────────────────────
class TestRiskEngine:
    def test_long_position_calc(self):
        from risk import calc_position
        from models import TradeSignal
        spec = calc_position(
            equity=10_000, risk_pct=1.0,
            entry_price=50_000, stop_loss_pct=2.0,
            take_profit_pct=4.0, leverage=5,
            signal=TradeSignal.LONG, symbol="BTCUSDT"
        )
        assert spec is not None
        assert spec.stop_loss  < spec.entry_price
        assert spec.take_profit > spec.entry_price
        assert spec.quantity > 0
        assert spec.rr_ratio == pytest.approx(2.0, rel=0.01)

    def test_short_position_calc(self):
        from risk import calc_position
        from models import TradeSignal
        spec = calc_position(
            equity=10_000, risk_pct=1.0,
            entry_price=50_000, stop_loss_pct=2.0,
            take_profit_pct=4.0, leverage=5,
            signal=TradeSignal.SHORT, symbol="BTCUSDT"
        )
        assert spec is not None
        assert spec.stop_loss  > spec.entry_price
        assert spec.take_profit < spec.entry_price

    def test_wait_returns_none(self):
        from risk import calc_position
        from models import TradeSignal
        spec = calc_position(
            equity=10_000, risk_pct=1.0,
            entry_price=100, stop_loss_pct=2.0,
            take_profit_pct=4.0, leverage=1,
            signal=TradeSignal.WAIT, symbol="X"
        )
        assert spec is None

    def test_sanity_check_passes(self):
        from risk import calc_position, sanity_check
        from models import TradeSignal
        spec = calc_position(
            equity=10_000, risk_pct=1.0,
            entry_price=100, stop_loss_pct=2.0,
            take_profit_pct=4.0, leverage=5,
            signal=TradeSignal.LONG, symbol="X"
        )
        ok, reason = sanity_check(spec)
        assert ok, reason

    def test_correlation_filter(self):
        from risk import correlation_ok
        # BTC and ETH in same group → blocked
        assert not correlation_ok("ETHUSDT", ["BTCUSDT"])
        # Different groups → ok
        assert correlation_ok("SOLUSDT", ["BTCUSDT"])

    def test_trailing_stop_long(self):
        from risk import TrailingStopState
        from models import TradeSignal
        ts = TrailingStopState("X", TradeSignal.LONG, trail_pct=2.0,
                               best_price=100, stop_price=98)
        assert not ts.update(105)   # price rose → stop moves up
        assert ts.stop_price == pytest.approx(102.9, rel=0.01)
        assert ts.update(100)       # falls back below new stop → hit

    def test_slippage(self):
        from risk import apply_slippage
        from models import TradeSignal
        long_fill  = apply_slippage(100.0, TradeSignal.LONG,  slippage_pct=0.1)
        short_fill = apply_slippage(100.0, TradeSignal.SHORT, slippage_pct=0.1)
        assert long_fill  > 100.0
        assert short_fill < 100.0


# ─── AI ───────────────────────────────────────────────────────────────────────
class TestAI:
    def _features(self, rsi_15=50.0, cross=1.0):
        return {
            "15m_rsi": rsi_15, "15m_ema_cross": cross,
            "15m_macd_hist": 0.001, "15m_momentum": 2.0,
            "15m_bb_pct": 0.5, "15m_vol_ratio": 1.2,
            "15m_trend": 1.0, "liquidity": 60.0,
            "sentiment": 0.1, "whale_score": 25.0,
        }

    def test_heuristic_score_range(self):
        from ai import EnsembleSignalModel
        model = EnsembleSignalModel.__new__(EnsembleSignalModel)
        for _ in range(20):
            feats = self._features(rsi_15=np.random.uniform(10, 90))
            score = model._heuristic_score(feats)
            assert 0.0 <= score <= 100.0, f"Score out of range: {score}"

    def test_dynamic_position_size_strong(self):
        from ai import ai_model
        size = ai_model.dynamic_position_size(85.0, base_risk_pct=1.0)
        assert size > 1.0

    def test_dynamic_position_size_weak(self):
        from ai import ai_model
        size = ai_model.dynamic_position_size(30.0, base_risk_pct=1.0)
        assert size == pytest.approx(0.5, rel=0.01)


# ─── Cache ────────────────────────────────────────────────────────────────────
class TestCache:
    def test_set_get(self):
        import cache
        cache.set("test_key", {"value": 42}, ttl=60)
        result = cache.get("test_key")
        assert result == {"value": 42}

    def test_miss_returns_none(self):
        import cache
        assert cache.get("nonexistent_key_xyz") is None

    def test_delete(self):
        import cache
        cache.set("del_key", "hello", ttl=60)
        cache.delete("del_key")
        assert cache.get("del_key") is None

    def test_ttl_expiry(self):
        import cache, time
        cache.set("ttl_key", "bye", ttl=1)
        time.sleep(1.1)
        assert cache.get("ttl_key") is None


# ─── Backtest metrics ─────────────────────────────────────────────────────────
class TestBacktestMetrics:
    def test_max_drawdown(self):
        from backtest import _max_drawdown
        curve = [100, 110, 105, 95, 98, 112]
        dd, dd_pct = _max_drawdown(curve)
        assert dd == pytest.approx(15.0, rel=0.01)

    def test_sharpe_flat(self):
        from backtest import _sharpe
        flat = [100.0] * 252
        assert _sharpe(flat) == 0.0

    def test_sharpe_uptrend(self):
        from backtest import _sharpe
        trend = [100 + i for i in range(252)]
        sharpe = _sharpe(trend)
        assert sharpe > 0


# ─── DB models ────────────────────────────────────────────────────────────────
class TestDBModels:
    def setup_method(self):
        from models import init_db
        init_db()

    def test_admin_user_created(self):
        from models import User, UserRole, get_db
        with get_db() as db:
            admin = db.query(User).filter_by(username="admin").first()
            assert admin is not None
            assert admin.role == UserRole.admin

    def test_user_settings_created(self):
        from models import UserSettings, get_db
        with get_db() as db:
            settings = db.query(UserSettings).first()
            assert settings is not None
            assert settings.risk_percent > 0
