"""
risk.py — Risk Management Engine.
  • Position sizing (% risk of equity)
  • Stop-loss / Take-profit calculation
  • Trailing stop tracker
  • Daily loss limiter
  • Max concurrent trades
  • Correlation filter (avoid similar positions)
  • Slippage simulation for backtesting
  • Emergency panic-button (close all)
  • Trade sanity checks
"""

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from logger import get_logger
from models import TradeSignal

log = get_logger("risk")


# ─────────────────────────────────────────────────────────────────────────────
# Position calculator
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class PositionSpec:
    symbol:       str
    signal:       TradeSignal
    entry_price:  float
    quantity:     float          # in base asset
    notional:     float          # quantity × entry_price
    stop_loss:    float
    take_profit:  float
    risk_amount:  float          # USDT at risk
    rr_ratio:     float          # reward-to-risk
    leverage:     int


def calc_position(
    equity:          float,
    risk_pct:        float,
    entry_price:     float,
    stop_loss_pct:   float,
    take_profit_pct: float,
    leverage:        int,
    signal:          TradeSignal,
    symbol:          str,
) -> Optional[PositionSpec]:
    """
    Returns PositionSpec or None if the trade fails sanity checks.

    equity       : total account equity in USDT
    risk_pct     : % of equity willing to risk per trade (e.g. 1.0)
    stop_loss_pct: distance from entry to SL in %
    take_profit_pct: distance from entry to TP in %
    leverage     : futures leverage
    """
    if entry_price <= 0 or equity <= 0:
        log.warning("[Risk] Invalid entry_price or equity.")
        return None
    if signal == TradeSignal.WAIT:
        return None

    risk_amount = equity * (risk_pct / 100)
    sl_distance = entry_price * (stop_loss_pct / 100)
    if sl_distance == 0:
        log.warning("[Risk] Zero SL distance for %s", symbol)
        return None

    quantity = (risk_amount / sl_distance) * leverage
    notional = quantity * entry_price

    if signal == TradeSignal.LONG:
        stop_loss   = entry_price * (1 - stop_loss_pct   / 100)
        take_profit = entry_price * (1 + take_profit_pct / 100)
    else:  # SHORT
        stop_loss   = entry_price * (1 + stop_loss_pct   / 100)
        take_profit = entry_price * (1 - take_profit_pct / 100)

    rr = take_profit_pct / stop_loss_pct if stop_loss_pct else 0

    spec = PositionSpec(
        symbol=symbol, signal=signal,
        entry_price=round(entry_price, 8),
        quantity=round(quantity, 6),
        notional=round(notional, 2),
        stop_loss=round(stop_loss, 8),
        take_profit=round(take_profit, 8),
        risk_amount=round(risk_amount, 2),
        rr_ratio=round(rr, 2),
        leverage=leverage,
    )
    return spec


# ─────────────────────────────────────────────────────────────────────────────
# Trailing stop tracker
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TrailingStopState:
    symbol:    str
    signal:    TradeSignal
    trail_pct: float               # % trail distance
    best_price: float              # most favorable price seen so far
    stop_price: float

    def update(self, current_price: float) -> bool:
        """
        Update trailing stop. Returns True if the stop has been hit.
        """
        if self.signal == TradeSignal.LONG:
            if current_price > self.best_price:
                self.best_price = current_price
                self.stop_price = current_price * (1 - self.trail_pct / 100)
            return current_price <= self.stop_price
        else:  # SHORT
            if current_price < self.best_price:
                self.best_price = current_price
                self.stop_price = current_price * (1 + self.trail_pct / 100)
            return current_price >= self.stop_price


# ─────────────────────────────────────────────────────────────────────────────
# Correlation filter
# ─────────────────────────────────────────────────────────────────────────────
# Pairs considered highly correlated — do not hold both simultaneously.
CORRELATED_GROUPS = [
    {"BTCUSDT", "ETHUSDT", "BNBUSDT"},
    {"SOLUSDT", "AVAXUSDT", "DOTUSDT"},
    {"LINKUSDT", "AAVEUSDT", "UNIUSDT"},
]

def correlation_ok(symbol: str, open_symbols: List[str]) -> bool:
    """
    Returns False if opening `symbol` would violate the correlation limit
    (i.e. another symbol from the same group is already open).
    """
    for group in CORRELATED_GROUPS:
        if symbol in group:
            for open_sym in open_symbols:
                if open_sym in group and open_sym != symbol:
                    log.info("[Risk] Correlation block: %s blocked by %s",
                             symbol, open_sym)
                    return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Daily loss tracker
# ─────────────────────────────────────────────────────────────────────────────
class DailyLossTracker:
    def __init__(self, max_loss_pct: float, equity: float):
        self._max_loss_pct = max_loss_pct
        self._initial_equity = equity
        self._realized_loss  = 0.0
        self._date_str       = self._today()

    @staticmethod
    def _today() -> str:
        import datetime
        return datetime.date.today().isoformat()

    def _reset_if_new_day(self) -> None:
        if self._today() != self._date_str:
            self._realized_loss = 0.0
            self._date_str      = self._today()

    def record_loss(self, amount: float) -> None:
        self._reset_if_new_day()
        if amount > 0:
            self._realized_loss += amount

    def is_limit_reached(self) -> bool:
        self._reset_if_new_day()
        limit = self._initial_equity * (self._max_loss_pct / 100)
        reached = self._realized_loss >= limit
        if reached:
            log.warning("[Risk] Daily loss limit reached: %.2f / %.2f",
                        self._realized_loss, limit)
        return reached

    def daily_pnl(self) -> float:
        return -self._realized_loss


