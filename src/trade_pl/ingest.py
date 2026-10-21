from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
    )
    df = df.reset_index()
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    df = df.rename(columns={date_col: "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]
    cols = ["date", "open", "high", "low", "close", "volume", "adj_close"]
    df = df[cols].dropna(subset=["close"])
    df = df.sort_values("date").drop_duplicates("date")
    return df.reset_index(drop=True)


def ticker_path(raw_dir: Path, symbol: str) -> Path:
    safe = symbol.replace("/", "-")
    return raw_dir / f"{safe}.parquet"


def load_cached(raw_dir: Path, symbol: str) -> pd.DataFrame | None:
    path = ticker_path(raw_dir, symbol)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def download_ticker(symbol: str, lookback_days: int) -> pd.DataFrame:
    end = datetime.utcnow().date() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    df = yf.download(
        symbol,
        start=start.isoformat(),
        end=end.isoformat(),
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    return _normalize_ohlcv(df)


def ingest_one(
    symbol: str,
    raw_dir: Path,
    lookback_days: int,
    force: bool = False,
) -> tuple[str, bool, str]:
    path = ticker_path(raw_dir, symbol)
    if path.exists() and not force:
        # Skip if updated today
        mtime = datetime.fromtimestamp(path.stat().st_mtime).date()
        if mtime >= date.today():
            return symbol, True, "cache-fresh"
    try:
        df = download_ticker(symbol, lookback_days)
        if df.empty or len(df) < 30:
            return symbol, False, "empty-or-short"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        return symbol, True, f"saved:{len(df)}"
    except Exception as exc:  # noqa: BLE001 — batch soft-fail
        logger.warning("ingest failed %s: %s", symbol, exc)
        return symbol, False, str(exc)


def ingest_many(
    tickers: list[str],
    raw_dir: Path,
    lookback_days: int = 400,
    workers: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    ok: list[str] = []
    failed: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {
            pool.submit(ingest_one, t, raw_dir, lookback_days, force): t for t in tickers
        }
        for fut in as_completed(futs):
            symbol, success, msg = fut.result()
            if success:
                ok.append(symbol)
            else:
                failed.append({"symbol": symbol, "error": msg})
    meta = {
        "as_of": date.today().isoformat(),
        "requested": len(tickers),
        "ok": len(ok),
        "failed": failed,
        "ok_symbols": sorted(ok),
    }
    (raw_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def list_cached_symbols(raw_dir: Path) -> list[str]:
    if not raw_dir.exists():
        return []
    return sorted(p.stem for p in raw_dir.glob("*.parquet"))
