"""
analysis.py — Technical analysis engine.
  • Indicators : RSI, EMA, MACD, Bollinger Bands, ATR, momentum
  • Multi-timeframe confluence
  • Liquidity score from order-book depth
  • Signal : LONG / SHORT / WAIT + confidence 0-100
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from api import BinanceREST, Kline, OrderBookDepth, Ticker
from config import TIMEFRAMES, PRIMARY_TF
from logger import get_logger
from models import TradeSignal

log = get_logger("analysis")


# ─────────────────────────────────────────────────────────────────────────────
# Indicator helpers (pure numpy, no external TA library required)
# ─────────────────────────────────────────────────────────────────────────────
def _closes(klines: List[Kline]) -> np.ndarray:
    return np.array([k.close for k in klines], dtype=float)

def _highs(klines: List[Kline]) -> np.ndarray:
    return np.array([k.high for k in klines], dtype=float)

def _lows(klines: List[Kline]) -> np.ndarray:
    return np.array([k.low for k in klines], dtype=float)

def _volumes(klines: List[Kline]) -> np.ndarray:
    return np.array([k.volume for k in klines], dtype=float)


def ema(series: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    result = np.empty_like(series)
    result[:period - 1] = np.nan
    result[period - 1]  = series[:period].mean()
    for i in range(period, len(series)):
        result[i] = series[i] * k + result[i - 1] * (1 - k)
    return result


def rsi(series: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(series)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full(len(series), np.nan)
    avg_loss = np.full(len(series), np.nan)
    avg_gain[period]  = gains[:period].mean()
    avg_loss[period]  = losses[:period].mean()
    for i in range(period + 1, len(series)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss == 0, np.inf, avg_gain / avg_loss)
    return 100 - (100 / (1 + rs))


def macd(series: np.ndarray,
         fast=12, slow=26, signal_period=9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    ema_fast   = ema(series, fast)
    ema_slow   = ema(series, slow)
    macd_line  = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_period)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger(series: np.ndarray, period=20,
              std_dev=2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = np.array([series[i - period:i].mean() if i >= period else np.nan
                    for i in range(1, len(series) + 1)])
    std = np.array([series[i - period:i].std() if i >= period else np.nan
                    for i in range(1, len(series) + 1)])
    return mid - std_dev * std, mid, mid + std_dev * std


def atr(klines: List[Kline], period=14) -> np.ndarray:
    highs  = _highs(klines)
    lows   = _lows(klines)
    closes = _closes(klines)
    tr = np.maximum(highs[1:] - lows[1:],
         np.maximum(np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:]  - closes[:-1])))
    result = np.full(len(klines), np.nan)
    result[period] = tr[:period].mean()
    for i in range(period + 1, len(klines)):
        result[i] = (result[i - 1] * (period - 1) + tr[i - 1]) / period
    return result


def momentum(series: np.ndarray, period=10) -> np.ndarray:
    result = np.full(len(series), np.nan)
    result[period:] = series[period:] / series[:-period] * 100 - 100
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Liquidity score from order-book
# ─────────────────────────────────────────────────────────────────────────────
def liquidity_score(depth: OrderBookDepth, depth_levels: int = 10) -> float:
    """
    Returns 0-100. Considers bid/ask imbalance and total depth in USDT.
    """
    bid_vol = sum(p * q for p, q in depth.bids[:depth_levels])
    ask_vol = sum(p * q for p, q in depth.asks[:depth_levels])
    total   = bid_vol + ask_vol
    if total == 0:
        return 0.0
    balance     = 1 - abs(bid_vol - ask_vol) / total   # 0=imbalanced, 1=balanced
    depth_score = min(total / 500_000, 1.0)             # saturates at 500K USDT
    return round((balance * 0.4 + depth_score * 0.6) * 100, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Single-timeframe analysis result
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TFAnalysis:
    timeframe:   str
    rsi:         float
    ema_9:       float
    ema_21:      float
    macd_hist:   float
    momentum:    float
    bb_pct:      float          # price position within Bollinger band (0-1)
    atr:         float
    volume_ratio: float         # current vs 20-bar avg volume
    trend:       str            # "up" | "down" | "sideways"
    signal:      TradeSignal


# ─────────────────────────────────────────────────────────────────────────────
# Full coin analysis result
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CoinAnalysis:
    symbol:          str
    price:           float
    change_24h:      float
    volume_24h:      float
    market_cap:      float
    liquidity:       float
    whale_score:     float
    sentiment:       float         # -1 to +1
    holder_count:    int
    tf_analyses:     Dict[str, TFAnalysis] = field(default_factory=dict)
    primary_signal:  TradeSignal = TradeSignal.WAIT
    ai_score:        float = 50.0
    stop_loss:       float = 0.0
    take_profit:     float = 0.0
    atr_value:       float = 0.0
    notes:           List[str] = field(default_factory=list)
    # ML feature vector (populated by ai.py)
    features:        Dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Analyzer
# ─────────────────────────────────────────────────────────────────────────────
class TechnicalAnalyzer:

    def analyze_klines(self, klines: List[Kline], timeframe: str) -> Optional[TFAnalysis]:
        if len(klines) < 30:
            log.warning("[Analysis] Not enough klines (%d) for %s", len(klines), timeframe)
            return None

        closes  = _closes(klines)
        volumes = _volumes(klines)

        rsi_vals   = rsi(closes, 14)
        ema9_vals  = ema(closes, 9)
        ema21_vals = ema(closes, 21)
        macd_line, sig_line, hist = macd(closes)
        mom_vals   = momentum(closes, 10)
        bb_lo, bb_mid, bb_hi = bollinger(closes, 20)
        atr_vals   = atr(klines, 14)

        cur_rsi   = float(rsi_vals[-1])  if not math.isnan(rsi_vals[-1])   else 50.0
        cur_ema9  = float(ema9_vals[-1]) if not math.isnan(ema9_vals[-1])  else closes[-1]
        cur_ema21 = float(ema21_vals[-1])if not math.isnan(ema21_vals[-1]) else closes[-1]
        cur_hist  = float(hist[-1])      if not math.isnan(hist[-1])       else 0.0
        cur_mom   = float(mom_vals[-1])  if not math.isnan(mom_vals[-1])   else 0.0
        cur_atr   = float(atr_vals[-1])  if not math.isnan(atr_vals[-1])   else 0.0
        cur_price = closes[-1]

        # Bollinger band position (0=at lower, 1=at upper)
        band_range = float(bb_hi[-1] - bb_lo[-1]) if not math.isnan(bb_hi[-1]) else 1.0
        bb_pct = float((cur_price - bb_lo[-1]) / band_range) if band_range else 0.5

        # Volume ratio vs 20-bar average
        vol_avg   = volumes[-21:-1].mean() if len(volumes) > 21 else volumes.mean()
        vol_ratio = float(volumes[-1] / vol_avg) if vol_avg > 0 else 1.0

        # Trend
        ema_cross = cur_ema9 > cur_ema21
        trend = "up" if ema_cross and cur_mom > 0 else "down" if not ema_cross and cur_mom < 0 else "sideways"

        # Single-TF signal (simple rules)
        signal = self._tf_signal(cur_rsi, cur_ema9, cur_ema21, cur_hist,
                                 cur_mom, bb_pct, vol_ratio, trend)

        return TFAnalysis(
            timeframe=timeframe,
            rsi=round(cur_rsi, 2),
            ema_9=round(cur_ema9, 6),
            ema_21=round(cur_ema21, 6),
            macd_hist=round(cur_hist, 6),
            momentum=round(cur_mom, 2),
            bb_pct=round(bb_pct, 3),
            atr=round(cur_atr, 6),
            volume_ratio=round(vol_ratio, 2),
            trend=trend,
            signal=signal,
        )

    @staticmethod
    def _tf_signal(rsi_v, ema9, ema21, hist, mom, bb_pct, vol_ratio, trend) -> TradeSignal:
        """Rule-based single-TF signal generation."""
        long_score  = 0
        short_score = 0

        if rsi_v < 35:              long_score  += 2
        elif rsi_v > 65:            short_score += 2
        if ema9 > ema21:            long_score  += 1
        else:                       short_score += 1
        if hist > 0:                long_score  += 1
        else:                       short_score += 1
        if mom > 1:                 long_score  += 1
        elif mom < -1:              short_score += 1
        if bb_pct < 0.2:           long_score  += 1
        elif bb_pct > 0.8:         short_score += 1
        if vol_ratio > 1.5:
            if trend == "up":       long_score  += 1
            elif trend == "down":   short_score += 1

        if long_score >= 4:         return TradeSignal.LONG
        if short_score >= 4:        return TradeSignal.SHORT
        return TradeSignal.WAIT

    def multi_tf_signal(self, tf_results: Dict[str, TFAnalysis]) -> TradeSignal:
        """
        Weighted confluence across timeframes.
        Higher timeframes have more weight.
        """
        weights = {"1m": 1, "5m": 2, "15m": 3, "1h": 4}
        long_w = short_w = 0
        for tf, res in tf_results.items():
            w = weights.get(tf, 1)
            if res.signal == TradeSignal.LONG:   long_w  += w
            elif res.signal == TradeSignal.SHORT: short_w += w

        total = long_w + short_w
        if total == 0:
            return TradeSignal.WAIT
        long_pct  = long_w  / total
        short_pct = short_w / total
        if long_pct  >= 0.6: return TradeSignal.LONG
        if short_pct >= 0.6: return TradeSignal.SHORT
        return TradeSignal.WAIT

    def build_feature_vector(self, tf_map: Dict[str, TFAnalysis],
                              liq: float, sentiment: float,
                              whale: float) -> Dict[str, float]:
        """Flatten all indicators into a feature dict for the ML model."""
        feats: Dict[str, float] = {}
        for tf, res in tf_map.items():
            feats[f"{tf}_rsi"]        = res.rsi
            feats[f"{tf}_ema_cross"]  = 1.0 if res.ema_9 > res.ema_21 else -1.0
            feats[f"{tf}_macd_hist"]  = res.macd_hist
            feats[f"{tf}_momentum"]   = res.momentum
            feats[f"{tf}_bb_pct"]     = res.bb_pct
            feats[f"{tf}_vol_ratio"]  = res.volume_ratio
            feats[f"{tf}_trend"]      = {"up": 1.0, "sideways": 0.0, "down": -1.0}.get(res.trend, 0.0)
        feats["liquidity"]   = liq
        feats["sentiment"]   = sentiment
        feats["whale_score"] = whale
        return feats


# Module-level singleton
analyzer = TechnicalAnalyzer()