# ─────────────────────────────────────────────────────────────────────────────
# Slippage simulator
# ─────────────────────────────────────────────────────────────────────────────
def apply_slippage(price: float, signal: TradeSignal,
                   slippage_pct: float = 0.05) -> float:
    """
    Simulate realistic entry slippage.
    LONG entries fill slightly higher, SHORT entries slightly lower.
    """
    slip = price * (slippage_pct / 100)
    return price + slip if signal == TradeSignal.LONG else price - slip


# ─────────────────────────────────────────────────────────────────────────────
# Sanity checks
# ─────────────────────────────────────────────────────────────────────────────
def sanity_check(spec: PositionSpec, max_notional: float = 100_000) -> Tuple[bool, str]:
    """Light sanity checks before submitting any order."""
    from typing import Tuple
    if spec.quantity <= 0:
        return False, "Quantity ≤ 0"
    if spec.notional > max_notional:
        return False, f"Notional {spec.notional:.0f} exceeds limit {max_notional:.0f}"
    if spec.stop_loss <= 0:
        return False, "Stop-loss ≤ 0"
    if spec.rr_ratio < 1.0:
        return False, f"RR ratio {spec.rr_ratio:.2f} < 1.0"
    return True, "OK"


# ─────────────────────────────────────────────────────────────────────────────
# RiskManager — stateful facade used by bot.py
# ─────────────────────────────────────────────────────────────────────────────
class RiskManager:
    def __init__(self,
                 equity: float,
                 risk_pct: float,
                 stop_loss_pct: float,
                 take_profit_pct: float,
                 leverage: int,
                 max_trades: int,
                 max_daily_loss_pct: float,
                 trail_pct: float = 1.0,
                 slippage_pct: float = 0.05):

        self.equity          = equity
        self.risk_pct        = risk_pct
        self.stop_loss_pct   = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.leverage        = leverage
        self.max_trades      = max_trades
        self.trail_pct       = trail_pct
        self.slippage_pct    = slippage_pct

        self._daily_loss     = DailyLossTracker(max_daily_loss_pct, equity)
        self._trailing: Dict[str, TrailingStopState] = {}
        self._emergency_stop = False

    # ── Emergency ────────────────────────────────────────────────────────────
    def trigger_emergency_stop(self) -> None:
        log.warning("[Risk] ⚠ EMERGENCY STOP TRIGGERED")
        self._emergency_stop = True

    def reset_emergency_stop(self) -> None:
        self._emergency_stop = False

    @property
    def emergency_stopped(self) -> bool:
        return self._emergency_stop

    # ── Position allocation ───────────────────────────────────────────────────
    def build_position(self, symbol: str, signal: TradeSignal,
                       entry_price: float,
                       open_symbols: List[str],
                       ai_score: float = 50.0,
                       simulate: bool = True) -> Optional[PositionSpec]:
        """Full risk-check pipeline → returns PositionSpec or None."""
        if self._emergency_stop:
            log.warning("[Risk] Emergency stop active — no new trades.")
            return None
        if self._daily_loss.is_limit_reached():
            return None
        if len(open_symbols) >= self.max_trades:
            log.info("[Risk] Max trades (%d) reached.", self.max_trades)
            return None
        if not correlation_ok(symbol, open_symbols):
            return None

        # Dynamic sizing from AI score
        from ai import ai_model
        adj_risk = ai_model.dynamic_position_size(ai_score, self.risk_pct)

        # Simulate entry slippage
        filled_price = (apply_slippage(entry_price, signal, self.slippage_pct)
                        if simulate else entry_price)

        spec = calc_position(
            equity=self.equity,
            risk_pct=adj_risk,
            entry_price=filled_price,
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
            leverage=self.leverage,
            signal=signal,
            symbol=symbol,
        )
        if spec is None:
            return None

        ok, reason = sanity_check(spec)
        if not ok:
            log.warning("[Risk] Sanity check failed for %s: %s", symbol, reason)
            return None

        return spec

    # ── Trailing stop ────────────────────────────────────────────────────────
    def init_trailing(self, symbol: str, signal: TradeSignal, entry: float) -> None:
        init_stop = (entry * (1 - self.trail_pct / 100) if signal == TradeSignal.LONG
                     else entry * (1 + self.trail_pct / 100))
        self._trailing[symbol] = TrailingStopState(
            symbol=symbol, signal=signal, trail_pct=self.trail_pct,
            best_price=entry, stop_price=init_stop,
        )

    def check_trailing(self, symbol: str, price: float) -> bool:
        state = self._trailing.get(symbol)
        if state is None:
            return False
        return state.update(price)

    def remove_trailing(self, symbol: str) -> None:
        self._trailing.pop(symbol, None)

    # ── Loss recording ────────────────────────────────────────────────────────
    def record_trade_result(self, pnl: float) -> None:
        if pnl < 0:
            self._daily_loss.record_loss(-pnl)
        # Update equity
        self.equity = max(0.0, self.equity + pnl)

    def update_equity(self, equity: float) -> None:
        self.equity = equity

    # ── Status ───────────────────────────────────────────────────────────────
    def status(self) -> dict:
        return {
            "equity":        self.equity,
            "daily_pnl":     self._daily_loss.daily_pnl(),
            "emergency_stop": self._emergency_stop,
            "trailing_symbols": list(self._trailing.keys()),
        }
