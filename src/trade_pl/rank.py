from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trade_pl.metrics import METRICS_VERSION

HORIZONS: dict[str, str] = {
    "week": "return_1w",
    "month": "return_1m",
    "quarter": "return_3m",
    "half_year": "return_6m",
    "year": "return_1y",
}


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
    if "sector" not in out.columns:
        out["sector"] = "Unknown"
    out["p_return_3m"] = _percentile_rank(out["return_3m"].fillna(-1))
    out["p_return_1m"] = _percentile_rank(out["return_1m"].fillna(-1))
    out["p_sharpe"] = _percentile_rank(out["sharpe_60d"].fillna(-10))
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


def _top_rows(df: pd.DataFrame, col: str, n: int = 25) -> list[dict[str, Any]]:
    if df.empty or col not in df.columns:
        return []
    view = df.dropna(subset=[col]).sort_values(col, ascending=False).head(n)
    cols = [
        "symbol",
        "sector",
        "market",
        "rank",
        "score",
        col,
        "return_1d",
        "return_1w",
        "return_1m",
        "return_3m",
        "return_6m",
        "return_1y",
        "sharpe_60d",
        "max_drawdown",
        "last_price",
    ]
    # preserve order, drop duplicates (col may already be in the list)
    seen: set[str] = set()
    uniq: list[str] = []
    for c in cols:
        if c in view.columns and c not in seen:
            seen.add(c)
            uniq.append(c)
    return view.loc[:, uniq].to_dict(orient="records")


def build_horizon_leaders(df: pd.DataFrame, n: int = 25) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "as_of": date.today().isoformat(),
        "metrics_version": METRICS_VERSION,
        "horizons": {},
    }
    for name, col in HORIZONS.items():
        top = _top_rows(df, col, n=n)
        payload["horizons"][name] = {
            "metric": col,
            "label": name.replace("_", " ").title(),
            "top": top,
            "best": top[0] if top else None,
        }
    return payload


def build_sector_leaders(df: pd.DataFrame, n: int = 15) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "as_of": date.today().isoformat(),
        "metrics_version": METRICS_VERSION,
        "sectors": {},
    }
    if df.empty or "sector" not in df.columns:
        return payload
    for sector, group in df.groupby("sector"):
        ranked = group.sort_values("score", ascending=False).head(n)
        by_month = group.dropna(subset=["return_1m"]).sort_values("return_1m", ascending=False).head(5)
        payload["sectors"][str(sector)] = {
            "count": int(len(group)),
            "top_by_score": ranked[
                [c for c in ["symbol", "rank", "score", "return_1m", "return_3m", "return_1y", "sharpe_60d", "last_price"] if c in ranked.columns]
            ].to_dict(orient="records"),
            "best_month": by_month[["symbol", "return_1m", "score", "rank"]].to_dict(orient="records")
            if not by_month.empty
            else [],
        }
    return payload


def write_leaderboard(df: pd.DataFrame, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "leaderboard.csv"
    df.to_csv(path, index=False)
    return path


def write_horizon_leaders(df: pd.DataFrame, report_dir: Path, n: int = 25) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "leaders_by_horizon.json"
    path.write_text(json.dumps(build_horizon_leaders(df, n=n), indent=2, default=float), encoding="utf-8")
    return path


def write_sector_leaders(df: pd.DataFrame, report_dir: Path, n: int = 15) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "leaders_by_sector.json"
    path.write_text(json.dumps(build_sector_leaders(df, n=n), indent=2, default=float), encoding="utf-8")
    return path


def write_suggested_pick(df: pd.DataFrame, report_dir: Path, weights: dict[str, float]) -> Path | None:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "suggested_pick.json"
    if df.empty:
        path.write_text(json.dumps({"error": "no candidates"}, indent=2), encoding="utf-8")
        return path
    top = df.iloc[0]
    horizons = build_horizon_leaders(df, n=1)
    payload: dict[str, Any] = {
        "as_of": date.today().isoformat(),
        "metrics_version": METRICS_VERSION,
        "ticker": top["symbol"],
        "sector": top.get("sector", "Unknown"),
        "score": float(top["score"]),
        "rank": int(top["rank"]),
        "label": "model suggested candidate — not financial advice",
        "metric_breakdown": {
            "return_1w": float(top.get("return_1w", float("nan"))),
            "return_1m": float(top["return_1m"]),
            "return_3m": float(top["return_3m"]),
            "return_6m": float(top.get("return_6m", float("nan"))),
            "return_1y": float(top.get("return_1y", float("nan"))),
            "sharpe_60d": float(top["sharpe_60d"]),
            "max_drawdown": float(top["max_drawdown"]),
            "rsi_14": float(top["rsi_14"]),
            "last_price": float(top["last_price"]),
            "avg_volume_20d": float(top["avg_volume_20d"]),
        },
        "best_by_horizon": {
            name: (data.get("best") or {}).get("symbol") for name, data in horizons["horizons"].items()
        },
        "weights": weights,
        "disclaimer": (
            "This is a research ranking from deterministic factors. "
            "It is not a guarantee of next-month performance and not investment advice."
        ),
    }
    path.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")
    return path
