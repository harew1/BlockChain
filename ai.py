"""
ai.py — Adaptive AI scoring system.
  • Ensemble of GradientBoosting + RandomForest classifiers
  • Online learning: retrains when enough new trade outcomes arrive
  • Model persistence via joblib
  • Feature importance logging
  • Converts raw ML probability → AI score 0-100
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import joblib
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from config import MODEL_PATH, SCALER_PATH, MIN_TRAIN_SAMPLES
from logger import get_logger
from models import TradeSignal

log = get_logger("ai")

# Persistent buffer file for unlabeled + labeled samples
_SAMPLE_BUFFER = Path(MODEL_PATH).parent / "sample_buffer.json"


# ─────────────────────────────────────────────────────────────────────────────
# Feature order — must be stable across save/load
# ─────────────────────────────────────────────────────────────────────────────
TF_LIST       = ["1m", "5m", "15m", "1h"]
BASE_FEATURES = [f"{tf}_{ind}" for tf in TF_LIST
                 for ind in ("rsi", "ema_cross", "macd_hist",
                             "momentum", "bb_pct", "vol_ratio", "trend")]
EXTRA_FEATURES = ["liquidity", "sentiment", "whale_score"]
FEATURE_ORDER  = BASE_FEATURES + EXTRA_FEATURES


def _feature_vec(feat_dict: Dict[str, float]) -> np.ndarray:
    return np.array([feat_dict.get(k, 0.0) for k in FEATURE_ORDER], dtype=float)


# ─────────────────────────────────────────────────────────────────────────────
# Sample buffer (persisted to disk between restarts)
# ─────────────────────────────────────────────────────────────────────────────
class SampleBuffer:
    def __init__(self, path: Path):
        self._path = path
        self._data: List[dict] = self._load()

    def _load(self) -> List[dict]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                return []
        return []

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, default=str))

    def add_pending(self, trade_id: int, features: Dict[str, float]) -> None:
        self._data.append({"trade_id": trade_id, "features": features,
                           "outcome": None, "ts": time.time()})
        self._save()

    def label(self, trade_id: int, outcome: int) -> None:
        """outcome: 1=win, 0=loss"""
        for sample in self._data:
            if sample["trade_id"] == trade_id:
                sample["outcome"] = outcome
                break
        self._save()

    def labeled(self) -> List[dict]:
        return [s for s in self._data if s["outcome"] is not None]

    def __len__(self) -> int:
        return len(self._data)


# ─────────────────────────────────────────────────────────────────────────────
# Ensemble classifier
# ─────────────────────────────────────────────────────────────────────────────
class EnsembleSignalModel:
    """
    Two-model soft-voting ensemble.
    Label: 1 = profitable trade, 0 = losing trade.
    predict_proba → probability of profit → AI score (0-100).
    """

    def __init__(self):
        self._gb  = Pipeline([
            ("scaler", StandardScaler()),
            ("clf",    GradientBoostingClassifier(
                n_estimators=100, learning_rate=0.05,
                max_depth=3, subsample=0.8, random_state=42,
            )),
        ])
        self._rf  = Pipeline([
            ("scaler", StandardScaler()),
            ("clf",    RandomForestClassifier(
                n_estimators=100, max_depth=5,
                min_samples_leaf=3, random_state=42,
            )),
        ])
        self._fitted = False
        self._buffer = SampleBuffer(_SAMPLE_BUFFER)
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        if Path(MODEL_PATH).exists() and Path(SCALER_PATH).exists():
            try:
                self._gb, self._rf = joblib.load(MODEL_PATH)
                self._fitted = True
                log.info("[AI] Model loaded from disk.")
            except Exception as exc:
                log.warning("[AI] Could not load model: %s", exc)

    def _save(self) -> None:
        Path(MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump((self._gb, self._rf), MODEL_PATH)
        log.info("[AI] Model saved to disk.")

    # ── Training ─────────────────────────────────────────────────────────────
    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._gb.fit(X, y)
        self._rf.fit(X, y)
        self._fitted = True
        self._save()
        self._log_feature_importance()

    def _log_feature_importance(self) -> None:
        try:
            importances = self._gb["clf"].feature_importances_
            ranked = sorted(zip(FEATURE_ORDER, importances),
                            key=lambda x: x[1], reverse=True)[:10]
            log.info("[AI] Top-10 feature importances: %s",
                     {k: round(v, 4) for k, v in ranked})
        except Exception:
            pass

    # ── Prediction ────────────────────────────────────────────────────────────
    def predict_score(self, features: Dict[str, float]) -> float:
        """Return 0-100 AI score. Falls back to rule-based heuristic if untrained."""
        if not self._fitted:
            return self._heuristic_score(features)
        X = _feature_vec(features).reshape(1, -1)
        try:
            p_gb = self._gb.predict_proba(X)[0][1]
            p_rf = self._rf.predict_proba(X)[0][1]
            prob = (p_gb + p_rf) / 2
            return round(prob * 100, 1)
        except Exception as exc:
            log.warning("[AI] predict_proba error: %s", exc)
            return self._heuristic_score(features)

    @staticmethod
    def _heuristic_score(features: Dict[str, float]) -> float:
        """Rule-based fallback while model isn't trained yet."""
        score = 50.0
        rsi_15  = features.get("15m_rsi", 50)
        cross   = features.get("15m_ema_cross", 0)
        hist    = features.get("15m_macd_hist", 0)
        liq     = features.get("liquidity", 50)
        whale   = features.get("whale_score", 20)
        sent    = features.get("sentiment", 0)

        score += (50 - rsi_15) * 0.3
        score += cross * 5
        score += min(max(hist * 1000, -10), 10)
        score += (liq - 50) * 0.1
        score += whale * 0.1
        score += sent * 5
        return round(max(0.0, min(100.0, score)), 1)

    # ── Online learning ───────────────────────────────────────────────────────
    def record_signal(self, trade_id: int,
                      features: Dict[str, float]) -> None:
        self._buffer.add_pending(trade_id, features)

    def record_outcome(self, trade_id: int, win: bool) -> None:
        self._buffer.label(trade_id, int(win))
        self._maybe_retrain()

    def _maybe_retrain(self) -> None:
        labeled = self._buffer.labeled()
        if len(labeled) < MIN_TRAIN_SAMPLES:
            log.info("[AI] Not enough samples (%d/%d) to retrain.",
                     len(labeled), MIN_TRAIN_SAMPLES)
            return
        X = np.array([_feature_vec(s["features"]) for s in labeled])
        y = np.array([s["outcome"] for s in labeled])
        log.info("[AI] Retraining on %d samples…", len(X))
        self.fit(X, y)

    # ── Dynamic risk adjustment ───────────────────────────────────────────────
    def dynamic_position_size(self, ai_score: float, base_risk_pct: float) -> float:
        """
        Strong signal (score ≥ 70) → up to 2× base risk.
        Weak signal  (score ≤ 40) → 0.5× base risk.
        """
        if ai_score >= 70:
            multiplier = 1.0 + (ai_score - 70) / 30  # 1.0 → 2.0
        elif ai_score <= 40:
            multiplier = 0.5
        else:
            multiplier = (ai_score - 40) / 30 * 0.5 + 0.5  # 0.5 → 1.0
        return round(base_risk_pct * multiplier, 3)


# Module-level singleton
ai_model = EnsembleSignalModel()
