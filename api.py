"""
api.py — Exchange API layer.
  • Binance Futures (primary)  — WebSocket + REST
  • Bybit Futures (optional)   — REST
  • CoinGecko                  — market cap / supply
  • On-chain stub              — whale / liquidity pool signals

All async. Rate-limit aware. Exponential-backoff reconnect on WebSocket drop.
"""

import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from cache import cached, get as cache_get, set as cache_set
from config import (
    BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_BASE_URL, BINANCE_WS_URL,
    BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_BASE_URL,
    COINGECKO_BASE, COINGECKO_API_KEY, QUOTE_ASSET,
)
from logger import get_logger

log = get_logger("api")


# ─────────────────────────────────────────────────────────────────────────────
# Data containers
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Ticker:
    symbol: str
    price: float
    change_24h: float   # %
    volume_24h: float
    high_24h: float
    low_24h: float
    ts: float = field(default_factory=time.time)


@dataclass
class Kline:
    symbol: str
    interval: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    closed: bool


@dataclass
class OrderBookDepth:
    symbol: str
    bids: List[List[float]]   # [[price, qty], ...]
    asks: List[List[float]]
    ts: float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────────────────
# Rate-limit token bucket
# ─────────────────────────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, calls_per_sec: float = 10.0):
        self._min_interval = 1.0 / calls_per_sec
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now  = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# ─────────────────────────────────────────────────────────────────────────────
# Binance REST client
# ─────────────────────────────────────────────────────────────────────────────
class BinanceREST:
    def __init__(self):
        self._limiter  = RateLimiter(calls_per_sec=8)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-MBX-APIKEY": BINANCE_API_KEY}
            )
        return self._session

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(
            BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return params

    async def _get(self, path: str, params: dict = None, signed=False,
                   retries=3) -> Any:
        await self._limiter.acquire()
        session = await self._session_()
        params  = params or {}
        if signed:
            params = self._sign(params)
        url = BINANCE_BASE_URL + path
        for attempt in range(retries):
            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    r.raise_for_status()
                    return await r.json()
            except Exception as exc:
                wait = 2 ** attempt
                log.warning("Binance GET %s failed (attempt %d/%d): %s — retry in %ds",
                            path, attempt + 1, retries, exc, wait)
                await asyncio.sleep(wait)
        raise RuntimeError(f"Binance REST failed after {retries} retries: {path}")

    async def _post(self, path: str, params: dict = None, retries=3) -> Any:
        await self._limiter.acquire()
        session = await self._session_()
        params  = self._sign(params or {})
        url     = BINANCE_BASE_URL + path
        for attempt in range(retries):
            try:
                async with session.post(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    r.raise_for_status()
                    return await r.json()
            except Exception as exc:
                wait = 2 ** attempt
                log.warning("Binance POST %s attempt %d: %s", path, attempt + 1, exc)
                await asyncio.sleep(wait)
        raise RuntimeError(f"Binance POST failed: {path}")

    # ── Market data ──────────────────────────────────────────────────────────
    async def get_ticker(self, symbol: str) -> Ticker:
        cached_val = cache_get(f"ticker:binance:{symbol}")
        if cached_val:
            return Ticker(**cached_val)
        data = await self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})
        t = Ticker(
            symbol=data["symbol"],
            price=float(data["lastPrice"]),
            change_24h=float(data["priceChangePercent"]),
            volume_24h=float(data["quoteVolume"]),
            high_24h=float(data["highPrice"]),
            low_24h=float(data["lowPrice"]),
        )
        cache_set(f"ticker:binance:{symbol}", t.__dict__, ttl=15)
        return t

    async def get_all_tickers(self) -> List[Ticker]:
        data = await self._get("/fapi/v1/ticker/24hr")
        tickers = []
        for d in data:
            if d["symbol"].endswith(QUOTE_ASSET):
                tickers.append(Ticker(
                    symbol=d["symbol"],
                    price=float(d["lastPrice"]),
                    change_24h=float(d["priceChangePercent"]),
                    volume_24h=float(d["quoteVolume"]),
                    high_24h=float(d["highPrice"]),
                    low_24h=float(d["lowPrice"]),
                ))
        return tickers

    async def get_klines(self, symbol: str, interval: str,
                         limit: int = 200) -> List[Kline]:
        cache_key = f"klines:binance:{symbol}:{interval}"
        cached_val = cache_get(cache_key)
        if cached_val:
            return [Kline(**k) for k in cached_val]
        data = await self._get("/fapi/v1/klines",
                               {"symbol": symbol, "interval": interval, "limit": limit})
        klines = [
            Kline(
                symbol=symbol, interval=interval,
                open_time=int(k[0]), open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]), volume=float(k[5]),
                close_time=int(k[6]), closed=True,
            ) for k in data
        ]
        cache_set(cache_key, [k.__dict__ for k in klines], ttl=20)
        return klines

    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBookDepth:
        data = await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})
        return OrderBookDepth(
            symbol=symbol,
            bids=[[float(p), float(q)] for p, q in data["bids"]],
            asks=[[float(p), float(q)] for p, q in data["asks"]],
        )

    async def get_exchange_info(self) -> dict:
        return await self._get("/fapi/v1/exchangeInfo")

    # ── Account / Orders ─────────────────────────────────────────────────────
    async def get_account(self) -> dict:
        return await self._get("/fapi/v2/account", signed=True)

    async def place_market_order(self, symbol: str, side: str,
                                 quantity: float) -> dict:
        """side: BUY | SELL"""
        params = {
            "symbol": symbol, "side": side,
            "type": "MARKET", "quantity": round(quantity, 6),
        }
        return await self._post("/fapi/v1/order", params)

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        return await self._post("/fapi/v1/leverage",
                                {"symbol": symbol, "leverage": leverage})

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Binance WebSocket manager
# ─────────────────────────────────────────────────────────────────────────────
class BinanceWebSocket:
    """
    Subscribes to multiple streams. Calls registered callbacks on each message.
    Reconnects with exponential backoff on disconnect.
    """
    MAX_BACKOFF = 60

    def __init__(self):
        self._streams: List[str] = []
        self._callbacks: Dict[str, List[Callable]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def subscribe(self, stream: str, callback: Callable) -> None:
        """
        stream examples:
          btcusdt@ticker
          btcusdt@kline_1m
          btcusdt@depth20
        """
        if stream not in self._streams:
            self._streams.append(stream)
        self._callbacks.setdefault(stream, []).append(callback)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        backoff = 1
        while self._running:
            if not self._streams:
                await asyncio.sleep(1)
                continue
            streams_path = "/".join(self._streams)
            url = f"{BINANCE_WS_URL}/stream?streams={streams_path}"
            try:
                async with websockets.connect(url, ping_interval=20,
                                              ping_timeout=30) as ws:
                    log.info("[WS] Connected: %d streams", len(self._streams))
                    backoff = 1
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            stream = msg.get("stream", "")
                            data   = msg.get("data", msg)
                            for cb in self._callbacks.get(stream, []):
                                asyncio.create_task(
                                    cb(data) if asyncio.iscoroutinefunction(cb)
                                    else asyncio.coroutine(cb)(data)
                                )
                        except Exception as exc:
                            log.error("[WS] Message parse error: %s", exc)
            except (ConnectionClosedError, ConnectionClosedOK) as exc:
                log.warning("[WS] Disconnected: %s — reconnect in %ds", exc, backoff)
            except Exception as exc:
                log.error("[WS] Unexpected error: %s — reconnect in %ds", exc, backoff)

            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF)


