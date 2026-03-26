"""
backtest.py — Vectorized Backtesting Engine.
  • Loads historical klines from Binance REST
  • Runs strategy + risk logic on each bar
  • Simulates slippage
  • Computes: win rate, net profit, max drawdown, Sharpe, equity curve
"""

import asyncio
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from api import binance_rest, Kline
from analysis import analyzer, TFAnalysis
from config import (
    DEFAULT_RISK_PERCENT, DEFAULT_STOP_LOSS_PCT, DEFAULT_TAKE_PROFIT_PCT,
    DEFAULT_LEVERAGE,
)
from logger import get_logger
from models import TradeSignal
from risk import apply_slippage, calc_position

log = get_logger("backtest")


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class BacktestTrade:
    symbol:      str
    signal:      TradeSignal
    entry_bar:   int
    exit_bar:    int
    entry_price: float
    exit_price:  float
    quantity:    float
    pnl:         float
    pnl_pct:     float
    exit_reason: str
    ai_score:    float = 50.0


@dataclass
class BacktestResult:
    symbol:        str
    interval:      str
    start_date:    str
    end_date:      str
    initial_equity: float
    final_equity:  float
    net_profit:    float
    net_profit_pct: float
    total_trades:  int
    win_trades:    int
    loss_trades:   int
    win_rate:      float
    avg_win:       float
    avg_loss:      float
    profit_factor: float
    max_drawdown:  float
    max_drawdown_pct: float
    sharpe:        float
    equity_curve:  List[float]
    trades:        List[BacktestTrade] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Metric calculators
# ─────────────────────────────────────────────────────────────────────────────
def _max_drawdown(equity_curve: List[float]) -> Tuple[float, float]:
    arr      = np.array(equity_curve, dtype=float)
    peak     = np.maximum.accumulate(arr)
    drawdown = peak - arr
    max_dd   = float(drawdown.max())
    max_dd_p = float((drawdown / np.where(peak == 0, 1, peak)).max() * 100)
    return max_dd, max_dd_p


def _sharpe(equity_curve: List[float], rf: float = 0.02) -> float:
    """Annualized Sharpe (assuming daily bars for simplicity)."""
    arr     = np.array(equity_curve, dtype=float)
    returns = np.diff(arr) / arr[:-1]
    if returns.std() == 0:
        return 0.0
    return float((returns.mean() - rf / 252) / returns.std() * np.sqrt(252))


