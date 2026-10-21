"""Wall Street–style floor board: gainers, losers, volume, sector heat."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from trade_pl.sectors import market_of


def _ret_col(df: pd.DataFrame) -> str:
    for col in ("return_1d", "return_1w", "return_1m"):
        if col in df.columns and df[col].notna().any():
            return col
    return "return_1w" if "return_1w" in df.columns else "score"


def _rows(df: pd.DataFrame, n: int = 15) -> list[dict[str, Any]]:
    if df.empty:
        return []
    cols = [
        c
        for c in (
            "symbol",
            "sector",
            "market",
            "last_price",
            "return_1d",
            "return_1w",
            "return_1m",
            "rel_volume",
            "avg_volume_20d",
            "rsi_14",
            "rank",
            "score",
        )
        if c in df.columns
    ]
    out = df.loc[:, cols].copy()
    records = out.to_dict(orient="records")
    for r in records:
        for k, v in list(r.items()):
            if v is None:
                continue
            try:
                if pd.isna(v):
                    r[k] = None
                elif hasattr(v, "item"):
                    r[k] = v.item()
            except Exception:
                pass
        if not r.get("market") and r.get("symbol"):
            r["market"] = market_of(str(r["symbol"]))
    return records[:n]


def build_floor(df: pd.DataFrame, n: int = 15) -> dict[str, Any]:
    """Build a desk snapshot from the latest leaderboard / summary frame."""
    if df is None or df.empty:
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "ret_metric": "return_1w",
            "gainers": [],
            "losers": [],
            "active": [],
            "momentum": [],
            "sector_heat": [],
            "market_heat": [],
            "tape": [],
            "counts": {"scored": 0},
        }

    view = df.copy()
    if "market" not in view.columns:
        view["market"] = view["symbol"].astype(str).map(market_of)

    ret = _ret_col(view)
    gainers = view.dropna(subset=[ret]).sort_values(ret, ascending=False).head(n)
    losers = view.dropna(subset=[ret]).sort_values(ret, ascending=True).head(n)

    if "rel_volume" in view.columns and view["rel_volume"].notna().any():
        active = view.dropna(subset=["rel_volume"]).sort_values("rel_volume", ascending=False).head(n)
    elif "avg_volume_20d" in view.columns:
        active = view.dropna(subset=["avg_volume_20d"]).sort_values("avg_volume_20d", ascending=False).head(n)
    else:
        active = view.head(n)

    if "rsi_14" in view.columns and view["rsi_14"].notna().any():
        # Near extremes = desk attention
        mom = view.dropna(subset=["rsi_14"]).copy()
        mom["_rsi_edge"] = (mom["rsi_14"] - 50.0).abs()
        momentum = mom.sort_values("_rsi_edge", ascending=False).head(n)
    else:
        momentum = gainers

    sector_heat: list[dict[str, Any]] = []
    if "sector" in view.columns and ret in view.columns:
        g = (
            view.dropna(subset=[ret, "sector"])
            .groupby("sector", as_index=False)
            .agg(avg_return=(ret, "mean"), n=("symbol", "count"), median_return=(ret, "median"))
            .sort_values("avg_return", ascending=False)
        )
        sector_heat = g.to_dict(orient="records")

    market_heat: list[dict[str, Any]] = []
    if ret in view.columns:
        g = (
            view.dropna(subset=[ret, "market"])
            .groupby("market", as_index=False)
            .agg(avg_return=(ret, "mean"), n=("symbol", "count"))
            .sort_values("avg_return", ascending=False)
        )
        market_heat = g.to_dict(orient="records")

    # Scrolling tape: mix top movers
    tape_src = pd.concat([gainers.head(8), losers.head(8), active.head(8)], ignore_index=True)
    if not tape_src.empty and "symbol" in tape_src.columns:
        tape_src = tape_src.drop_duplicates(subset=["symbol"]).head(24)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ret_metric": ret,
        "gainers": _rows(gainers, n),
        "losers": _rows(losers, n),
        "active": _rows(active, n),
        "momentum": _rows(momentum, n),
        "sector_heat": sector_heat,
        "market_heat": market_heat,
        "tape": _rows(tape_src, 24),
        "counts": {
            "scored": int(len(view)),
            "sectors": int(view["sector"].nunique()) if "sector" in view.columns else 0,
            "markets": int(view["market"].nunique()) if "market" in view.columns else 0,
        },
    }
