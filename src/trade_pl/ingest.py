"""OHLCV ingest with Yahoo rate-limit / crumb handling.

Yahoo Finance uses a shared cookie+crumb. Bursting many parallel
``yfinance`` calls triggers ``401 Invalid Crumb`` / ``Unauthorized``.
This module:
  - keeps concurrency low
  - retries auth/rate-limit errors with backoff and crumb reset
  - fails fast on permanent 404 / delisted / no-timezone errors
  - reuses today's cache, and keeps yesterday's cache on soft failure
  - quiets yfinance's noisy ERROR spam during batch runs
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Serialize crumb-sensitive history fetches across threads.
# Shared with enrichment so pipeline stages don't fight Yahoo's crumb.
_YF_LOCK = threading.Lock()
_LAST_FETCH_TS = 0.0
_ADAPTIVE_BACKOFF = 0.0

# Known dead / acquired / Yahoo-404 tickers. Skip without calling the API.
# Keep small; prefer removing from universe_catalog when confirmed.
DEAD_SYMBOLS: frozenset[str] = frozenset(
    {
        "ATVI",  # Activision → MSFT
        "SGEN",  # Seagen → PFE
        "SPLK",  # Splunk → Cisco
        "CDAY",  # Ceridian → DAY (renamed/acquired path)
        "NEWR",  # New Relic taken private
        "HES",  # Hess → CVX
        "MRO",  # Marathon Oil → COP
        "WRK",  # WestRock merged
        "PEAK",  # Healthpeak renamed / structure change
        "SWN",  # Southwestern → Expand Energy
        "CLR",  # Continental Resources taken private
        "PXD",  # Pioneer → XOM
        "K",  # Yahoo 404 (Kellanova); prefer peers
        "MMC",  # Yahoo 404 intermittently
        "FI",  # Yahoo 404; use FISV historically — keep skipped
        "CTRA",  # Yahoo 404 intermittently
        "BROOKFIELD",
        "SQ",  # became XYZ
        "FB",  # became META
        "TWTR",
        "AESI",  # often missing
    }
)


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


@contextmanager
def _quiet_yfinance():
    yf_logger = logging.getLogger("yfinance")
    prev = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        yf_logger.setLevel(prev)


def _reset_yahoo_auth() -> None:
    """Drop cached crumb/cookie so the next call re-authenticates."""
    try:
        from yfinance.data import YfData

        data = YfData()
        with getattr(data, "_cookie_lock", threading.Lock()):
            data._crumb = None
            data._cookie = None
    except Exception as exc:  # noqa: BLE001
        logger.debug("crumb reset failed: %s", exc)


def _is_auth_error(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    return any(
        tok in text
        for tok in (
            "invalid crumb",
            "unauthorized",
            "401",
            "too many requests",
            "rate limit",
            "429",
        )
    )


def _is_permanent_error(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    return any(
        tok in text
        for tok in (
            "delisted",
            "no timezone",
            "quote not found",
            "not found for symbol",
            "no data found",
            "symbol may be delisted",
            "404",
        )
    )


def _pace(min_interval: float) -> None:
    global _LAST_FETCH_TS
    if min_interval <= 0 and _ADAPTIVE_BACKOFF <= 0:
        return
    now = time.monotonic()
    gap = max(0.0, float(min_interval)) + _ADAPTIVE_BACKOFF
    wait = gap - (now - _LAST_FETCH_TS)
    if wait > 0:
        time.sleep(wait + random.uniform(0, max(0.05, gap * 0.2)))
    _LAST_FETCH_TS = time.monotonic()


def yahoo_note_throttle() -> None:
    """Widen the shared Yahoo gap after a 429 / crumb failure."""
    global _ADAPTIVE_BACKOFF
    _ADAPTIVE_BACKOFF = min(10.0, max(1.2, _ADAPTIVE_BACKOFF * 1.6) + 0.4)


def yahoo_note_ok() -> None:
    """Ease adaptive backoff after a successful Yahoo call."""
    global _ADAPTIVE_BACKOFF
    _ADAPTIVE_BACKOFF = max(0.0, _ADAPTIVE_BACKOFF * 0.65 - 0.15)


def yahoo_is_throttle(exc: BaseException | str) -> bool:
    return _is_auth_error(exc)


def yahoo_reset_auth() -> None:
    _reset_yahoo_auth()


@contextmanager
def yahoo_slot(min_interval: float = 0.5):
    """Hold the shared Yahoo lock and pace before a crumb-sensitive call."""
    with _YF_LOCK:
        _pace(min_interval)
        yield


def download_ticker(
    symbol: str,
    lookback_days: int,
    *,
    retries: int = 4,
    min_interval: float = 0.35,
) -> pd.DataFrame:
    """Fetch OHLCV with lock + selective retries against Yahoo crumb failures."""
    end = datetime.utcnow().date() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    last_err: BaseException | None = None

    with _quiet_yfinance():
        for attempt in range(1, retries + 1):
            try:
                with _YF_LOCK:
                    _pace(min_interval)
                    ticker = yf.Ticker(symbol)
                    raw = ticker.history(
                        start=start.isoformat(),
                        end=end.isoformat(),
                        auto_adjust=False,
                        actions=False,
                        raise_errors=False,
                    )
                df = _normalize_ohlcv(raw)
                if not df.empty:
                    return df
                # Empty after a real call: usually permanent (delisted / bad symbol).
                # One short retry only in case of a transient empty response.
                last_err = RuntimeError("empty-or-delisted")
                if attempt >= 2:
                    break
                time.sleep(0.6 + random.uniform(0, 0.4))
                continue
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if _is_permanent_error(exc):
                    logger.info("Skip %s (permanent Yahoo miss): %s", symbol, exc)
                    break
                if _is_auth_error(exc):
                    logger.warning(
                        "Yahoo auth/rate limit on %s (attempt %d/%d)",
                        symbol,
                        attempt,
                        retries,
                    )
                    with _YF_LOCK:
                        _reset_yahoo_auth()
                    time.sleep(min(8.0, 1.5 * attempt) + random.uniform(0.2, 1.0))
                    continue
                logger.debug("download error %s attempt %d: %s", symbol, attempt, exc)
                time.sleep(min(5.0, 0.8 * attempt) + random.uniform(0.1, 0.6))

    if last_err and _is_auth_error(last_err):
        raise last_err
    return pd.DataFrame()


def ingest_one(
    symbol: str,
    raw_dir: Path,
    lookback_days: int,
    force: bool = False,
    min_interval: float = 0.35,
) -> tuple[str, bool, str]:
    path = ticker_path(raw_dir, symbol)
    sym_u = symbol.upper()
    if sym_u in DEAD_SYMBOLS:
        if path.exists() and not force:
            return symbol, True, "cache-dead-symbol"
        return symbol, False, "dead-symbol"

    if path.exists() and not force:
        mtime = datetime.fromtimestamp(path.stat().st_mtime).date()
        if mtime >= date.today():
            return symbol, True, "cache-fresh"
    try:
        df = download_ticker(symbol, lookback_days, min_interval=min_interval)
        if df.empty or len(df) < 30:
            if path.exists() and not force:
                return symbol, True, "cache-stale"
            return symbol, False, "empty-or-short"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        return symbol, True, f"saved:{len(df)}"
    except Exception as exc:  # noqa: BLE001 — batch soft-fail
        if path.exists() and not force:
            logger.warning("ingest soft-fail %s (keeping cache): %s", symbol, exc)
            return symbol, True, f"cache-stale:{exc}"
        logger.warning("ingest failed %s: %s", symbol, exc)
        return symbol, False, str(exc)


def ingest_many(
    tickers: list[str],
    raw_dir: Path,
    lookback_days: int = 400,
    workers: int = 3,
    force: bool = False,
    min_interval: float = 0.35,
) -> dict[str, Any]:
    """Download many tickers. Keep workers low (2–4) to avoid Yahoo 401s."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(int(workers), 4))
    ok: list[str] = []
    failed: list[dict[str, str]] = []
    cached = 0
    saved = 0
    skipped_dead = 0

    logger.info(
        "Ingest starting: %d symbols, workers=%d, pace≈%.2fs (Yahoo crumb-safe)",
        len(tickers),
        workers,
        min_interval,
    )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(ingest_one, t, raw_dir, lookback_days, force, min_interval): t
            for t in tickers
        }
        done = 0
        for fut in as_completed(futs):
            symbol, success, msg = fut.result()
            done += 1
            if success:
                ok.append(symbol)
                if msg.startswith("saved:"):
                    saved += 1
                else:
                    cached += 1
            else:
                failed.append({"symbol": symbol, "error": msg})
                if msg == "dead-symbol":
                    skipped_dead += 1
            if done % 50 == 0 or done == len(tickers):
                logger.info(
                    "Ingest progress %d/%d  ok=%d fail=%d (saved=%d cache=%d dead-skip=%d)",
                    done,
                    len(tickers),
                    len(ok),
                    len(failed),
                    saved,
                    cached,
                    skipped_dead,
                )
            if saved and saved % 100 == 0 and msg.startswith("saved:"):
                time.sleep(2.5 + random.uniform(0, 1.5))

    meta = {
        "as_of": date.today().isoformat(),
        "requested": len(tickers),
        "ok": len(ok),
        "saved": saved,
        "cached": cached,
        "skipped_dead": skipped_dead,
        "failed": failed,
        "ok_symbols": sorted(ok),
        "workers": workers,
        "min_interval": min_interval,
    }
    (raw_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def list_cached_symbols(raw_dir: Path) -> list[str]:
    if not raw_dir.exists():
        return []
    return sorted(p.stem for p in raw_dir.glob("*.parquet"))
