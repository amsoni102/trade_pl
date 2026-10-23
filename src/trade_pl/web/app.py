from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pandas as pd
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trade_pl.breaking_news import get_scanner
from trade_pl.config import ROOT, load_config
from trade_pl.enrichment import load_profile, save_profile
from trade_pl.floor import build_floor
from trade_pl.search import normalize_symbol, search_stocks
from trade_pl.sectors import market_of

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def _paths() -> dict[str, Path]:
    cfg = load_config()
    return {
        "reports": Path(cfg["paths"]["reports"]),
        "plots": Path(cfg["paths"]["plots"]),
        "metrics": Path(cfg["paths"]["data_metrics"]),
        "enrichment": Path(cfg["paths"].get("data_enrichment", ROOT / "data" / "enrichment")),
        "search": Path(cfg["paths"].get("data_search", ROOT / "data" / "search")),
        "filings": Path(cfg["paths"].get("data_filings", ROOT / "data" / "filings")),
    }


def _leaderboard_rows() -> list[dict[str, Any]]:
    df = load_leaderboard()
    if df.empty:
        return []
    return df.to_dict(orient="records")


def _run_search(q: str, limit: int = 12) -> list[dict[str, Any]]:
    paths = _paths()
    return search_stocks(
        q,
        leaderboard_rows=_leaderboard_rows(),
        enrichment_dir=paths["enrichment"],
        cache_dir=paths["search"],
        limit=limit,
        yahoo=True,
    )


def _refresh_filings_and_calendar(symbols: list[str]) -> None:
    """Background: SEC filings for top names + rebuild priority event calendar."""
    import threading

    def _job() -> None:
        try:
            from trade_pl.events_calendar import build_calendar, write_calendar
            from trade_pl.sec_filings import fetch_filings_many

            paths = _paths()
            filings_dir = paths["filings"]
            enrich_dir = paths["enrichment"]
            cik_cache = ROOT / "data" / "sec_cik_map.json"
            # Official reports for a wide top set (SEC rate-limit friendly workers)
            fetch_filings_many(symbols[:200], filings_dir, cik_cache, workers=3)
            cal = build_calendar(enrich_dir, filings_dir, symbols=symbols[:400], limit=250)
            write_calendar(cal, paths["reports"] / "latest" / "events_calendar.json")
            write_calendar(cal, ROOT / "data" / "events_calendar.json")
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("trade_pl").warning("filings/calendar refresh failed: %s", exc)

    threading.Thread(target=_job, name="filings-calendar", daemon=True).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Keep scanning public finance RSS / news feeds for as long as the website is up.
    universe: list[str] = []
    lb = _paths()["reports"] / "latest" / "leaderboard.csv"
    if lb.exists():
        try:
            universe = pd.read_csv(lb)["symbol"].astype(str).head(500).tolist()
        except Exception:
            universe = []
    scanner = get_scanner(cache_path=ROOT / "data" / "breaking_news.json", universe=universe)
    scanner.start()
    if universe:
        _refresh_filings_and_calendar(universe)
    yield
    scanner.stop()


