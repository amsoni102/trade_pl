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
from trade_pl.enrichment import load_profile

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def _paths() -> dict[str, Path]:
    cfg = load_config()
    return {
        "reports": Path(cfg["paths"]["reports"]),
        "plots": Path(cfg["paths"]["plots"]),
        "metrics": Path(cfg["paths"]["data_metrics"]),
        "enrichment": Path(cfg["paths"].get("data_enrichment", ROOT / "data" / "enrichment")),
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Keep scanning public finance RSS / news feeds for as long as the website is up.
    universe: list[str] = []
    lb = _paths()["reports"] / "latest" / "leaderboard.csv"
    if lb.exists():
        try:
            universe = pd.read_csv(lb)["symbol"].astype(str).head(300).tolist()
        except Exception:
            universe = []
    scanner = get_scanner(cache_path=ROOT / "data" / "breaking_news.json", universe=universe)
    scanner.start()
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
    return pd.read_csv(path)


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


templates.env.filters["money"] = _fmt_money


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
            "breaking_news": (news.get("items") or [])[:12],
            "news_urgent": news.get("breaking") or [],
            "news_tickers": news.get("with_tickers") or [],
            "news_updated_at": news.get("updated_at"),
            "news_scanning": news.get("scanning"),
            "news_sources_ok": news.get("sources_ok") or [],
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


@app.get("/stock/{symbol}", response_class=HTMLResponse)
def stock_detail(
    request: Request,
    symbol: str,
    chart: str = Query("dashboard"),
    refresh: int = Query(0),
) -> HTMLResponse:
    symbol = symbol.upper().replace(".", "-")
    if chart not in {"dashboard", "drawdown", "forecast"}:
        chart = "dashboard"
    df = load_leaderboard()
    row = None
    if not df.empty and symbol in set(df["symbol"]):
        row = df[df["symbol"] == symbol].iloc[0].to_dict()
    plots_dir = _paths()["plots"]
    metrics_dir = _paths()["metrics"]
    chart_path = plots_dir / symbol / f"{chart}.html"

    # Lazy-build 5y forecast chart on first open if missing
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
    # Lazy-fetch news/financials on first open (or ?refresh=1) so non-top names still get detail.
    if profile is None or refresh or not (profile.get("news") or profile.get("financials", {}).get("income")):
        try:
            from trade_pl.enrichment import fetch_profile

            profile = fetch_profile(symbol)
            enrich_dir.mkdir(parents=True, exist_ok=True)
            (enrich_dir / f"{symbol}.json").write_text(
                json.dumps(profile, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            profile = profile or None
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
        },
    )


@app.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    symbols: str = Query(""),
) -> HTMLResponse:
    df = load_leaderboard()
    wanted = [s.strip().upper().replace(".", "-") for s in symbols.split(",") if s.strip()][:5]
    rows: list[dict[str, Any]] = []
    if not df.empty and wanted:
        rows = df[df["symbol"].isin(wanted)].sort_values("rank").to_dict(orient="records")
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
    symbol = symbol.upper().replace(".", "-")
    profile = load_profile(_paths()["enrichment"], symbol) or {}
    df = load_leaderboard()
    row = {}
    if not df.empty and symbol in set(df["symbol"]):
        row = df[df["symbol"] == symbol].iloc[0].to_dict()
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
