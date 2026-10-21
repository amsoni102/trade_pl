from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trade_pl.config import ROOT, load_config

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

app = FastAPI(title="trade_pl", description="Local stock research website")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


def _paths() -> dict[str, Path]:
    cfg = load_config()
    return {
        "reports": Path(cfg["paths"]["reports"]),
        "plots": Path(cfg["paths"]["plots"]),
        "metrics": Path(cfg["paths"]["data_metrics"]),
    }


# Mount plot directory at import time so /plots/... works without startup hooks.
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


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    sort: str = Query("score"),
    q: str = Query(""),
) -> HTMLResponse:
    df = load_leaderboard()
    pick = load_pick()
    manifest = load_manifest()
    sort_options = [
        ("score", "Score"),
        ("return_3m", "3M return"),
        ("return_1m", "1M return"),
        ("sharpe_60d", "Sharpe"),
        ("max_drawdown", "Max drawdown (least bad)"),
    ]
    rows: list[dict[str, Any]] = []
    if not df.empty:
        view = df.copy()
        if q.strip():
            view = view[view["symbol"].str.contains(q.strip().upper(), na=False)]
        ascending = sort == "max_drawdown"
        if sort in view.columns:
            view = view.sort_values(sort, ascending=ascending)
        else:
            view = view.sort_values("score", ascending=False)
        rows = view.to_dict(orient="records")

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
            "report_label": Path(manifest.get("report_dir", "reports/latest")).name,
            "sort": sort,
            "q": q,
            "sort_options": sort_options,
        },
    )


@app.get("/stock/{symbol}", response_class=HTMLResponse)
def stock_detail(
    request: Request,
    symbol: str,
    chart: str = Query("dashboard"),
) -> HTMLResponse:
    symbol = symbol.upper()
    if chart not in {"dashboard", "drawdown"}:
        chart = "dashboard"
    df = load_leaderboard()
    row = None
    if not df.empty and symbol in set(df["symbol"]):
        row = df[df["symbol"] == symbol].iloc[0].to_dict()
    chart_path = _paths()["plots"] / symbol / f"{chart}.html"
    return templates.TemplateResponse(
        request,
        "ticker.html",
        {
            "symbol": symbol,
            "row": row,
            "chart": chart,
            "has_chart": chart_path.exists(),
        },
    )


@app.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    symbols: str = Query(""),
) -> HTMLResponse:
    df = load_leaderboard()
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()][:5]
    rows: list[dict[str, Any]] = []
    if not df.empty and wanted:
        rows = df[df["symbol"].isin(wanted)].sort_values("rank").to_dict(orient="records")
    return templates.TemplateResponse(
        request,
        "compare.html",
        {
            "symbols_raw": symbols,
            "rows": rows,
        },
    )


@app.get("/api/leaderboard")
def api_leaderboard() -> list[dict[str, Any]]:
    df = load_leaderboard()
    if df.empty:
        return []
    return df.to_dict(orient="records")


@app.get("/api/suggested_pick")
def api_pick() -> dict[str, Any]:
    return load_pick() or {"error": "no pick"}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "root": str(ROOT)}


def main() -> None:
    # ensure plots mount directory exists before uvicorn loads
    _paths()["plots"].mkdir(parents=True, exist_ok=True)
    uvicorn.run("trade_pl.web.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