app = FastAPI(
    title="trade_pl",
    description="Local stock research website",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


_PLOTS_DIR = _paths()["plots"]
_PLOTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/plots", StaticFiles(directory=str(_PLOTS_DIR)), name="plots")


def _latest_dir() -> Path:
    return _paths()["reports"] / "latest"


def load_leaderboard() -> pd.DataFrame:
    path = _latest_dir() / "leaderboard.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if not df.empty and "market" not in df.columns and "symbol" in df.columns:
        df["market"] = df["symbol"].astype(str).map(market_of)
    return df


def load_pick() -> dict[str, Any] | None:
    path = _latest_dir() / "suggested_pick.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest() -> dict[str, Any]:
    path = _latest_dir() / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_horizons() -> dict[str, Any]:
    path = _latest_dir() / "leaders_by_horizon.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_sectors() -> dict[str, Any]:
    path = _latest_dir() / "leaders_by_sector.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_money(v: Any) -> str:
    try:
        if v is None:
            return "—"
        x = float(v)
        absx = abs(x)
        if absx >= 1e12:
            return f"${x/1e12:.2f}T"
        if absx >= 1e9:
            return f"${x/1e9:.2f}B"
        if absx >= 1e6:
            return f"${x/1e6:.2f}M"
        return f"${x:,.0f}"
    except Exception:
        return "—"


def _fmt_price(v: Any) -> str:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return "—"
        x = float(v)
        if abs(x) >= 1000:
            return f"{x:,.2f}"
        if abs(x) >= 1:
            return f"{x:.2f}"
        return f"{x:.4f}"
    except Exception:
        return "—"


def _signal_from_recommendation(rec: Any) -> dict[str, str]:
    raw = str(rec or "").strip().lower().replace(" ", "_").replace("-", "_")
    if raw in {"", "none", "nan", "—", "-"}:
        return {"label": "—", "class": "signal-none", "raw": ""}
    mapping = {
        "strong_buy": ("BUY", "signal-buy"),
        "buy": ("BUY", "signal-buy"),
        "overweight": ("BUY", "signal-buy"),
        "outperform": ("BUY", "signal-buy"),
        "hold": ("HOLD", "signal-hold"),
        "neutral": ("HOLD", "signal-hold"),
        "market_perform": ("HOLD", "signal-hold"),
        "equal_weight": ("HOLD", "signal-hold"),
        "underperform": ("SELL", "signal-sell"),
        "underweight": ("SELL", "signal-sell"),
        "sell": ("SELL", "signal-sell"),
        "strong_sell": ("SELL", "signal-sell"),
    }
    if raw in mapping:
        label, css = mapping[raw]
        return {"label": label, "class": css, "raw": str(rec)}
    return {"label": "—", "class": "signal-none", "raw": str(rec or "")}


def _signal_from_row(row: dict[str, Any] | None) -> dict[str, str]:
    """Fallback desk signal from model score / short return when no analyst rec."""
    if not row:
        return {"label": "—", "class": "signal-none", "raw": ""}
    try:
        score = float(row.get("score")) if row.get("score") is not None else None
    except Exception:
        score = None
    try:
        r1w = float(row.get("return_1w")) if row.get("return_1w") is not None else None
    except Exception:
        r1w = None
    try:
        r1d = float(row.get("return_1d")) if row.get("return_1d") is not None else None
    except Exception:
        r1d = None
    if score is not None:
        rw = r1w or 0.0
        if score >= 0.72 or (score >= 0.6 and rw > 0.02):
            return {"label": "BUY", "class": "signal-buy", "raw": "model"}
        if score <= 0.35 or (score <= 0.45 and rw < -0.02):
            return {"label": "SELL", "class": "signal-sell", "raw": "model"}
        return {"label": "HOLD", "class": "signal-hold", "raw": "model"}
    # Off-board: momentum from live returns
    if r1w is not None:
        if r1w > 0.03:
            return {"label": "BUY", "class": "signal-buy", "raw": "momentum"}
        if r1w < -0.03:
            return {"label": "SELL", "class": "signal-sell", "raw": "momentum"}
        return {"label": "HOLD", "class": "signal-hold", "raw": "momentum"}
    if r1d is not None:
        if r1d > 0.02:
            return {"label": "BUY", "class": "signal-buy", "raw": "momentum"}
        if r1d < -0.02:
            return {"label": "SELL", "class": "signal-sell", "raw": "momentum"}
        return {"label": "HOLD", "class": "signal-hold", "raw": "momentum"}
    return {"label": "HOLD", "class": "signal-hold", "raw": "n/a"}


def _ccy_symbol(code: Any) -> str:
    return {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "CNY": "¥",
        "HKD": "HK$",
        "BRL": "R$",
        "AUD": "A$",
        "CAD": "C$",
        "CHF": "CHF ",
        "KRW": "₩",
        "INR": "₹",
        "TWD": "NT$",
    }.get(str(code or "USD").upper(), f"{code} ")


templates.env.filters["ccy"] = _ccy_symbol


templates.env.filters["money"] = _fmt_money
templates.env.filters["price"] = _fmt_price
templates.env.filters["signal_rec"] = _signal_from_recommendation
templates.env.filters["signal_row"] = _signal_from_row


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    sort: str = Query("score"),
    q: str = Query(""),
    sector: str = Query(""),
) -> HTMLResponse:
    df = load_leaderboard()
    pick = load_pick()
    manifest = load_manifest()
    horizons = load_horizons()
    sectors_payload = load_sectors()
    sort_options = [
        ("score", "Score"),
        ("return_1w", "1W return"),
        ("return_1m", "1M return"),
        ("return_3m", "3M return"),
        ("return_6m", "6M return"),
        ("return_1y", "1Y return"),
        ("sharpe_60d", "Sharpe"),
        ("max_drawdown", "Max drawdown (least bad)"),
    ]
    sector_list = sorted(df["sector"].dropna().unique().tolist()) if not df.empty and "sector" in df.columns else []
    rows: list[dict[str, Any]] = []
    if not df.empty:
        view = df.copy()
        if sector.strip() and "sector" in view.columns:
            view = view[view["sector"] == sector.strip()]
        if q.strip():
            view = view[view["symbol"].str.contains(q.strip().upper(), na=False)]
        ascending = sort == "max_drawdown"
        if sort in view.columns:
            view = view.sort_values(sort, ascending=ascending)
        else:
            view = view.sort_values("score", ascending=False)
        # Show the full scored board. Keep global `rank`, and add sequential
        # `list_rank` for the current filtered view (so sector pages don't look "broken").
        rows = view.to_dict(orient="records")
        for i, r in enumerate(rows, start=1):
            r["list_rank"] = i
            r["global_rank"] = r.get("rank")

    horizon_cards = []
    for key, data in (horizons.get("horizons") or {}).items():
        best = data.get("best") or {}
        horizon_cards.append(
            {
                "key": key,
                "label": data.get("label", key),
                "symbol": best.get("symbol"),
                "ret": best.get(data.get("metric")),
                "sector": best.get("sector"),
            }
        )

    news = get_scanner().snapshot()
    cal = load_events_calendar()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rows": rows,
            "pick": pick,
            "as_of": (pick or {}).get("as_of") or manifest.get("as_of") or "—",
            "metrics_version": (pick or {}).get("metrics_version")
            or manifest.get("config", {}).get("metrics_version")
            or "metrics_v1",
            "plotted_n": len(manifest.get("plotted") or []),
            "enriched_n": len(manifest.get("enriched") or []),
            "scored_n": manifest.get("tickers_scored") or len(rows),
            "report_label": Path(manifest.get("report_dir", "reports/latest")).name,
            "sort": sort,
            "q": q,
            "sector": sector,
            "sector_list": sector_list,
            "sort_options": sort_options,
            "horizon_cards": horizon_cards,
            "sector_names": sorted((sectors_payload.get("sectors") or {}).keys()),
            "breaking_news": (news.get("items") or [])[:18],
            "news_urgent": news.get("breaking") or [],
            "news_tickers": news.get("with_tickers") or [],
            "news_updated_at": news.get("updated_at"),
            "news_scanning": news.get("scanning"),
            "news_sources_ok": news.get("sources_ok") or [],
            "critical_events": (cal.get("critical") or [])[:8],
            "week_events": (cal.get("this_week") or [])[:8],
        },
    )


