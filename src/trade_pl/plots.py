from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trade_pl.forecast import forecast_symbol

logger = logging.getLogger(__name__)

# ~5.5 calendar years so dashboards cover multi-year history.
DEFAULT_CHART_LOOKBACK_DAYS = 2000


def _load_series(metrics_dir: Path, symbol: str) -> pd.DataFrame | None:
    path = metrics_dir / f"{symbol}_series.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def _series_path(metrics_dir: Path, symbol: str) -> Path:
    return metrics_dir / f"{symbol}_series.parquet"


def _chart_paths(plots_dir: Path, symbol: str) -> dict[str, Path]:
    out = plots_dir / symbol
    return {
        "dir": out,
        "dashboard": out / "dashboard.html",
        "drawdown": out / "drawdown.html",
        "forecast": out / "forecast.html",
        "forecast_json": out / "forecast.json",
    }


def charts_ready(plots_dir: Path, symbol: str) -> bool:
    paths = _chart_paths(plots_dir, symbol)
    return paths["dashboard"].exists() and paths["drawdown"].exists()


def make_dashboard(series: pd.DataFrame, symbol: str) -> go.Figure:
    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.4, 0.15, 0.2, 0.25],
        subplot_titles=(f"{symbol} price", "Volume", "RSI(14)", "MACD"),
    )
    fig.add_trace(
        go.Scatter(x=series["date"], y=series["adj_close"], name="Adj Close", line=dict(color="#0f766e")),
        row=1,
        col=1,
    )
    for col, color in [("sma_20", "#ca8a04"), ("sma_50", "#2563eb"), ("sma_200", "#9333ea")]:
        if col in series.columns:
            fig.add_trace(
                go.Scatter(x=series["date"], y=series[col], name=col.upper(), line=dict(width=1, color=color)),
                row=1,
                col=1,
            )
    fig.add_trace(
        go.Bar(x=series["date"], y=series["volume"], name="Volume", marker_color="#94a3b8"),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=series["date"], y=series["rsi_14"], name="RSI", line=dict(color="#ea580c")),
        row=3,
        col=1,
    )
    fig.add_hline(y=70, line_dash="dot", line_color="#dc2626", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#16a34a", row=3, col=1)
    fig.add_trace(
        go.Scatter(x=series["date"], y=series["macd"], name="MACD", line=dict(color="#0284c7")),
        row=4,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=series["date"], y=series["macd_signal"], name="Signal", line=dict(color="#db2777")),
        row=4,
        col=1,
    )
    fig.add_trace(
        go.Bar(x=series["date"], y=series["macd_hist"], name="Hist", marker_color="#64748b"),
        row=4,
        col=1,
    )
    fig.update_layout(
        height=900,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def make_drawdown(series: pd.DataFrame, symbol: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=series["date"],
            y=series["drawdown"] * 100,
            fill="tozeroy",
            name="Drawdown %",
            line=dict(color="#b91c1c"),
        )
    )
    fig.update_layout(
        title=f"{symbol} drawdown",
        template="plotly_white",
        yaxis_title="Drawdown %",
        height=350,
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return fig


def plot_symbol(symbol: str, metrics_dir: Path, plots_dir: Path) -> Path | None:
    series = _load_series(metrics_dir, symbol)
    if series is None or series.empty:
        return None
    paths = _chart_paths(plots_dir, symbol)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    dash = make_dashboard(series, symbol)
    dash.write_html(paths["dashboard"], include_plotlyjs="cdn", full_html=True)
    dd = make_drawdown(series, symbol)
    dd.write_html(paths["drawdown"], include_plotlyjs="cdn", full_html=True)
    forecast_symbol(symbol, metrics_dir, plots_dir)
    charts = ["dashboard.html", "drawdown.html", "forecast.html"]
    (paths["dir"] / "meta.json").write_text(
        '{"symbol":"%s","charts":%s}' % (symbol, __import__("json").dumps(charts)),
        encoding="utf-8",
    )
    return paths["dir"]


def ensure_metrics_series(
    symbol: str,
    *,
    raw_dir: Path,
    metrics_dir: Path,
    lookback_days: int = DEFAULT_CHART_LOOKBACK_DAYS,
    force_ingest: bool = False,
) -> pd.DataFrame | None:
    """Download OHLCV if needed, compute indicator series, return it."""
    from trade_pl.ingest import ingest_one, ticker_path
    from trade_pl.metrics import compute_series

    raw_path = ticker_path(raw_dir, symbol)
    if force_ingest or not raw_path.exists() or raw_path.stat().st_size < 100:
        ok_sym, success, msg = ingest_one(
            symbol,
            raw_dir,
            lookback_days=lookback_days,
            force=force_ingest or not raw_path.exists(),
            min_interval=0.25,
        )
        if not success and not raw_path.exists():
            logger.warning("cannot build charts for %s: ingest failed (%s)", symbol, msg)
            return None

    if not raw_path.exists():
        return None

    try:
        df = pd.read_parquet(raw_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("read raw failed %s: %s", symbol, exc)
        return None
    if df is None or df.empty or len(df) < 30:
        return None

    series = compute_series(df)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    series.to_parquet(_series_path(metrics_dir, symbol), index=False)
    return series


def ensure_symbol_charts(
    symbol: str,
    *,
    raw_dir: Path,
    metrics_dir: Path,
    plots_dir: Path,
    lookback_days: int = DEFAULT_CHART_LOOKBACK_DAYS,
    force: bool = False,
) -> dict[str, Any]:
    """Guarantee dashboard + drawdown (+ forecast when possible) for any ticker.

    Used by the website so searched / off-board names get the same multi-year
    charts as pipeline top-K names.
    """
    paths = _chart_paths(plots_dir, symbol)
    ready = charts_ready(plots_dir, symbol) and paths["forecast"].exists()
    if ready and not force:
        return {
            "ok": True,
            "symbol": symbol,
            "cached": True,
            "dashboard": paths["dashboard"].exists(),
            "drawdown": paths["drawdown"].exists(),
            "forecast": paths["forecast"].exists(),
        }

    series = _load_series(metrics_dir, symbol)
    span_days = 0
    if series is not None and not series.empty and "date" in series.columns:
        try:
            span_days = int((pd.to_datetime(series["date"]).max() - pd.to_datetime(series["date"]).min()).days)
        except Exception:
            span_days = 0
    # Rebuild when missing, charts absent, or history shorter than ~3 years.
    needs_series = (
        force
        or series is None
        or series.empty
        or len(series) < 250
        or span_days < 900
        or not charts_ready(plots_dir, symbol)
    )
    if needs_series:
        series = ensure_metrics_series(
            symbol,
            raw_dir=raw_dir,
            metrics_dir=metrics_dir,
            lookback_days=lookback_days,
            # Re-download when the cached history is too short for multi-year charts.
            force_ingest=force or span_days < 900 or (series is not None and len(series) < 250),
        )

    if series is None or series.empty:
        return {
            "ok": False,
            "symbol": symbol,
            "error": "no-price-history",
            "dashboard": False,
            "drawdown": False,
            "forecast": False,
        }

    out = plot_symbol(symbol, metrics_dir, plots_dir)
    return {
        "ok": out is not None,
        "symbol": symbol,
        "cached": False,
        "bars": int(len(series)),
        "dashboard": paths["dashboard"].exists(),
        "drawdown": paths["drawdown"].exists(),
        "forecast": paths["forecast"].exists(),
    }


def plot_many(
    symbols: list[str],
    metrics_dir: Path,
    plots_dir: Path,
    workers: int = 4,
) -> list[str]:
    """Build dashboard / drawdown / forecast charts. Skip symbols that already have a dashboard."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    need = [
        s
        for s in symbols
        if not (plots_dir / s / "dashboard.html").exists()
        or not (plots_dir / s / "drawdown.html").exists()
    ]
    if not need:
        return [s for s in symbols if (plots_dir / s / "dashboard.html").exists()]

    done: list[str] = [s for s in symbols if s not in need and (plots_dir / s / "dashboard.html").exists()]

    def _one(sym: str) -> str | None:
        try:
            if plot_symbol(sym, metrics_dir, plots_dir):
                return sym
        except Exception:
            return None
        return None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(_one, s) for s in need]
        for fut in as_completed(futs):
            sym = fut.result()
            if sym:
                done.append(sym)
    return done
