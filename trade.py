"""
trade.py — Trade Execution Engine.
  • SIMULATION mode  : no real orders, virtual portfolio
  • REAL mode        : Binance Futures market orders
  • Per-user trade ledger
  • PnL / ROI / equity-curve tracking
  • Post-trade AI outcome labeling
"""

import datetime
import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from api import binance_rest
from config import SIMULATION_MODE
from logger import get_logger
from models import Trade, TradeSignal, TradeStatus, get_db

log = get_logger("trade")


# ─────────────────────────────────────────────────────────────────────────────
# Virtual position (simulation)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class VirtualPosition:
    trade_id:    int
    user_id:     int
    symbol:      str
    signal:      TradeSignal
    entry_price: float
    quantity:    float
    leverage:    int
    stop_loss:   float
    take_profit: float
    ai_score:    float
    ai_features: dict
    opened_at:   datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    peak_price:  float = 0.0

    def unrealized_pnl(self, current_price: float) -> float:
        if self.signal == TradeSignal.LONG:
            return (current_price - self.entry_price) * self.quantity * self.leverage
        else:
            return (self.entry_price - current_price) * self.quantity * self.leverage

    def unrealized_pnl_pct(self, current_price: float) -> float:
        cost = self.entry_price * self.quantity
        return self.unrealized_pnl(current_price) / cost * 100 if cost else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Trade Engine
