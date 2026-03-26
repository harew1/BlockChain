"""
scanner.py — Market Scanner.
  • Fetches all USDT futures tickers from Binance
  • Filters by volume, liquidity, volatility
  • Enriches each coin with CoinGecko market-cap data, on-chain data
  • Builds a full CoinProfile for each top-N coin
  • Detects arbitrage opportunities across exchanges
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from api import (
    BinanceREST, BybitREST, CoinGeckoClient, OnChainClient,
    binance_rest, bybit_rest, coingecko, onchain,
    detect_arbitrage, Ticker,
)
from analysis import analyzer, CoinAnalysis, TFAnalysis
from config import (
    TIMEFRAMES, MIN_VOLUME_USDT, MIN_LIQUIDITY_SCORE,
    MIN_VOLATILITY_PCT, DEFAULT_TOP_N_COINS, ARBITRAGE_THRESHOLD,
)
from logger import get_logger

log = get_logger("scanner")


# ─────────────────────────────────────────────────────────────────────────────
# CoinProfile — richer than Ticker, includes all data sources
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CoinProfile:
    symbol:         str
    price:          float
    change_24h:     float
    volume_24h:     float
    high_24h:       float
    low_24h:        float
    market_cap:     float = 0.0
    circulating:    float = 0.0
    liquidity:      float = 0.0
    whale_score:    float = 0.0
    pool_delta:     float = 0.0
    sentiment:      float = 0.0    # placeholder, can hook into Twitter/Reddit
    holder_count:   int   = 0
    volatility_pct: float = 0.0
    analysis:       Optional[CoinAnalysis] = None
    ts:             float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = {
            "symbol":       self.symbol,
            "price":        self.price,
            "change_24h":   self.change_24h,
            "volume_24h":   self.volume_24h,
            "market_cap":   self.market_cap,
            "liquidity":    self.liquidity,
            "whale_score":  self.whale_score,
            "sentiment":    self.sentiment,
            "volatility":   self.volatility_pct,
            "ts":           self.ts,
        }
        if self.analysis:
            d["signal"]   = self.analysis.primary_signal.value
            d["ai_score"] = self.analysis.ai_score
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Volatility from ticker
# ─────────────────────────────────────────────────────────────────────────────
def _volatility(high: float, low: float, price: float) -> float:
    if price == 0:
        return 0.0
    return round((high - low) / price * 100, 2)


# ─────────────────────────────────────────────────────────────────────────────
# CoinGecko id lookup (simple mapping for major coins)
# ─────────────────────────────────────────────────────────────────────────────
_CG_ID: Dict[str, str] = {
    "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "BNBUSDT": "binancecoin",
    "SOLUSDT": "solana",  "ADAUSDT": "cardano",  "XRPUSDT": "ripple",
    "AVAXUSDT": "avalanche-2", "DOTUSDT": "polkadot", "LINKUSDT": "chainlink",
    "MATICUSDT": "matic-network",
}

def _cg_id(symbol: str) -> Optional[str]:
    return _CG_ID.get(symbol)


# ─────────────────────────────────────────────────────────────────────────────
# Market Scanner
# ─────────────────────────────────────────────────────────────────────────────
class MarketScanner:

    def __init__(self,
                 top_n: int               = DEFAULT_TOP_N_COINS,
                 min_volume: float        = MIN_VOLUME_USDT,
                 min_liquidity: float     = MIN_LIQUIDITY_SCORE,
                 min_volatility: float    = MIN_VOLATILITY_PCT,
                 arb_threshold: float     = ARBITRAGE_THRESHOLD,
                 exchange: str            = "binance"):

        self.top_n         = top_n
        self.min_volume    = min_volume
        self.min_liquidity = min_liquidity
        self.min_volatility= min_volatility
        self.arb_threshold = arb_threshold
        self.exchange      = exchange
        self._last_profiles: List[CoinProfile] = []
        self._arb_opps: List[dict] = []

    # ── Step 1: Filter tickers ────────────────────────────────────────────────
    async def _filter_tickers(self) -> List[Ticker]:
        tickers = await binance_rest.get_all_tickers()
        filtered = []
        for t in tickers:
            vol   = t.volume_24h
            vol_p = _volatility(t.high_24h, t.low_24h, t.price)
            if vol < self.min_volume:
                continue
            if vol_p < self.min_volatility:
                continue
            filtered.append(t)
        # Sort by volume descending, take top N
        filtered.sort(key=lambda t: t.volume_24h, reverse=True)
        return filtered[:self.top_n * 3]   # over-fetch, liquidity filter next

    # ── Step 2: Enrich with liquidity from order book ─────────────────────────
    async def _enrich_liquidity(self, tickers: List[Ticker]) -> List[tuple]:
        """Returns list of (ticker, liquidity_score)."""
        results = []
        sem = asyncio.Semaphore(5)

        async def fetch_one(ticker: Ticker):
            async with sem:
                try:
                    depth = await binance_rest.get_orderbook(ticker.symbol, limit=20)
                    from analysis import liquidity_score as _liq_score
                    liq = _liq_score(depth)
                except Exception:
                    liq = 0.0
                results.append((ticker, liq))

        await asyncio.gather(*[fetch_one(t) for t in tickers])
        return [(t, l) for t, l in results if l >= self.min_liquidity]

    # ── Step 3: Build full analysis per coin ──────────────────────────────────
    async def _analyze_coin(self, ticker: Ticker, liq: float,
                             cg_map: Dict[str, dict]) -> CoinProfile:
        # CoinGecko data
        cg   = cg_map.get(_cg_id(ticker.symbol) or "", {})
        mcap = cg.get("market_cap", 0) or 0
        circ = cg.get("circulating_supply", 0) or 0

        # On-chain
        whale = await onchain.get_whale_activity_score(ticker.symbol)
        pool  = await onchain.get_liquidity_pool_delta(ticker.symbol)

        profile = CoinProfile(
            symbol=ticker.symbol,
            price=ticker.price,
            change_24h=ticker.change_24h,
            volume_24h=ticker.volume_24h,
            high_24h=ticker.high_24h,
            low_24h=ticker.low_24h,
            market_cap=float(mcap),
            circulating=float(circ),
            liquidity=liq,
            whale_score=whale,
            pool_delta=pool,
            sentiment=self._mock_sentiment(ticker.symbol),
            volatility_pct=_volatility(ticker.high_24h, ticker.low_24h, ticker.price),
        )

        # Technical analysis per timeframe
        tf_map: Dict[str, TFAnalysis] = {}
        for tf in TIMEFRAMES:
            try:
                klines = await binance_rest.get_klines(ticker.symbol, tf, limit=100)
                result = analyzer.analyze_klines(klines, tf)
                if result:
                    tf_map[tf] = result
            except Exception as exc:
                log.debug("[Scanner] klines %s %s: %s", ticker.symbol, tf, exc)

        # Aggregate signal
        primary_signal = analyzer.multi_tf_signal(tf_map)
        features = analyzer.build_feature_vector(tf_map, liq, profile.sentiment, whale)

        from ai import ai_model
        ai_score = ai_model.predict_score(features)

        # ATR for SL/TP
        atr_val = 0.0
        pf_tf   = tf_map.get("15m") or (list(tf_map.values())[0] if tf_map else None)
        if pf_tf:
            atr_val = pf_tf.atr

        analysis = CoinAnalysis(
            symbol=ticker.symbol,
            price=ticker.price,
            change_24h=ticker.change_24h,
            volume_24h=ticker.volume_24h,
            market_cap=float(mcap),
            liquidity=liq,
            whale_score=whale,
            sentiment=profile.sentiment,
            holder_count=0,
            tf_analyses=tf_map,
            primary_signal=primary_signal,
            ai_score=ai_score,
            atr_value=atr_val,
            features=features,
        )
        profile.analysis = analysis
        return profile

    @staticmethod
    def _mock_sentiment(symbol: str) -> float:
        """Placeholder — replace with Twitter/Reddit API."""
        import random
        return round(random.uniform(-0.5, 0.5), 2)

    # ── Step 4: Arbitrage check ───────────────────────────────────────────────
    async def _check_arbitrage(self, symbols: List[str]) -> List[dict]:
        opps = []
        sem  = asyncio.Semaphore(3)

        async def check_one(sym: str):
            async with sem:
                opp = await detect_arbitrage(sym, binance_rest, bybit_rest,
                                             self.arb_threshold)
                if opp:
                    opps.append(opp)
                    log.info("[Arb] %s: spread=%.3f%%", sym, opp["spread_pct"])

        await asyncio.gather(*[check_one(s) for s in symbols])
        return sorted(opps, key=lambda x: x["spread_pct"], reverse=True)

    # ── Main scan ─────────────────────────────────────────────────────────────
    async def scan(self) -> List[CoinProfile]:
        log.info("[Scanner] Starting market scan…")
        t0 = time.time()

        # 1. Filter
        tickers = await self._filter_tickers()
        log.info("[Scanner] After volume/volatility filter: %d coins", len(tickers))

        # 2. Liquidity
        enriched = await self._enrich_liquidity(tickers)
        enriched.sort(key=lambda x: x[1], reverse=True)
        enriched = enriched[:self.top_n]
        log.info("[Scanner] After liquidity filter: %d coins", len(enriched))

        # 3. CoinGecko batch
        cg_ids = [_cg_id(t.symbol) for t, _ in enriched if _cg_id(t.symbol)]
        try:
            cg_list = await coingecko.get_markets(ids=cg_ids or None, per_page=50)
            cg_map  = {item["id"]: item for item in cg_list}
        except Exception:
            cg_map = {}

        # 4. Full analysis (parallel with semaphore)
        sem      = asyncio.Semaphore(4)
        profiles = []

        async def analyze_one(ticker, liq):
            async with sem:
                try:
                    p = await self._analyze_coin(ticker, liq, cg_map)
                    profiles.append(p)
                except Exception as exc:
                    log.error("[Scanner] analyze_coin %s: %s", ticker.symbol, exc)

        await asyncio.gather(*[analyze_one(t, l) for t, l in enriched])

        # 5. Arbitrage
        symbols = [p.symbol for p in profiles]
        self._arb_opps = await self._check_arbitrage(symbols[:10])

        # Sort by AI score
        profiles.sort(key=lambda p: (p.analysis.ai_score if p.analysis else 0), reverse=True)
        self._last_profiles = profiles

        elapsed = time.time() - t0
        log.info("[Scanner] Scan complete: %d profiles in %.1fs", len(profiles), elapsed)
        return profiles

    @property
    def last_profiles(self) -> List[CoinProfile]:
        return self._last_profiles

    @property
    def arbitrage_opportunities(self) -> List[dict]:
        return self._arb_opps


# Module-level singleton
scanner = MarketScanner()
