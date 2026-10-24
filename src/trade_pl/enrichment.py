from __future__ import annotations

import json
import logging
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import yfinance as yf

from trade_pl.ingest import (
    yahoo_is_throttle,
    yahoo_note_ok,
    yahoo_note_throttle,
    yahoo_reset_auth,
    yahoo_slot,
)

logger = logging.getLogger(__name__)

# Quiet repeated 429 spam; one summary line at the end of enrich_many.
_THROTTLE_HITS = 0


def _fetch_news_search(symbol: str, limit: int = 12) -> list[dict[str, Any]]:
    """Fallback when yfinance.Ticker.news is empty (Yahoo API changes often)."""
    url = (
        "https://query1.finance.yahoo.com/v1/finance/search?"
        f"q={symbol}&quotesCount=0&newsCount={limit}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 trade_pl/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        logger.debug("news search failed %s: %s", symbol, exc)
        return []
    out: list[dict[str, Any]] = []
    for item in data.get("news") or []:
        title = item.get("title")
        if not title:
            continue
        out.append(
            {
                "title": title,
                "publisher": item.get("publisher"),
                "link": item.get("link"),
                "published": _ts_to_iso(item.get("providerPublishTime")),
            }
        )
    return out


def _safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def _ts_to_iso(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.utcfromtimestamp(int(v)).date().isoformat()
        if hasattr(v, "isoformat"):
            return v.isoformat()[:10]
        return str(v)[:10]
    except Exception:
        return None


def _fast_info_dict(ticker: Any) -> dict[str, Any]:
    try:
        fi = ticker.fast_info
        if fi is None:
            return {}
        if isinstance(fi, dict):
            return fi
        return dict(fi)
    except Exception:
        return {}


def _yahoo_call(
    label: str,
    fn: Callable[[], Any],
    *,
    min_interval: float,
    retries: int = 3,
    default: Any = None,
) -> Any:
    """Run a Yahoo accessor with shared pacing + throttle backoff."""
    global _THROTTLE_HITS
    last_exc: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            with yahoo_slot(min_interval):
                out = fn()
            yahoo_note_ok()
            return out
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if yahoo_is_throttle(exc):
                _THROTTLE_HITS += 1
                yahoo_note_throttle()
                yahoo_reset_auth()
                sleep_s = min(12.0, 1.2 * attempt + random.uniform(0.3, 1.2))
                logger.debug(
                    "Yahoo throttle on %s (attempt %d/%d); sleep %.1fs",
                    label,
                    attempt,
                    retries,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue
            logger.debug("%s failed: %s", label, exc)
            break
    if last_exc is not None and yahoo_is_throttle(last_exc):
        logger.debug("%s gave up after throttle: %s", label, last_exc)
    elif last_exc is not None:
        logger.debug("%s failed: %s", label, last_exc)
    return default


def _history_quote(ticker: Any, *, min_interval: float) -> dict[str, Any]:
    """Price + short-horizon returns from OHLCV history (works when info is thin)."""
    out: dict[str, Any] = {}
    hist = _yahoo_call(
        f"{getattr(ticker, 'ticker', '?')}.history",
        lambda: ticker.history(period="2y", auto_adjust=False, actions=False),
        min_interval=min_interval,
        retries=3,
        default=None,
    )
    if hist is None or getattr(hist, "empty", True) or "Close" not in getattr(hist, "columns", []):
        return out
    close = hist["Close"].dropna()
    if close.empty:
        return out
    last = float(close.iloc[-1])
    out["last_price"] = last
    if len(close) >= 2:
        prev = float(close.iloc[-2])
        out["previous_close"] = prev
        out["return_1d"] = (last / prev - 1.0) if prev else None

    def _ret(n: int) -> float | None:
        if len(close) <= n:
            return None
        base = float(close.iloc[-n - 1])
        return (last / base - 1.0) if base else None

    out["return_1w"] = _ret(5)
    out["return_1m"] = _ret(21)
    out["return_3m"] = _ret(63)
    out["return_6m"] = _ret(126)
    out["return_1y"] = _ret(252)
    if "Volume" in hist.columns and len(hist) >= 1:
        out["volume"] = _safe_float(hist["Volume"].iloc[-1])
        out["avg_volume_20d"] = _safe_float(hist["Volume"].tail(20).mean())
    if "High" in hist.columns:
        out["day_high"] = _safe_float(hist["High"].iloc[-1])
    if "Low" in hist.columns:
        out["day_low"] = _safe_float(hist["Low"].iloc[-1])
    if "Open" in hist.columns:
        out["open"] = _safe_float(hist["Open"].iloc[-1])
    return out


def fetch_profile(symbol: str, *, min_interval: float = 0.75) -> dict[str, Any]:
    from trade_pl.search import normalize_symbol

    symbol = normalize_symbol(symbol)
    t = yf.Ticker(symbol)

    info_raw = _yahoo_call(
        f"{symbol}.info",
        lambda: t.info or {},
        min_interval=min_interval,
        retries=3,
        default={},
    )
    info: dict[str, Any] = info_raw if isinstance(info_raw, dict) else {}

    fast = _yahoo_call(
        f"{symbol}.fast_info",
        lambda: _fast_info_dict(t),
        min_interval=min_interval * 0.6,
        retries=2,
        default={},
    ) or {}

    hist_q = _history_quote(t, min_interval=min_interval)

    news_raw = _yahoo_call(
        f"{symbol}.news",
        lambda: t.news or [],
        min_interval=min_interval * 0.6,
        retries=2,
        default=[],
    ) or []

    news: list[dict[str, Any]] = []
    for item in news_raw[:12]:
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        title = item.get("title") or content.get("title")
        link = (
            item.get("link")
            or (content.get("clickThroughUrl") or {}).get("url")
            or (content.get("canonicalUrl") or {}).get("url")
        )
        pub = item.get("providerPublishTime") or content.get("pubDate") or item.get("pubDate")
        publisher = item.get("publisher") or (content.get("provider") or {}).get("displayName")
        if title:
            news.append(
                {
                    "title": title,
                    "publisher": publisher,
                    "link": link,
                    "published": _ts_to_iso(pub) if isinstance(pub, (int, float)) else (str(pub)[:10] if pub else None),
                }
            )
    if not news:
        news = _fetch_news_search(symbol)

    calendar: dict[str, Any] = {}
    cal = _yahoo_call(
        f"{symbol}.calendar",
        lambda: t.calendar,
        min_interval=min_interval * 0.5,
        retries=2,
        default=None,
    )
    try:
        if isinstance(cal, dict):
            calendar = {str(k): (str(v) if not isinstance(v, (list, dict)) else v) for k, v in cal.items()}
        elif cal is not None:
            try:
                calendar = {str(k): str(v) for k, v in dict(cal).items()}
            except Exception:
                calendar = {"raw": str(cal)[:500]}
    except Exception:
        calendar = {}

    earnings_dates: list[dict[str, Any]] = []
    ed = _yahoo_call(
        f"{symbol}.earnings_dates",
        lambda: t.earnings_dates,
        min_interval=min_interval * 0.5,
        retries=2,
        default=None,
    )
    try:
        if ed is not None and hasattr(ed, "reset_index"):
            tmp = ed.reset_index().head(8)
            for _, row in tmp.iterrows():
                earnings_dates.append(
                    {
                        k: (
                            None
                            if (isinstance(v, float) and v != v)
                            else (str(v) if not isinstance(v, (int, float)) else v)
                        )
                        for k, v in row.to_dict().items()
                    }
                )
    except Exception:
        earnings_dates = []

    financials: dict[str, Any] = {"income": [], "balance": [], "cashflow": []}
    try:
        inc = _yahoo_call(
            f"{symbol}.income_stmt",
            lambda: t.income_stmt,
            min_interval=min_interval,
            retries=2,
            default=None,
        )
        if inc is not None and not getattr(inc, "empty", True):
            cols = list(inc.columns)[:4]
            for col in cols:
                financials["income"].append(
                    {
                        "period": str(col)[:10],
                        "Total Revenue": _safe_float(inc[col].get("Total Revenue")),
                        "Gross Profit": _safe_float(inc[col].get("Gross Profit")),
                        "Operating Income": _safe_float(inc[col].get("Operating Income")),
                        "Net Income": _safe_float(inc[col].get("Net Income")),
                        "EBITDA": _safe_float(inc[col].get("EBITDA")),
                    }
                )
    except Exception:
        pass
    try:
        bal = _yahoo_call(
            f"{symbol}.balance_sheet",
            lambda: t.balance_sheet,
            min_interval=min_interval,
            retries=2,
            default=None,
        )
        if bal is not None and not getattr(bal, "empty", True):
            cols = list(bal.columns)[:4]
            for col in cols:
                financials["balance"].append(
                    {
                        "period": str(col)[:10],
                        "Total Assets": _safe_float(bal[col].get("Total Assets")),
                        "Total Liabilities Net Minority Interest": _safe_float(
                            bal[col].get("Total Liabilities Net Minority Interest")
                        ),
                        "Stockholders Equity": _safe_float(bal[col].get("Stockholders Equity")),
                        "Cash And Cash Equivalents": _safe_float(bal[col].get("Cash And Cash Equivalents")),
                        "Total Debt": _safe_float(bal[col].get("Total Debt")),
                    }
                )
    except Exception:
        pass
    try:
        cf = _yahoo_call(
            f"{symbol}.cashflow",
            lambda: t.cashflow,
            min_interval=min_interval,
            retries=2,
            default=None,
        )
        if cf is not None and not getattr(cf, "empty", True):
            cols = list(cf.columns)[:4]
            for col in cols:
                financials["cashflow"].append(
                    {
                        "period": str(col)[:10],
                        "Operating Cash Flow": _safe_float(cf[col].get("Operating Cash Flow")),
                        "Capital Expenditure": _safe_float(cf[col].get("Capital Expenditure")),
                        "Free Cash Flow": _safe_float(cf[col].get("Free Cash Flow")),
                    }
                )
    except Exception:
        pass

    last_price = (
        _safe_float(info.get("currentPrice"))
        or _safe_float(info.get("regularMarketPrice"))
        or _safe_float(fast.get("lastPrice"))
        or _safe_float(fast.get("last_price"))
        or hist_q.get("last_price")
    )
    prev_close = (
        _safe_float(info.get("previousClose"))
        or _safe_float(info.get("regularMarketPreviousClose"))
        or _safe_float(fast.get("previousClose"))
        or _safe_float(fast.get("regularMarketPreviousClose"))
        or hist_q.get("previous_close")
    )
    day_chg = None
    day_chg_pct = hist_q.get("return_1d")
    if last_price is not None and prev_close not in (None, 0):
        day_chg = last_price - float(prev_close)
        day_chg_pct = day_chg / float(prev_close)

    rec_raw = info.get("recommendationKey") or info.get("recommendationMean")
    if isinstance(rec_raw, str) and rec_raw.strip().lower() in {"none", "nan", ""}:
        rec_raw = None

    currency = info.get("currency") or fast.get("currency") or "USD"
    exchange = info.get("exchange") or fast.get("exchange") or info.get("fullExchangeName")

    profile = {
        "symbol": symbol,
        "as_of": date.today().isoformat(),
        "name": info.get("longName") or info.get("shortName") or fast.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "website": info.get("website"),
        "summary": info.get("longBusinessSummary"),
        "employees": info.get("fullTimeEmployees"),
        "city": info.get("city"),
        "country": info.get("country"),
        "currency": currency,
        "exchange": exchange,
        "quote_type": info.get("quoteType") or fast.get("quoteType"),
        "last_price": last_price,
        "previous_close": prev_close,
        "open": _safe_float(info.get("open")) or _safe_float(fast.get("open")) or hist_q.get("open"),
        "day_high": _safe_float(info.get("dayHigh")) or _safe_float(fast.get("dayHigh")) or hist_q.get("day_high"),
        "day_low": _safe_float(info.get("dayLow")) or _safe_float(fast.get("dayLow")) or hist_q.get("day_low"),
        "volume": _safe_float(info.get("volume")) or _safe_float(fast.get("lastVolume")) or hist_q.get("volume"),
        "avg_volume_20d": hist_q.get("avg_volume_20d") or _safe_float(info.get("averageVolume")),
        "day_change": day_chg,
        "day_change_pct": day_chg_pct,
        "return_1d": hist_q.get("return_1d") if hist_q.get("return_1d") is not None else day_chg_pct,
        "return_1w": hist_q.get("return_1w"),
        "return_1m": hist_q.get("return_1m"),
        "return_3m": hist_q.get("return_3m"),
        "return_6m": hist_q.get("return_6m"),
        "return_1y": hist_q.get("return_1y") or _safe_float(fast.get("yearChange")),
        "market_cap": _safe_float(info.get("marketCap")) or _safe_float(fast.get("marketCap")),
        "enterprise_value": _safe_float(info.get("enterpriseValue")),
        "trailing_pe": _safe_float(info.get("trailingPE")),
        "forward_pe": _safe_float(info.get("forwardPE")),
        "peg_ratio": _safe_float(info.get("pegRatio")),
        "price_to_book": _safe_float(info.get("priceToBook")),
        "profit_margins": _safe_float(info.get("profitMargins")),
        "operating_margins": _safe_float(info.get("operatingMargins")),
        "revenue_growth": _safe_float(info.get("revenueGrowth")),
        "earnings_growth": _safe_float(info.get("earningsGrowth")),
        "dividend_yield": _safe_float(info.get("dividendYield")),
        "payout_ratio": _safe_float(info.get("payoutRatio")),
        "beta": _safe_float(info.get("beta")),
        "fifty_two_week_high": _safe_float(info.get("fiftyTwoWeekHigh"))
        or _safe_float(fast.get("yearHigh")),
        "fifty_two_week_low": _safe_float(info.get("fiftyTwoWeekLow"))
        or _safe_float(fast.get("yearLow")),
        "target_mean_price": _safe_float(info.get("targetMeanPrice")),
        "recommendation": rec_raw,
        "earnings_date": _ts_to_iso(info.get("earningsTimestamp"))
        or _ts_to_iso(info.get("earningsTimestampStart")),
        "ex_dividend_date": _ts_to_iso(info.get("exDividendDate")),
        "calendar": calendar,
        "earnings_dates": earnings_dates,
        "news": news,
        "financials": financials,
    }
    return profile


def profile_needs_refresh(profile: dict[str, Any] | None) -> bool:
    """True when cached profile is missing critical quote fields."""
    if not profile:
        return True
    if profile.get("last_price") in (None, "", 0):
        return True
    if not (profile.get("news") or (profile.get("financials") or {}).get("income")):
        return True
    return False


def profile_is_fresh(
    profile: dict[str, Any] | None,
    path: Path | None = None,
    *,
    max_age_hours: float = 18.0,
) -> bool:
    """Skip Yahoo re-fetch when we already have a usable recent profile."""
    if profile_needs_refresh(profile):
        return False
    if path is not None and path.exists() and max_age_hours > 0:
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        if age_h <= max_age_hours:
            return True
    as_of = str((profile or {}).get("as_of") or "")[:10]
    return as_of == date.today().isoformat()


def enrich_many(
    symbols: list[str],
    out_dir: Path,
    workers: int = 2,
    *,
    min_interval: float = 0.75,
    max_age_hours: float = 18.0,
    force: bool = False,
) -> list[str]:
    """Enrich symbols with Yahoo pacing. Reuses fresh cache to avoid 429 storms."""
    global _THROTTLE_HITS
    _THROTTLE_HITS = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(int(workers), 3))
    done: list[str] = []
    skipped = 0
    failed = 0
    total = len(symbols)

    logger.info(
        "Enrich starting: %d symbols, workers=%d, pace≈%.2fs, skip_fresh≤%.0fh",
        total,
        workers,
        min_interval,
        max_age_hours,
    )

    def _one(sym: str) -> tuple[str, str]:
        """Return (symbol, status) where status is saved|skipped|failed."""
        path = profile_path(out_dir, sym)
        if not force:
            existing = load_profile(out_dir, sym)
            if profile_is_fresh(existing, path, max_age_hours=max_age_hours):
                return sym, "skipped"
        try:
            profile = fetch_profile(sym, min_interval=min_interval)
            # Keep prior good fields if this pass was heavily throttled.
            if profile_needs_refresh(profile) and path.exists():
                prior = load_profile(out_dir, sym)
                if prior and not profile_needs_refresh(prior):
                    for key in ("name", "sector", "industry", "summary", "news", "financials", "calendar"):
                        if not profile.get(key) and prior.get(key):
                            profile[key] = prior[key]
                    if profile.get("last_price") in (None, "", 0) and prior.get("last_price"):
                        profile["last_price"] = prior["last_price"]
            save_profile(out_dir, profile)
            return sym, "saved"
        except Exception as exc:  # noqa: BLE001
            if yahoo_is_throttle(exc):
                yahoo_note_throttle()
                logger.debug("enrich throttle %s: %s", sym, exc)
            else:
                logger.warning("enrich failed %s: %s", sym, exc)
            return sym, "failed"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, s) for s in symbols]
        finished = 0
        for fut in as_completed(futs):
            sym, status = fut.result()
            finished += 1
            if status == "saved":
                done.append(sym)
            elif status == "skipped":
                skipped += 1
                done.append(sym)
            else:
                failed += 1
            if finished % 50 == 0 or finished == total:
                logger.info(
                    "Enrich progress %d/%d  ok=%d skip=%d fail=%d (throttle_hits=%d)",
                    finished,
                    total,
                    len(done) - skipped,
                    skipped,
                    failed,
                    _THROTTLE_HITS,
                )

    logger.info(
        "Enrich done ok=%d skipped_fresh=%d failed=%d throttle_hits=%d",
        len(done) - skipped,
        skipped,
        failed,
        _THROTTLE_HITS,
    )
    if _THROTTLE_HITS:
        logger.warning(
            "Yahoo rate-limited enrichment %d times — slowed automatically; "
            "re-run later or raise enrich_min_interval if this persists.",
            _THROTTLE_HITS,
        )
    return done


def profile_path(out_dir: Path, symbol: str) -> Path:
    from trade_pl.search import normalize_symbol

    return out_dir / f"{normalize_symbol(symbol)}.json"


def save_profile(out_dir: Path, profile: dict[str, Any]) -> Path:
    """Persist one enrichment profile (financials/news/etc.) for later search + detail pages."""
    from trade_pl.search import normalize_symbol

    symbol = normalize_symbol(str(profile.get("symbol") or ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = profile_path(out_dir, symbol)
    payload = {**profile, "symbol": symbol}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_profile(out_dir: Path, symbol: str) -> dict[str, Any] | None:
    path = profile_path(out_dir, symbol)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
