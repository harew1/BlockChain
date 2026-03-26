"""
bot.py — Main trading bot loop.
  scan → analyze → trade → monitor → repeat
  • Per-user bot instances
  • Configurable scan interval
  • Emergency stop integration
  • WebSocket live price feed
  • Health heartbeat
"""

import asyncio
import datetime
import time
from typing import Dict, Optional

from analysis import analyzer
from api import binance_rest, ws_manager
from logger import get_logger
from models import SystemLog, LogLevel, TradeSignal, UserSettings, get_db
from notifications import NotificationDispatcher
from risk import RiskManager
from scanner import MarketScanner, CoinProfile
from trade import TradeEngine

log = get_logger("bot")


# ─────────────────────────────────────────────────────────────────────────────
class TradingBot:
    """
    One instance per user. Manages the full scan→trade lifecycle.
    """

    def __init__(self,
                 user_id: int,
                 settings: UserSettings,
                 initial_equity: float = 10_000.0):

        self.user_id  = user_id
        self.settings = settings
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_scan: Optional[datetime.datetime] = None
        self._scan_count = 0
        self._start_time: Optional[float] = None

        # Sub-systems
        self.scanner = MarketScanner(
            top_n=20,
            min_volume=settings.risk_percent * 1_000_000,  # scale with risk
        )
        self.risk_mgr = RiskManager(
            equity=initial_equity,
            risk_pct=settings.risk_percent,
            stop_loss_pct=settings.stop_loss_pct,
            take_profit_pct=settings.take_profit_pct,
            leverage=settings.leverage,
            max_trades=settings.max_trades,
            max_daily_loss_pct=settings.max_daily_loss,
        )
        self.engine = TradeEngine(
            user_id=user_id,
            simulation=settings.simulation_mode,
            initial_equity=initial_equity,
        )
        self.notifier = NotificationDispatcher(
            telegram=settings.notify_telegram,
            discord=settings.notify_discord,
            email=settings.notify_email,
            user_id=user_id,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._running:
            log.info("[Bot] Already running for user %d", self.user_id)
            return
        self._running    = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._loop())
        log.info("[Bot] Started for user %d (interval=%ds sim=%s)",
                 self.user_id, self.settings.scan_interval,
                 self.settings.simulation_mode)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        log.info("[Bot] Stopped for user %d", self.user_id)

    def emergency_stop(self) -> None:
        self.risk_mgr.trigger_emergency_stop()
        self.stop()
        # Schedule async close_all in running event loop if possible
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self._close_all_and_notify())
        except RuntimeError:
            pass

    async def _close_all_and_notify(self) -> None:
        results = await self.engine.close_all(reason="emergency_stop")
        for r in results:
            await self.notifier.trade_closed(
                r["symbol"], r["pnl"], r["pnl_pct"], "emergency_stop",
                self.settings.simulation_mode,
            )

    # ── Main loop ─────────────────────────────────────────────────────────────
    async def _loop(self) -> None:
        while self._running:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("[Bot] Unexpected error in cycle: %s", exc, exc_info=True)
                self._db_log(LogLevel.ERROR, str(exc))

            # Wait for next scan interval
            if self._running:
                await asyncio.sleep(self.settings.scan_interval)

    async def _cycle(self) -> None:
        if self.risk_mgr.emergency_stopped:
            log.warning("[Bot] Emergency stop active — skipping cycle.")
            return
        if not self.settings.trading_enabled:
            log.debug("[Bot] Trading disabled for user %d", self.user_id)
            return

        t0 = time.time()
        log.info("[Bot] ── Cycle #%d ─────────────────", self._scan_count + 1)

        # 1. Monitor existing positions (SL/TP/trailing)
        await self.engine.monitor_positions(self.risk_mgr)

        # 2. Market scan
        profiles = await self.scanner.scan()
        self._scan_count += 1
        self._last_scan  = datetime.datetime.utcnow()

        # 3. Arbitrage alerts
        for opp in self.scanner.arbitrage_opportunities[:3]:
            await self.notifier.arbitrage(opp)

        # 4. Signal processing
        signal_counts = {"LONG": 0, "SHORT": 0, "WAIT": 0}
        for profile in profiles:
            if profile.analysis is None:
                continue
            sig   = profile.analysis.primary_signal
            score = profile.analysis.ai_score
            signal_counts[sig.value] = signal_counts.get(sig.value, 0) + 1

            if sig == TradeSignal.WAIT:
                continue
            if score < 55:
                log.debug("[Bot] %s score %.1f too low — skip", profile.symbol, score)
                continue
            if profile.symbol in self.engine.open_symbols:
                continue

            # Build position spec via risk manager
            spec = self.risk_mgr.build_position(
                symbol=profile.symbol,
                signal=sig,
                entry_price=profile.price,
                open_symbols=self.engine.open_symbols,
                ai_score=score,
                simulate=self.settings.simulation_mode,
            )
            if spec is None:
                continue

            # Execute trade
            trade = await self.engine.open_trade(
                symbol=spec.symbol,
                signal=spec.signal,
                entry_price=spec.entry_price,
                quantity=spec.quantity,
                leverage=spec.leverage,
                stop_loss=spec.stop_loss,
                take_profit=spec.take_profit,
                ai_score=score,
                ai_features=profile.analysis.features,
            )
            if trade:
                # Init trailing stop
                self.risk_mgr.init_trailing(spec.symbol, spec.signal, spec.entry_price)
                # Update risk manager equity
                self.risk_mgr.update_equity(self.engine.equity)
                # Notify
                await self.notifier.trade_opened(
                    spec.symbol, sig.value, spec.entry_price,
                    spec.stop_loss, spec.take_profit,
                    score, self.settings.simulation_mode,
                )
                self._db_log(LogLevel.TRADE,
                             f"Opened {sig.value} {spec.symbol} @{spec.entry_price:.6f}")

        # 5. Scan summary notification (every 10 scans)
        if self._scan_count % 10 == 0:
            await self.notifier.scan_done(len(profiles), signal_counts)

        elapsed = time.time() - t0
        log.info("[Bot] Cycle done in %.1fs — open=%d equity=%.2f",
                 elapsed, len(self.engine.open_symbols), self.engine.equity)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _db_log(self, level: LogLevel, message: str, extra: dict = None) -> None:
        try:
            with get_db() as db:
                db.add(SystemLog(
                    user_id=self.user_id,
                    level=level,
                    module="bot",
                    message=message,
                    extra=extra,
                ))
        except Exception as exc:
            log.warning("[Bot] DB log failed: %s", exc)

    def status(self) -> dict:
        uptime = int(time.time() - self._start_time) if self._start_time else 0
        return {
            "user_id":     self.user_id,
            "running":     self._running,
            "scan_count":  self._scan_count,
            "last_scan":   self._last_scan.isoformat() if self._last_scan else None,
            "uptime_secs": uptime,
            "equity":      round(self.engine.equity, 2),
            "open_trades": len(self.engine.open_symbols),
            "emergency":   self.risk_mgr.emergency_stopped,
            "simulation":  self.settings.simulation_mode,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Bot registry — keyed by user_id
# ─────────────────────────────────────────────────────────────────────────────
class BotRegistry:
    def __init__(self):
        self._bots: Dict[int, TradingBot] = {}

    def get(self, user_id: int) -> Optional[TradingBot]:
        return self._bots.get(user_id)

    def create(self, user_id: int, settings: UserSettings,
               equity: float = 10_000.0) -> TradingBot:
        bot = TradingBot(user_id, settings, equity)
        self._bots[user_id] = bot
        return bot

    def start(self, user_id: int) -> bool:
        bot = self._bots.get(user_id)
        if bot:
            bot.start()
            return True
        return False

    def stop(self, user_id: int) -> bool:
        bot = self._bots.get(user_id)
        if bot:
            bot.stop()
            return True
        return False

    def emergency_stop(self, user_id: int) -> bool:
        bot = self._bots.get(user_id)
        if bot:
            bot.emergency_stop()
            return True
        return False

    def all_status(self) -> list:
        return [bot.status() for bot in self._bots.values()]


# Module-level registry
registry = BotRegistry()