# ─────────────────────────────────────────────────────────────────────────────
class TradeEngine:

    def __init__(self, user_id: int, simulation: bool = True,
                 initial_equity: float = 10_000.0):
        self.user_id    = user_id
        self.simulation = simulation
        self.equity     = initial_equity
        self._positions: Dict[str, VirtualPosition] = {}
        self._equity_curve: List[dict] = []
        self._lock = asyncio.Lock()

    # ── Open trade ────────────────────────────────────────────────────────────
    async def open_trade(self,
                         symbol:      str,
                         signal:      TradeSignal,
                         entry_price: float,
                         quantity:    float,
                         leverage:    int,
                         stop_loss:   float,
                         take_profit: float,
                         ai_score:    float = 50.0,
                         ai_features: dict  = None) -> Optional[Trade]:
        if signal == TradeSignal.WAIT:
            return None
        if symbol in self._positions:
            log.info("[Trade] Already in position: %s", symbol)
            return None

        async with self._lock:
            # Persist to DB first to get an ID
            with get_db() as db:
                trade = Trade(
                    user_id=user_id    if (user_id := self.user_id) else self.user_id,
                    symbol=symbol,
                    signal=signal,
                    status=TradeStatus.open,
                    simulation=self.simulation,
                    exchange="binance",
                    entry_price=entry_price,
                    quantity=quantity,
                    leverage=leverage,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    ai_score=ai_score,
                    ai_features=ai_features or {},
                )
                db.add(trade)
                db.flush()
                trade_id = trade.id

            # Real execution
            if not self.simulation:
                try:
                    side = "BUY" if signal == TradeSignal.LONG else "SELL"
                    await binance_rest.set_leverage(symbol, leverage)
                    order = await binance_rest.place_market_order(symbol, side, quantity)
                    log.info("[Trade] REAL ORDER: %s %s qty=%s orderId=%s",
                             side, symbol, quantity, order.get("orderId"))
                except Exception as exc:
                    log.error("[Trade] Real order failed for %s: %s", symbol, exc)
                    with get_db() as db:
                        t = db.query(Trade).filter_by(id=trade_id).first()
                        if t:
                            t.status = TradeStatus.failed
                    return None
            else:
                log.info("[Trade] SIM OPEN: %s %s @%.6f qty=%.4f",
                         signal.value, symbol, entry_price, quantity)

            # Register virtual position
            pos = VirtualPosition(
                trade_id=trade_id,
                user_id=self.user_id,
                symbol=symbol,
                signal=signal,
                entry_price=entry_price,
                quantity=quantity,
                leverage=leverage,
                stop_loss=stop_loss,
                take_profit=take_profit,
                ai_score=ai_score,
                ai_features=ai_features or {},
                peak_price=entry_price,
            )
            self._positions[symbol] = pos

            # Record in AI buffer
            from ai import ai_model
            ai_model.record_signal(trade_id, ai_features or {})

            log.info("[Trade] Opened #%d %s %s", trade_id, signal.value, symbol)
            return trade

    # ── Close trade ───────────────────────────────────────────────────────────
    async def close_trade(self, symbol: str, exit_price: float,
                          reason: str = "signal") -> Optional[dict]:
        async with self._lock:
            pos = self._positions.pop(symbol, None)
            if pos is None:
                log.warning("[Trade] No open position for %s", symbol)
                return None

            pnl = pos.unrealized_pnl(exit_price)
            pnl_pct = pos.unrealized_pnl_pct(exit_price)
            cost    = pos.entry_price * pos.quantity
            roi     = pnl / (cost / pos.leverage) * 100 if cost else 0.0

            now = datetime.datetime.utcnow()

            with get_db() as db:
                trade = db.query(Trade).filter_by(id=pos.trade_id).first()
                if trade:
                    trade.status     = TradeStatus.closed
                    trade.exit_price = exit_price
                    trade.pnl        = round(pnl, 4)
                    trade.pnl_pct    = round(pnl_pct, 4)
                    trade.roi        = round(roi, 4)
                    trade.exit_time  = now
                    trade.notes      = reason

            # Real close order
            if not self.simulation:
                try:
                    side = "SELL" if pos.signal == TradeSignal.LONG else "BUY"
                    await binance_rest.place_market_order(symbol, side, pos.quantity)
                except Exception as exc:
                    log.error("[Trade] Real close order failed for %s: %s", symbol, exc)

            self.equity += pnl
            self._record_equity()

            # Teach AI outcome
            from ai import ai_model
            ai_model.record_outcome(pos.trade_id, win=(pnl > 0))

            result = {
                "trade_id":   pos.trade_id,
                "symbol":     symbol,
                "signal":     pos.signal.value,
                "entry":      pos.entry_price,
                "exit":       exit_price,
                "pnl":        round(pnl, 4),
                "pnl_pct":    round(pnl_pct, 4),
                "roi":        round(roi, 4),
                "reason":     reason,
                "closed_at":  now.isoformat(),
            }
            log.info("[Trade] Closed #%d %s PnL=%.4f (%.2f%%) reason=%s",
                     pos.trade_id, symbol, pnl, pnl_pct, reason)
            return result

    # ── Position monitor (SL / TP / Trailing) ─────────────────────────────────
    async def monitor_positions(self, risk_mgr) -> None:
        """
        Called periodically by the bot loop to check SL/TP/trailing stop.
        risk_mgr: RiskManager instance
        """
        if not self._positions:
            return
        for symbol, pos in list(self._positions.items()):
            try:
                ticker = await binance_rest.get_ticker(symbol)
                price  = ticker.price
            except Exception as exc:
                log.warning("[Trade] Cannot fetch price for %s: %s", symbol, exc)
                continue

            # Trailing stop
            if risk_mgr.check_trailing(symbol, price):
                await self.close_trade(symbol, price, reason="trailing_stop")
                risk_mgr.remove_trailing(symbol)
                continue

            # Hard SL / TP
            if pos.signal == TradeSignal.LONG:
                if price <= pos.stop_loss:
                    await self.close_trade(symbol, price, reason="stop_loss")
                elif price >= pos.take_profit:
                    await self.close_trade(symbol, price, reason="take_profit")
            else:
                if price >= pos.stop_loss:
                    await self.close_trade(symbol, price, reason="stop_loss")
                elif price <= pos.take_profit:
                    await self.close_trade(symbol, price, reason="take_profit")

    # ── Emergency close all ───────────────────────────────────────────────────
    async def close_all(self, reason: str = "emergency") -> List[dict]:
        results = []
        for symbol in list(self._positions.keys()):
            try:
                ticker = await binance_rest.get_ticker(symbol)
                r = await self.close_trade(symbol, ticker.price, reason)
                if r:
                    results.append(r)
            except Exception as exc:
                log.error("[Trade] Emergency close %s: %s", symbol, exc)
        return results

    # ── Equity curve ──────────────────────────────────────────────────────────
    def _record_equity(self) -> None:
        self._equity_curve.append({
            "ts":     datetime.datetime.utcnow().isoformat(),
            "equity": round(self.equity, 2),
        })

    def equity_curve(self) -> List[dict]:
        return self._equity_curve

    # ── Portfolio snapshot ────────────────────────────────────────────────────
    async def portfolio_snapshot(self) -> dict:
        positions = []
        total_unrealized = 0.0
        for symbol, pos in self._positions.items():
            try:
                ticker = await binance_rest.get_ticker(symbol)
                upnl   = pos.unrealized_pnl(ticker.price)
                upnl_p = pos.unrealized_pnl_pct(ticker.price)
                total_unrealized += upnl
                positions.append({
                    "symbol":       symbol,
                    "signal":       pos.signal.value,
                    "entry_price":  pos.entry_price,
                    "current_price": ticker.price,
                    "quantity":     pos.quantity,
                    "leverage":     pos.leverage,
                    "unrealized_pnl": round(upnl, 4),
                    "unrealized_pct": round(upnl_p, 2),
                    "stop_loss":    pos.stop_loss,
                    "take_profit":  pos.take_profit,
                    "ai_score":     pos.ai_score,
                    "opened_at":    pos.opened_at.isoformat(),
                })
            except Exception:
                pass

        with get_db() as db:
            trades = db.query(Trade).filter_by(
                user_id=self.user_id, status=TradeStatus.closed.value
            ).all()
            total_realized = sum(t.pnl or 0 for t in trades)
            win_trades     = sum(1 for t in trades if (t.pnl or 0) > 0)
            total_trades   = len(trades)
            win_rate       = win_trades / total_trades * 100 if total_trades else 0

        return {
            "equity":            round(self.equity, 2),
            "unrealized_pnl":    round(total_unrealized, 4),
            "realized_pnl":      round(total_realized, 4),
            "total_pnl":         round(total_unrealized + total_realized, 4),
            "win_rate":          round(win_rate, 1),
            "total_trades":      total_trades,
            "open_positions":    positions,
            "equity_curve":      self._equity_curve[-50:],
        }

    # ── Trade history ─────────────────────────────────────────────────────────
    def trade_history(self, limit: int = 50) -> List[dict]:
        with get_db() as db:
            trades = (
                db.query(Trade)
                .filter_by(user_id=self.user_id)
                .order_by(Trade.entry_time.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id":          t.id,
                    "symbol":      t.symbol,
                    "signal":      t.signal.value,
                    "status":      t.status.value,
                    "simulation":  t.simulation,
                    "entry_price": t.entry_price,
                    "exit_price":  t.exit_price,
                    "pnl":         t.pnl,
                    "pnl_pct":     t.pnl_pct,
                    "ai_score":    t.ai_score,
                    "entry_time":  t.entry_time.isoformat() if t.entry_time else None,
                    "exit_time":   t.exit_time.isoformat()  if t.exit_time  else None,
                    "notes":       t.notes,
                }
                for t in trades
            ]

    @property
    def open_symbols(self) -> List[str]:
        return list(self._positions.keys())