# ─────────────────────────────────────────────────────────────────────────────
# Bybit REST client (optional)
# ─────────────────────────────────────────────────────────────────────────────
class BybitREST:
    def __init__(self):
        self._limiter = RateLimiter(calls_per_sec=5)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _sign(self, params: dict) -> dict:
        ts = str(int(time.time() * 1000))
        params_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign_str   = ts + BYBIT_API_KEY + "5000" + params_str
        signature  = hmac.new(
            BYBIT_API_SECRET.encode(), sign_str.encode(), hashlib.sha256
        ).hexdigest()
        return {"api_key": BYBIT_API_KEY, "timestamp": ts,
                "recv_window": "5000", "sign": signature, **params}

    async def _get(self, path: str, params: dict = None,
                   signed=False, retries=3) -> Any:
        await self._limiter.acquire()
        session = await self._session_()
        params  = params or {}
        if signed:
            params = self._sign(params)
        url = BYBIT_BASE_URL + path
        for attempt in range(retries):
            try:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=10)) as r:
                    r.raise_for_status()
                    return await r.json()
            except Exception as exc:
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f"Bybit GET failed: {path}")

    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        try:
            data = await self._get("/v5/market/tickers",
                                   {"category": "linear", "symbol": symbol})
            item = data["result"]["list"][0]
            return Ticker(
                symbol=item["symbol"],
                price=float(item["lastPrice"]),
                change_24h=float(item["price24hPcnt"]) * 100,
                volume_24h=float(item["volume24h"]) * float(item["lastPrice"]),
                high_24h=float(item["highPrice24h"]),
                low_24h=float(item["lowPrice24h"]),
            )
        except Exception as exc:
            log.warning("[Bybit] get_ticker %s: %s", symbol, exc)
            return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ─────────────────────────────────────────────────────────────────────────────
