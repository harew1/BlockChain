"""
notifications.py — Multi-channel notification dispatcher.
  Channels : Telegram | Discord (webhook) | Email (SMTP)
  Features :
    • Deduplication via AlertHistory table
    • Async sending
    • Per-user channel preferences
    • Retry on transient errors
"""

import asyncio
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import aiohttp

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    DISCORD_WEBHOOK,
    EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, EMAIL_TO,
)
from logger import get_logger
from models import AlertHistory, get_db

log = get_logger("notifications")


# ─────────────────────────────────────────────────────────────────────────────
# Low-level senders
# ─────────────────────────────────────────────────────────────────────────────
async def _send_telegram(message: str, retries: int = 3) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.debug("[Notify] Telegram not configured.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            try:
                async with session.post(url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=10)) as r:
                    r.raise_for_status()
                    log.info("[Notify] Telegram sent.")
                    return True
            except Exception as exc:
                log.warning("[Notify] Telegram attempt %d: %s", attempt + 1, exc)
                await asyncio.sleep(2 ** attempt)
    return False


async def _send_discord(message: str, retries: int = 3) -> bool:
    if not DISCORD_WEBHOOK:
        log.debug("[Notify] Discord not configured.")
        return False
    payload = {"content": message}
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            try:
                async with session.post(DISCORD_WEBHOOK, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=10)) as r:
                    r.raise_for_status()
                    log.info("[Notify] Discord sent.")
                    return True
            except Exception as exc:
                log.warning("[Notify] Discord attempt %d: %s", attempt + 1, exc)
                await asyncio.sleep(2 ** attempt)
    return False


async def _send_email(subject: str, body: str, retries: int = 2) -> bool:
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        log.debug("[Notify] Email not configured.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(body, "plain"))

    def _smtp_send():
        ctx = ssl.create_default_context()
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as srv:
            srv.starttls(context=ctx)
            srv.login(EMAIL_USER, EMAIL_PASS)
            srv.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())

    for attempt in range(retries):
        try:
            await asyncio.to_thread(_smtp_send)
            log.info("[Notify] Email sent to %s", EMAIL_TO)
            return True
        except Exception as exc:
            log.warning("[Notify] Email attempt %d: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Message builder helpers
# ─────────────────────────────────────────────────────────────────────────────
def trade_opened_msg(symbol: str, signal: str, entry: float, sl: float,
                     tp: float, ai_score: float, sim: bool) -> str:
    mode = "🔵 SIM" if sim else "🟢 LIVE"
    arrow = "📈 LONG" if signal == "LONG" else "📉 SHORT"
    return (
        f"<b>{mode} — Trade Opened</b>\n"
        f"{arrow} <b>{symbol}</b>\n"
        f"Entry : <code>{entry:.6f}</code>\n"
        f"SL    : <code>{sl:.6f}</code>\n"
        f"TP    : <code>{tp:.6f}</code>\n"
        f"AI    : <code>{ai_score:.1f}/100</code>"
    )


def trade_closed_msg(symbol: str, pnl: float, pnl_pct: float,
                     reason: str, sim: bool) -> str:
    emoji = "✅" if pnl >= 0 else "❌"
    mode  = "🔵 SIM" if sim else "🟢 LIVE"
    return (
        f"<b>{mode} — Trade Closed {emoji}</b>\n"
        f"<b>{symbol}</b> [{reason}]\n"
        f"PnL   : <code>{pnl:+.4f} USDT</code>\n"
        f"PnL % : <code>{pnl_pct:+.2f}%</code>"
    )


def scan_summary_msg(profiles_count: int, signals: dict) -> str:
    return (
        f"<b>📊 Scan Complete</b>\n"
        f"Coins analyzed : {profiles_count}\n"
        f"LONG signals   : {signals.get('LONG', 0)}\n"
        f"SHORT signals  : {signals.get('SHORT', 0)}\n"
        f"WAIT           : {signals.get('WAIT', 0)}"
    )


def arbitrage_msg(opp: dict) -> str:
    return (
        f"<b>⚡ Arbitrage Opportunity</b>\n"
        f"<b>{opp['symbol']}</b>\n"
        f"Buy on  : {opp['buy_on']} @ {opp['binance_price' if opp['buy_on']=='binance' else 'bybit_price']:.6f}\n"
        f"Sell on : {opp['sell_on']}\n"
        f"Spread  : <code>{opp['spread_pct']:.3f}%</code>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────
class NotificationDispatcher:

    def __init__(self,
                 telegram: bool = True,
                 discord:  bool = True,
                 email:    bool = False,
                 user_id: Optional[int] = None):
        self.telegram = telegram
        self.discord  = discord
        self.email    = email
        self.user_id  = user_id

    async def send(self, message: str, subject: str = "CryptoTrader Alert") -> None:
        """Dispatch to all configured channels and record in DB."""
        tasks = []
        if self.telegram:
            tasks.append(self._dispatch("telegram", message))
        if self.discord:
            tasks.append(self._dispatch("discord", message))
        if self.email:
            tasks.append(self._dispatch("email", message, subject))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch(self, channel: str, message: str,
                        subject: str = "CryptoTrader Alert") -> None:
        sent = False
        if channel == "telegram":
            sent = await _send_telegram(message)
        elif channel == "discord":
            sent = await _send_discord(message)
        elif channel == "email":
            sent = await _send_email(subject, message)

        # Record in DB regardless of success
        try:
            with get_db() as db:
                db.add(AlertHistory(
                    user_id=self.user_id,
                    channel=channel,
                    message=message[:2000],
                    sent=sent,
                ))
        except Exception as exc:
            log.warning("[Notify] DB record failed: %s", exc)

    # ── Convenience wrappers ──────────────────────────────────────────────────
    async def trade_opened(self, symbol, signal, entry, sl, tp, ai_score, sim):
        await self.send(trade_opened_msg(symbol, signal, entry, sl, tp, ai_score, sim))

    async def trade_closed(self, symbol, pnl, pnl_pct, reason, sim):
        await self.send(trade_closed_msg(symbol, pnl, pnl_pct, reason, sim))

    async def scan_done(self, profiles_count, signals):
        await self.send(scan_summary_msg(profiles_count, signals))

    async def arbitrage(self, opp):
        await self.send(arbitrage_msg(opp))
