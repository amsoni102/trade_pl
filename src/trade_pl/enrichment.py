from __future__ import annotations

import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)


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


def _history_quote(ticker: Any, lookback_days: int = 420) -> dict[str, Any]:
    """Price + short-horizon returns from OHLCV history (works when info is thin)."""
    out: dict[str, Any] = {}
    try:
        hist = ticker.history(period="2y", auto_adjust=False, actions=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("history quote failed: %s", exc)
        return out
    if hist is None or hist.empty or "Close" not in hist.columns:
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


def fetch_profile(symbol: str) -> dict[str, Any]:
    from trade_pl.search import normalize_symbol

    symbol = normalize_symbol(symbol)
    t = yf.Ticker(symbol)
    info: dict[str, Any] = {}
    try:
        info = t.info or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("info failed %s: %s", symbol, exc)
    fast = _fast_info_dict(t)
    hist_q = _history_quote(t)

    news_raw = []
    try:
        news_raw = t.news or []
    except Exception:
        news_raw = []

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
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            calendar = {str(k): (str(v) if not isinstance(v, (list, dict)) else v) for k, v in cal.items()}
        elif cal is not None:
            # pandas Series / DataFrame-ish
            try:
                calendar = {str(k): str(v) for k, v in dict(cal).items()}
            except Exception:
                calendar = {"raw": str(cal)[:500]}
    except Exception:
        calendar = {}

    earnings_dates: list[dict[str, Any]] = []
    try:
        ed = t.earnings_dates
        if ed is not None and hasattr(ed, "reset_index"):
            tmp = ed.reset_index().head(8)
            for _, row in tmp.iterrows():
                earnings_dates.append({k: (None if (isinstance(v, float) and v != v) else (str(v) if not isinstance(v, (int, float)) else v)) for k, v in row.to_dict().items()})
    except Exception:
        earnings_dates = []

    financials: dict[str, Any] = {"income": [], "balance": [], "cashflow": []}
    try:
        inc = t.income_stmt
        if inc is not None and not inc.empty:
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
        bal = t.balance_sheet
        if bal is not None and not bal.empty:
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
        cf = t.cashflow
        if cf is not None and not cf.empty:
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


def enrich_many(
    symbols: list[str],
    out_dir: Path,
    workers: int = 4,
) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    done: list[str] = []

    def _one(sym: str) -> str | None:
        try:
            profile = fetch_profile(sym)
            save_profile(out_dir, profile)
            return sym
        except Exception as exc:  # noqa: BLE001
            logger.warning("enrich failed %s: %s", sym, exc)
            return None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(_one, s) for s in symbols]
        for fut in as_completed(futs):
            sym = fut.result()
            if sym:
                done.append(sym)
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