@app.get("/news", response_class=HTMLResponse)
def news_page(request: Request) -> HTMLResponse:
    news = get_scanner().snapshot()
    return templates.TemplateResponse(
        request,
        "news.html",
        {
            "items": news.get("items") or [],
            "groups": news.get("groups") or {},
            "breaking": news.get("breaking") or [],
            "with_tickers": news.get("with_tickers") or [],
            "updated_at": news.get("updated_at"),
            "scanning": news.get("scanning"),
            "sources_ok": news.get("sources_ok") or [],
            "sources_failed": news.get("sources_failed") or [],
        },
    )


def load_events_calendar() -> dict[str, Any]:
    for path in (
        _latest_dir() / "events_calendar.json",
        ROOT / "data" / "events_calendar.json",
    ):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    # Build on the fly from whatever enrichment/filings we already have
    try:
        from trade_pl.events_calendar import build_calendar, write_calendar

        paths = _paths()
        cal = build_calendar(paths["enrichment"], paths["filings"], limit=200)
        write_calendar(cal, ROOT / "data" / "events_calendar.json")
        return cal
    except Exception:
        return {"critical": [], "this_week": [], "filings_recent": [], "all": [], "legend": []}


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request) -> HTMLResponse:
    cal = load_events_calendar()
    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "cal": cal,
            "critical": cal.get("critical") or [],
            "this_week": cal.get("this_week") or [],
            "filings_recent": cal.get("filings_recent") or [],
            "all_events": cal.get("all") or [],
            "legend": cal.get("legend") or [],
            "as_of": cal.get("as_of"),
        },
    )


@app.get("/api/calendar")
def api_calendar() -> dict[str, Any]:
    return load_events_calendar()


@app.post("/api/calendar/refresh")
def api_calendar_refresh() -> dict[str, str]:
    lb = _paths()["reports"] / "latest" / "leaderboard.csv"
    symbols: list[str] = []
    if lb.exists():
        try:
            symbols = pd.read_csv(lb)["symbol"].astype(str).head(500).tolist()
        except Exception:
            symbols = []
    if symbols:
        _refresh_filings_and_calendar(symbols)
    return {"status": "refreshing"}