# ─────────────────────────────────────────────────────────────────────────────
# Backtester
# ─────────────────────────────────────────────────────────────────────────────
class Backtester:

    def __init__(self,
                 initial_equity: float     = 10_000.0,
                 risk_pct: float           = DEFAULT_RISK_PERCENT,
                 stop_loss_pct: float      = DEFAULT_STOP_LOSS_PCT,
                 take_profit_pct: float    = DEFAULT_TAKE_PROFIT_PCT,
                 leverage: int             = DEFAULT_LEVERAGE,
                 slippage_pct: float       = 0.05,
                 warm_up_bars: int         = 30):

        self.initial_equity  = initial_equity
        self.risk_pct        = risk_pct
        self.stop_loss_pct   = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.leverage        = leverage
        self.slippage_pct    = slippage_pct
        self.warm_up_bars    = warm_up_bars

    async def fetch_klines(self, symbol: str, interval: str,
                            limit: int = 1000) -> List[Kline]:
        log.info("[Backtest] Fetching %d klines for %s %s…", limit, symbol, interval)
        return await binance_rest.get_klines(symbol, interval, limit=limit)

    def run(self, klines: List[Kline], symbol: str,
            interval: str = "1h") -> BacktestResult:
        equity        = self.initial_equity
        equity_curve  = [equity]
        in_position   = False
        bt_trades: List[BacktestTrade] = []
        pos: Optional[dict] = None

        start = klines[self.warm_up_bars].open_time if len(klines) > self.warm_up_bars else klines[0].open_time
        end   = klines[-1].close_time

        for i in range(self.warm_up_bars, len(klines)):
            current_bar  = klines[i]
            current_price = current_bar.close
            window        = klines[max(0, i - 100): i + 1]

            # Check exit first
            if in_position and pos:
                exit_reason = None
                if pos["signal"] == TradeSignal.LONG:
                    if current_bar.low  <= pos["sl"]: exit_reason = "stop_loss";   ep = pos["sl"]
                    elif current_bar.high >= pos["tp"]: exit_reason = "take_profit"; ep = pos["tp"]
                else:
                    if current_bar.high >= pos["sl"]: exit_reason = "stop_loss";   ep = pos["sl"]
                    elif current_bar.low  <= pos["tp"]: exit_reason = "take_profit"; ep = pos["tp"]

                if exit_reason:
                    ep = apply_slippage(ep, pos["signal"], self.slippage_pct)
                    if pos["signal"] == TradeSignal.LONG:
                        pnl = (ep - pos["entry"]) * pos["qty"] * self.leverage
                    else:
                        pnl = (pos["entry"] - ep) * pos["qty"] * self.leverage
                    pnl_pct = pnl / (pos["entry"] * pos["qty"]) * 100
                    equity += pnl
                    equity_curve.append(equity)
                    bt_trades.append(BacktestTrade(
                        symbol=symbol,
                        signal=pos["signal"],
                        entry_bar=pos["entry_bar"],
                        exit_bar=i,
                        entry_price=pos["entry"],
                        exit_price=ep,
                        quantity=pos["qty"],
                        pnl=round(pnl, 4),
                        pnl_pct=round(pnl_pct, 4),
                        exit_reason=exit_reason,
                        ai_score=pos.get("ai_score", 50.0),
                    ))
                    in_position = False
                    pos = None
                    continue

            # Generate new signal if flat
            if not in_position:
                tf_res = analyzer.analyze_klines(window, interval)
                if tf_res is None:
                    continue
                signal = tf_res.signal
                if signal == TradeSignal.WAIT:
                    continue

                # Position size
                entry_raw = current_price
                spec = calc_position(
                    equity=equity,
                    risk_pct=self.risk_pct,
                    entry_price=entry_raw,
                    stop_loss_pct=self.stop_loss_pct,
                    take_profit_pct=self.take_profit_pct,
                    leverage=self.leverage,
                    signal=signal,
                    symbol=symbol,
                )
                if spec is None:
                    continue

                entry_filled = apply_slippage(entry_raw, signal, self.slippage_pct)
                pos = {
                    "signal":     signal,
                    "entry":      entry_filled,
                    "entry_bar":  i,
                    "sl":         spec.stop_loss,
                    "tp":         spec.take_profit,
                    "qty":        spec.quantity,
                    "ai_score":   50.0,
                }
                in_position = True

        # ── Metrics ───────────────────────────────────────────────────────────
        wins   = [t for t in bt_trades if t.pnl > 0]
        losses = [t for t in bt_trades if t.pnl <= 0]
        net    = equity - self.initial_equity
        max_dd, max_dd_pct = _max_drawdown(equity_curve) if equity_curve else (0, 0)
        sharpe = _sharpe(equity_curve) if len(equity_curve) > 2 else 0.0

        avg_win   = float(np.mean([t.pnl for t in wins]))    if wins   else 0.0
        avg_loss  = float(np.mean([t.pnl for t in losses]))  if losses else 0.0
        pf_num    = sum(t.pnl for t in wins)
        pf_den    = abs(sum(t.pnl for t in losses)) or 1e-9
        profit_factor = pf_num / pf_den

        def ts_to_str(ts):
            return datetime.datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")

        return BacktestResult(
            symbol=symbol,
            interval=interval,
            start_date=ts_to_str(start),
            end_date=ts_to_str(end),
            initial_equity=self.initial_equity,
            final_equity=round(equity, 2),
            net_profit=round(net, 2),
            net_profit_pct=round(net / self.initial_equity * 100, 2),
            total_trades=len(bt_trades),
            win_trades=len(wins),
            loss_trades=len(losses),
            win_rate=round(len(wins) / len(bt_trades) * 100 if bt_trades else 0, 1),
            avg_win=round(avg_win, 4),
            avg_loss=round(avg_loss, 4),
            profit_factor=round(profit_factor, 3),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            sharpe=round(sharpe, 3),
            equity_curve=equity_curve,
            trades=bt_trades,
        )

    async def run_async(self, symbol: str, interval: str = "1h",
                        limit: int = 1000) -> BacktestResult:
        klines = await self.fetch_klines(symbol, interval, limit)
        result = await asyncio.to_thread(self.run, klines, symbol, interval)
        log.info("[Backtest] %s %s: trades=%d win=%.1f%% net=%.2f dd=%.2f%%",
                 symbol, interval, result.total_trades, result.win_rate,
                 result.net_profit, result.max_drawdown_pct)
        return result

    def result_to_dict(self, r: BacktestResult) -> dict:
        return {
            "symbol":          r.symbol,
            "interval":        r.interval,
            "start_date":      r.start_date,
            "end_date":        r.end_date,
            "initial_equity":  r.initial_equity,
            "final_equity":    r.final_equity,
            "net_profit":      r.net_profit,
            "net_profit_pct":  r.net_profit_pct,
            "total_trades":    r.total_trades,
            "win_rate":        r.win_rate,
            "avg_win":         r.avg_win,
            "avg_loss":        r.avg_loss,
            "profit_factor":   r.profit_factor,
            "max_drawdown":    r.max_drawdown,
            "max_drawdown_pct": r.max_drawdown_pct,
            "sharpe":          r.sharpe,
            "equity_curve":    r.equity_curve,
        }


# Module-level singleton
backtester = Backtester()
