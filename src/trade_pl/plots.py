from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trade_pl.forecast import forecast_symbol


def _load_series(metrics_dir: Path, symbol: str) -> pd.DataFrame | None:
    path = metrics_dir / f"{symbol}_series.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


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
    out = plots_dir / symbol
    out.mkdir(parents=True, exist_ok=True)
    dash = make_dashboard(series, symbol)
    dash.write_html(out / "dashboard.html", include_plotlyjs="cdn", full_html=True)
    dd = make_drawdown(series, symbol)
    dd.write_html(out / "drawdown.html", include_plotlyjs="cdn", full_html=True)
    forecast_symbol(symbol, metrics_dir, plots_dir)
    charts = ["dashboard.html", "drawdown.html", "forecast.html"]
    (out / "meta.json").write_text(
        '{"symbol":"%s","charts":%s}' % (symbol, __import__("json").dumps(charts)),
        encoding="utf-8",
    )
    return out


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