@app.post("/api/breaking-news/refresh")
def api_news_refresh() -> dict[str, Any]:
    scanner = get_scanner()
    # refresh in background thread so request returns quickly if already scanning
    if not scanner.snapshot().get("scanning"):
        threading = __import__("threading")
        threading.Thread(target=scanner.refresh_once, daemon=True).start()
    return {"status": "refreshing"}


@app.get("/api/breaking-news")
def api_breaking_news() -> dict[str, Any]:
    return get_scanner().snapshot()


@app.get("/horizons", response_class=HTMLResponse)
def horizons_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "horizons.html",
        {"horizons": load_horizons()},
    )


@app.get("/sectors", response_class=HTMLResponse)
def sectors_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "sectors.html",
        {"payload": load_sectors()},
    )


@app.get("/sectors/{sector}", response_class=HTMLResponse)
def sector_detail(request: Request, sector: str) -> HTMLResponse:
    payload = load_sectors()
    data = (payload.get("sectors") or {}).get(sector)
    df = load_leaderboard()
    rows = []
    if not df.empty and "sector" in df.columns:
        rows = df[df["sector"] == sector].sort_values("score", ascending=False).to_dict(orient="records")
        for i, r in enumerate(rows, start=1):
            r["list_rank"] = i
            r["global_rank"] = r.get("rank")
    return templates.TemplateResponse(
        request,
        "sector_detail.html",
        {"sector": sector, "data": data, "rows": rows},
    )


@app.get("/floor", response_class=HTMLResponse)
def floor_page(request: Request) -> HTMLResponse:
    payload = build_floor(load_leaderboard(), n=18)
    return templates.TemplateResponse(request, "floor.html", {"floor": payload})


@app.get("/api/floor")
def api_floor(n: int = Query(15, ge=5, le=40)) -> dict[str, Any]:
    return build_floor(load_leaderboard(), n=n)


@app.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = Query(""),
) -> HTMLResponse:
    hits: list[dict[str, Any]] = []
    error: str | None = None
    query = q.strip()
    if query:
        try:
            hits = _run_search(query, limit=15)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
    return templates.TemplateResponse(
        request,
        "search.html",
        {"q": query, "hits": hits, "error": error},
    )


@app.get("/api/search")
def api_search(
    q: str = Query(""),
    limit: int = Query(10, ge=1, le=25),
) -> dict[str, Any]:
    query = q.strip()
    if not query:
        return {"q": "", "hits": []}
    return {"q": query, "hits": _run_search(query, limit=limit)}


