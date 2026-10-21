from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trade_pl.metrics import METRICS_VERSION


def _percentile_rank(s: pd.Series, ascending: bool = True) -> pd.Series:
    return s.rank(pct=True, ascending=ascending, method="average")


def score_summary(df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    weights = weights or {
        "return_3m": 0.35,
        "return_1m": 0.15,
        "sharpe": 0.25,
        "low_drawdown": 0.15,
        "liquidity": 0.10,
    }
    out = df.copy()
    out["p_return_3m"] = _percentile_rank(out["return_3m"].fillna(-1))
    out["p_return_1m"] = _percentile_rank(out["return_1m"].fillna(-1))
    out["p_sharpe"] = _percentile_rank(out["sharpe_60d"].fillna(-10))
    # less negative drawdown is better
    out["p_low_drawdown"] = _percentile_rank(out["max_drawdown"].fillna(-1), ascending=True)
    out["p_liquidity"] = _percentile_rank(np.log1p(out["avg_volume_20d"].fillna(0)))
    out["score"] = (
        weights.get("return_3m", 0) * out["p_return_3m"]
        + weights.get("return_1m", 0) * out["p_return_1m"]
        + weights.get("sharpe", 0) * out["p_sharpe"]
        + weights.get("low_drawdown", 0) * out["p_low_drawdown"]
        + weights.get("liquidity", 0) * out["p_liquidity"]
    )
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    return out


def write_leaderboard(df: pd.DataFrame, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "leaderboard.csv"
    df.to_csv(path, index=False)
    return path


def write_suggested_pick(df: pd.DataFrame, report_dir: Path, weights: dict[str, float]) -> Path | None:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "suggested_pick.json"
    if df.empty:
        path.write_text(json.dumps({"error": "no candidates"}, indent=2), encoding="utf-8")
        return path
    top = df.iloc[0]
    payload: dict[str, Any] = {
        "as_of": date.today().isoformat(),
        "metrics_version": METRICS_VERSION,
        "ticker": top["symbol"],
        "score": float(top["score"]),
        "rank": int(top["rank"]),
        "label": "model suggested candidate — not financial advice",
        "metric_breakdown": {
            "return_1m": float(top["return_1m"]),
            "return_3m": float(top["return_3m"]),
            "sharpe_60d": float(top["sharpe_60d"]),
            "max_drawdown": float(top["max_drawdown"]),
            "rsi_14": float(top["rsi_14"]),
            "last_price": float(top["last_price"]),
            "avg_volume_20d": float(top["avg_volume_20d"]),
        },
        "weights": weights,
        "disclaimer": (
            "This is a research ranking from deterministic factors. "
            "It is not a guarantee of next-month performance and not investment advice."
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