# CoinGecko
# ─────────────────────────────────────────────────────────────────────────────
class CoinGeckoClient:
    def __init__(self):
        self._limiter = RateLimiter(calls_per_sec=1)
        self._session: Optional[aiohttp.ClientSession] = None
        headers = {}
        if COINGECKO_API_KEY:
            headers["x-cg-pro-api-key"] = COINGECKO_API_KEY
        self._headers = headers

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._headers)
        return self._session

    async def get_markets(self, ids: List[str] = None,
                          per_page: int = 50) -> List[dict]:
        cache_key = "coingecko:markets"
        cached_val = cache_get(cache_key)
        if cached_val:
            return cached_val
        await self._limiter.acquire()
        session = await self._session_()
        params  = {"vs_currency": "usd", "per_page": per_page,
                   "order": "volume_desc", "sparkline": "false"}
        if ids:
            params["ids"] = ",".join(ids)
        try:
            async with session.get(f"{COINGECKO_BASE}/coins/markets",
                                   params=params,
                                   timeout=aiohttp.ClientTimeout(total=15)) as r:
                r.raise_for_status()
                data = await r.json()
                cache_set(cache_key, data, ttl=120)
                return data
        except Exception as exc:
            log.warning("[CoinGecko] get_markets: %s", exc)
            return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ─────────────────────────────────────────────────────────────────────────────
# On-chain stub (extend with actual APIs: Dune, Etherscan, etc.)
# ─────────────────────────────────────────────────────────────────────────────
class OnChainClient:
    """
    Placeholder for on-chain data.
    Returns normalized scores (0–100) so the rest of the system
    can immediately consume them without waiting for a real integration.
    """

    async def get_whale_activity_score(self, symbol: str) -> float:
        """
        0 = no whale activity, 100 = extreme whale movement.
        Real impl: query Etherscan / Dune / Nansen API.
        """
        import random
        return round(random.uniform(10, 60), 1)

    async def get_liquidity_pool_delta(self, symbol: str) -> float:
        """
        Positive = liquidity added, Negative = removed (%).
        """
        import random
        return round(random.uniform(-5, 5), 2)


# ─────────────────────────────────────────────────────────────────────────────
# Arbitrage detector
# ─────────────────────────────────────────────────────────────────────────────
async def detect_arbitrage(symbol: str,
                            binance: BinanceREST,
                            bybit: BybitREST,
                            threshold_pct: float = 0.3) -> Optional[dict]:
    """Return opportunity dict if spread > threshold, else None."""
    try:
        b_ticker = await binance.get_ticker(symbol)
        y_ticker = await bybit.get_ticker(symbol)
        if b_ticker is None or y_ticker is None:
            return None
        spread = abs(b_ticker.price - y_ticker.price) / b_ticker.price * 100
        if spread >= threshold_pct:
            buy_on  = "binance" if b_ticker.price < y_ticker.price else "bybit"
            sell_on = "bybit"   if buy_on == "binance"             else "binance"
            return {
                "symbol":      symbol,
                "binance_price": b_ticker.price,
                "bybit_price":   y_ticker.price,
                "spread_pct":    round(spread, 4),
                "buy_on":        buy_on,
                "sell_on":       sell_on,
            }
    except Exception as exc:
        log.warning("[Arbitrage] %s: %s", symbol, exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Singleton instances (module-level, lazy-init)
# ─────────────────────────────────────────────────────────────────────────────
binance_rest = BinanceREST()
bybit_rest   = BybitREST()
coingecko    = CoinGeckoClient()
onchain      = OnChainClient()
ws_manager   = BinanceWebSocket()