@app.get("/stock/{symbol}", response_class=HTMLResponse)
def stock_detail(
    request: Request,
    symbol: str,
    chart: str = Query("dashboard"),
    refresh: int = Query(0),
) -> HTMLResponse:
    symbol = normalize_symbol(symbol)
    if chart not in {"dashboard", "drawdown", "forecast"}:
        chart = "dashboard"
    df = load_leaderboard()
    row = None
    if not df.empty and symbol in set(df["symbol"].astype(str).map(normalize_symbol)):
        # Match both BRK-B and BRK.B style keys from older runs
        matched = df[df["symbol"].astype(str).map(normalize_symbol) == symbol]
        if not matched.empty:
            row = matched.iloc[0].to_dict()
    plots_dir = _paths()["plots"]
    metrics_dir = _paths()["metrics"]
    chart_path = plots_dir / symbol / f"{chart}.html"

    # Lazy-build full chart pack (dashboard / drawdown / 5y forecast) if any are missing
    needed = ("dashboard.html", "drawdown.html", "forecast.html")
    if refresh or any(not (plots_dir / symbol / name).exists() for name in needed):
        try:
            from trade_pl.plots import plot_symbol

            plot_symbol(symbol, metrics_dir, plots_dir)
        except Exception:
            # Fall back to forecast-only if full pack fails
            if chart == "forecast" and not chart_path.exists():
                try:
                    from trade_pl.forecast import forecast_symbol

                    forecast_symbol(symbol, metrics_dir, plots_dir)
                except Exception:
                    pass
        chart_path = plots_dir / symbol / f"{chart}.html"

    forecast_summary = None
    fjson = plots_dir / symbol / "forecast.json"
    if fjson.exists():
        try:
            forecast_summary = json.loads(fjson.read_text(encoding="utf-8"))
        except Exception:
            forecast_summary = None

    enrich_dir = _paths()["enrichment"]
    profile = load_profile(enrich_dir, symbol)
    # Always ensure quote + financials for searched / off-board names.
    from trade_pl.enrichment import fetch_profile, profile_needs_refresh

    if refresh or profile_needs_refresh(profile):
        try:
            profile = fetch_profile(symbol)
            save_profile(enrich_dir, profile)
        except Exception:
            profile = profile or None

    # Synthesize a display row from profile when not on the scored leaderboard.
    if row is None and profile:
        row = {
            "symbol": symbol,
            "sector": profile.get("sector") or "Unknown",
            "market": market_of(symbol),
            "last_price": profile.get("last_price"),
            "return_1d": profile.get("return_1d") or profile.get("day_change_pct"),
            "return_1w": profile.get("return_1w"),
            "return_1m": profile.get("return_1m"),
            "return_3m": profile.get("return_3m"),
            "return_6m": profile.get("return_6m"),
            "return_1y": profile.get("return_1y"),
            "score": None,
            "rank": None,
            "sharpe_60d": None,
            "max_drawdown": None,
            "on_board": False,
        }
    elif row is not None:
        row = {**row, "on_board": True}
        if profile and row.get("last_price") in (None, "") and profile.get("last_price") is not None:
            row["last_price"] = profile["last_price"]

    # Ensure optional metric keys exist so Jinja attr access never blows up
    if row is not None:
        for key in (
            "return_1d",
            "return_1w",
            "return_1m",
            "return_3m",
            "return_6m",
            "return_1y",
            "sharpe_60d",
            "max_drawdown",
            "score",
            "rank",
            "sector",
            "last_price",
            "on_board",
        ):
            row.setdefault(key, None)

    # Official SEC filings (10-K / 10-Q / 8-K) — fetch on demand if missing
    filings = None
    try:
        from trade_pl.sec_filings import (
            fetch_filings_for_symbol,
            load_filings,
            load_ticker_cik_map,
            save_filings,
        )

        filings_dir = _paths()["filings"]
        filings = load_filings(filings_dir, symbol)
        if refresh or filings is None or not (filings.get("filings") or filings.get("note")):
            cik_map = load_ticker_cik_map(ROOT / "data" / "sec_cik_map.json")
            filings = fetch_filings_for_symbol(symbol, cik_map)
            save_filings(filings_dir, filings)
    except Exception:
        filings = filings or None

    return templates.TemplateResponse(
        request,
        "ticker.html",
        {
            "symbol": symbol,
            "row": row,
            "chart": chart,
            "has_chart": chart_path.exists(),
            "profile": profile,
            "forecast": forecast_summary,
            "filings": filings,
        },
    )


@app.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    symbols: str = Query(""),
) -> HTMLResponse:
    df = load_leaderboard()
    wanted = [normalize_symbol(s) for s in symbols.split(",") if s.strip()][:5]
    rows: list[dict[str, Any]] = []
    if not df.empty and wanted:
        view = df.copy()
        view["_sym"] = view["symbol"].astype(str).map(normalize_symbol)
        rows = view[view["_sym"].isin(wanted)].sort_values("rank").drop(columns=["_sym"]).to_dict(orient="records")
    return templates.TemplateResponse(
        request,
        "compare.html",
        {"symbols_raw": symbols, "rows": rows},
    )


@app.get("/api/leaderboard")
def api_leaderboard() -> list[dict[str, Any]]:
    df = load_leaderboard()
    if df.empty:
        return []
    return df.to_dict(orient="records")


@app.get("/api/horizons")
def api_horizons() -> dict[str, Any]:
    return load_horizons()


@app.get("/api/sectors")
def api_sectors() -> dict[str, Any]:
    return load_sectors()


@app.get("/api/stock/{symbol}")
def api_stock(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    profile = load_profile(_paths()["enrichment"], symbol) or {}
    df = load_leaderboard()
    row = {}
    if not df.empty:
        matched = df[df["symbol"].astype(str).map(normalize_symbol) == symbol]
        if not matched.empty:
            row = matched.iloc[0].to_dict()
    return {"metrics": row, "profile": profile}


@app.get("/api/suggested_pick")
def api_pick() -> dict[str, Any]:
    return load_pick() or {"error": "no pick"}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "root": str(ROOT)}


def main() -> None:
    _paths()["plots"].mkdir(parents=True, exist_ok=True)
    uvicorn.run("trade_pl.web.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
