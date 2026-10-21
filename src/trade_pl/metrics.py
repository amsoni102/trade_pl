from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

METRICS_VERSION = "metrics_v1"


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _max_drawdown(cum: pd.Series) -> float:
    peak = cum.cummax()
    dd = cum / peak - 1.0
    return float(dd.min()) if len(dd) else float("nan")


def compute_series(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    px = out["adj_close"].astype(float)
    out["return_1d"] = px.pct_change()
    out["cum_return"] = (1 + out["return_1d"].fillna(0)).cumprod() - 1
    out["sma_20"] = px.rolling(20).mean()
    out["sma_50"] = px.rolling(50).mean()
    out["sma_200"] = px.rolling(200).mean()
    out["ema_12"] = px.ewm(span=12, adjust=False).mean()
    out["ema_26"] = px.ewm(span=26, adjust=False).mean()
    out["macd"] = out["ema_12"] - out["ema_26"]
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    out["rsi_14"] = _rsi(px, 14)
    out["vol_20"] = out["return_1d"].rolling(20).std() * np.sqrt(252)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - out["close"].shift()).abs(),
            (out["low"] - out["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr_14"] = tr.rolling(14).mean()
    mid = px.rolling(20).mean()
    std = px.rolling(20).std()
    out["bb_width"] = (2 * std) / mid
    out["volume_sma_20"] = out["volume"].rolling(20).mean()
    out["rel_volume"] = out["volume"] / out["volume_sma_20"]
    peak = out["cum_return"].cummax()
    out["drawdown"] = out["cum_return"] / peak.replace(0, np.nan) - 1.0
    # fix: drawdown should be from equity curve of price
    equity = px / px.iloc[0]
    peak_eq = equity.cummax()
    out["drawdown"] = equity / peak_eq - 1.0
    return out


def summarize(symbol: str, series: pd.DataFrame, min_history_days: int = 120) -> dict[str, Any] | None:
    if series is None or len(series) < min_history_days:
        return None
    px = series["adj_close"].astype(float)
    rets = series["return_1d"].dropna()

    def window_return(n: int) -> float:
        if len(px) <= n:
            return float("nan")
        return float(px.iloc[-1] / px.iloc[-n - 1] - 1)

    vol = float(rets.tail(60).std() * np.sqrt(252)) if len(rets) >= 20 else float("nan")
    mean_d = float(rets.tail(60).mean()) if len(rets) >= 20 else float("nan")
    sharpe = float(mean_d / rets.tail(60).std() * np.sqrt(252)) if len(rets) >= 20 and rets.tail(60).std() > 0 else float("nan")
    equity = px / px.iloc[0]
    max_dd = _max_drawdown(equity)
    avg_vol = float(series["volume"].tail(20).mean())
    last = series.iloc[-1]
    return {
        "symbol": symbol,
        "as_of": str(pd.Timestamp(series["date"].iloc[-1]).date()),
        "last_price": float(px.iloc[-1]),
        "return_1m": window_return(21),
        "return_3m": window_return(63),
        "return_6m": window_return(126),
        "return_1y": window_return(252),
        "rsi_14": float(last["rsi_14"]) if pd.notna(last.get("rsi_14")) else float("nan"),
        "vol_20": float(last["vol_20"]) if pd.notna(last.get("vol_20")) else vol,
        "atr_14": float(last["atr_14"]) if pd.notna(last.get("atr_14")) else float("nan"),
        "sharpe_60d": sharpe,
        "max_drawdown": max_dd,
        "avg_volume_20d": avg_vol,
        "rel_volume": float(last["rel_volume"]) if pd.notna(last.get("rel_volume")) else float("nan"),
        "above_sma_50": bool(px.iloc[-1] > last["sma_50"]) if pd.notna(last.get("sma_50")) else False,
        "above_sma_200": bool(px.iloc[-1] > last["sma_200"]) if pd.notna(last.get("sma_200")) else False,
        "metrics_version": METRICS_VERSION,
        "n_bars": int(len(series)),
    }


def compute_for_symbol(
    symbol: str,
    raw_dir: Path,
    metrics_dir: Path,
    min_history_days: int = 120,
) -> dict[str, Any] | None:
    path = raw_dir / f"{symbol.replace('/', '-')}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    series = compute_series(df)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    series.to_parquet(metrics_dir / f"{symbol}_series.parquet", index=False)
    return summarize(symbol, series, min_history_days=min_history_days)


def compute_many(
    symbols: list[str],
    raw_dir: Path,
    metrics_dir: Path,
    min_history_days: int = 120,
    min_price: float = 5.0,
    min_avg_volume: float = 500_000,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for sym in symbols:
        summary = compute_for_symbol(sym, raw_dir, metrics_dir, min_history_days)
        if not summary:
            continue
        if summary["last_price"] < min_price:
            continue
        if summary["avg_volume_20d"] < min_avg_volume:
            continue
        rows.append(summary)
    out = pd.DataFrame(rows)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    summary_path = metrics_dir / "summary.csv"
    if not out.empty:
        out.to_csv(summary_path, index=False)
    else:
        summary_path.write_text("", encoding="utf-8")
    return out
