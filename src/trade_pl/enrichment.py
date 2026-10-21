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


def fetch_profile(symbol: str) -> dict[str, Any]:
    from trade_pl.search import normalize_symbol

    symbol = normalize_symbol(symbol)
    t = yf.Ticker(symbol)
    info: dict[str, Any] = {}
    try:
        info = t.info or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("info failed %s: %s", symbol, exc)

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

    profile = {
        "symbol": symbol,
        "as_of": date.today().isoformat(),
        "name": info.get("shortName") or info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "website": info.get("website"),
        "summary": info.get("longBusinessSummary"),
        "employees": info.get("fullTimeEmployees"),
        "city": info.get("city"),
        "country": info.get("country"),
        "market_cap": _safe_float(info.get("marketCap")),
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
        "fifty_two_week_high": _safe_float(info.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low": _safe_float(info.get("fiftyTwoWeekLow")),
        "target_mean_price": _safe_float(info.get("targetMeanPrice")),
        "recommendation": info.get("recommendationKey"),
        "earnings_date": _ts_to_iso(info.get("earningsTimestamp"))
        or _ts_to_iso(info.get("earningsTimestampStart")),
        "ex_dividend_date": _ts_to_iso(info.get("exDividendDate")),
        "calendar": calendar,
        "earnings_dates": earnings_dates,
        "news": news,
        "financials": financials,
    }
    return profile


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
