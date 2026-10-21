from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# ~5 trading years
FORECAST_TRADING_DAYS = 252 * 5
TRADING_DAYS_YEAR = 252


def _estimate_params(px: pd.Series) -> dict[str, float]:
    """Estimate annualized drift/vol from history, with sanity caps."""
    rets = np.log(px.astype(float) / px.astype(float).shift(1)).dropna()
    if len(rets) < 40:
        raise ValueError("not enough history for forecast")

    mu_d = float(rets.mean())
    sig_d = float(rets.std(ddof=1))
    if not np.isfinite(sig_d) or sig_d <= 1e-8:
        sig_d = 0.01

    # Blend full-sample drift with trailing ~1y drift (less noisy than raw mean alone)
    tail = rets.tail(min(252, len(rets)))
    mu_1y = float(tail.mean())
    mu_d_blend = 0.5 * mu_d + 0.5 * mu_1y

    mu_ann = mu_d_blend * TRADING_DAYS_YEAR
    sig_ann = sig_d * np.sqrt(TRADING_DAYS_YEAR)

    # Shrink aggressive recent winners toward a long-run equity prior (~8%)
    # so 5y charts stay interpretable (still labeled as a model, not truth).
    prior = 0.08
    shrink = 0.55
    mu_ann = float(prior * shrink + mu_ann * (1.0 - shrink))
    mu_ann = float(np.clip(mu_ann, -0.25, 0.28))
    sig_ann = float(np.clip(sig_ann, 0.10, 0.55))

    return {
        "mu_daily": mu_ann / TRADING_DAYS_YEAR,
        "sigma_daily": sig_ann / np.sqrt(TRADING_DAYS_YEAR),
        "mu_ann": mu_ann,
        "sigma_ann": sig_ann,
        "n_history": int(len(rets)),
    }


def build_forecast(
    series: pd.DataFrame,
    years: float = 5.0,
    n_paths: int = 400,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Geometric Brownian Motion fan chart from historical drift/vol.

    Returns history + projected percentiles. This is a statistical projection,
    not a prediction of what will happen.
    """
    df = series.dropna(subset=["adj_close", "date"]).copy()
    df["date"] = pd.to_datetime(df["date"])
    px = df["adj_close"].astype(float)
    params = _estimate_params(px)

    last_price = float(px.iloc[-1])
    last_date = pd.Timestamp(df["date"].iloc[-1])
    horizon = int(round(TRADING_DAYS_YEAR * years))

    mu = params["mu_daily"]
    sigma = params["sigma_daily"]
    rng = np.random.default_rng(seed)

    # Monte Carlo paths under GBM: S_{t+1} = S_t * exp((mu - 0.5 sig^2) dt + sig sqrt(dt) Z)
    dt = 1.0
    drift = (mu - 0.5 * sigma**2) * dt
    shocks = rng.normal(0.0, 1.0, size=(n_paths, horizon))
    log_increments = drift + sigma * np.sqrt(dt) * shocks
    log_paths = np.cumsum(log_increments, axis=1)
    paths = last_price * np.exp(log_paths)

    # Analytical median / bands (closed form) for smooth center line
    t = np.arange(1, horizon + 1)
    median = last_price * np.exp(mu * t)
    p10 = last_price * np.exp(mu * t + sigma * np.sqrt(t) * (-1.2816))
    p25 = last_price * np.exp(mu * t + sigma * np.sqrt(t) * (-0.6745))
    p75 = last_price * np.exp(mu * t + sigma * np.sqrt(t) * (0.6745))
    p90 = last_price * np.exp(mu * t + sigma * np.sqrt(t) * (1.2816))

    # Also report MC percentiles (more realistic fat paths)
    mc_p10 = np.percentile(paths, 10, axis=0)
    mc_p50 = np.percentile(paths, 50, axis=0)
    mc_p90 = np.percentile(paths, 90, axis=0)

    future_dates = pd.bdate_range(last_date + pd.Timedelta(days=1), periods=horizon)

    # Keep full available history on chart (usually ~1y+), not a tiny stub.
    hist_tail = df.tail(min(780, len(df)))

    # Downsample forecast to ~weekly for a cleaner line (still ends at 5y)
    step = 5
    idx = list(range(0, horizon, step))
    if idx[-1] != horizon - 1:
        idx.append(horizon - 1)

    summary = {
        "model": "gbm_historical_drift_vol_shrunk",
        "label": "5y statistical projection — not a guarantee / not advice",
        "years": years,
        "last_price": last_price,
        "as_of": str(last_date.date()),
        "expected_cagr": params["mu_ann"],
        "hist_vol_ann": params["sigma_ann"],
        "n_history_days": params["n_history"],
        "projected_median_5y": float(median[-1]),
        "projected_p10_5y": float(p10[-1]),
        "projected_p90_5y": float(p90[-1]),
        "projected_mc_p50_5y": float(mc_p50[-1]),
        "upside_pct_median": float(median[-1] / last_price - 1.0),
        "band_low_pct": float(p10[-1] / last_price - 1.0),
        "band_high_pct": float(p90[-1] / last_price - 1.0),
    }

    def _take(arr: np.ndarray) -> list[float]:
        return [float(arr[i]) for i in idx]

    dates_s = [future_dates[i].strftime("%Y-%m-%d") for i in idx]

    return {
        "summary": summary,
        "history": {
            "date": hist_tail["date"].dt.strftime("%Y-%m-%d").tolist(),
            "price": hist_tail["adj_close"].astype(float).tolist(),
        },
        "forecast": {
            "date": dates_s,
            "median": _take(median),
            "p10": _take(p10),
            "p25": _take(p25),
            "p75": _take(p75),
            "p90": _take(p90),
            "mc_p10": _take(mc_p10),
            "mc_p50": _take(mc_p50),
            "mc_p90": _take(mc_p90),
        },
    }


def make_forecast_figure(symbol: str, payload: dict[str, Any]) -> go.Figure:
    hist = payload["history"]
    fc = payload["forecast"]
    s = payload["summary"]

    fig = go.Figure()

    # Outer band first (drawn under), then inner band — no spaghetti paths.
    fig.add_trace(
        go.Scatter(
            x=fc["date"] + fc["date"][::-1],
            y=fc["p90"] + fc["p10"][::-1],
            fill="toself",
            fillcolor="rgba(14, 116, 144, 0.12)",
            line=dict(width=0),
            name="Likely range (p10–p90)",
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=fc["date"] + fc["date"][::-1],
            y=fc["p75"] + fc["p25"][::-1],
            fill="toself",
            fillcolor="rgba(14, 116, 144, 0.22)",
            line=dict(width=0),
            name="Core range (p25–p75)",
            hoverinfo="skip",
        )
    )

    # Edge lines for the bands (readable without fill alone)
    fig.add_trace(
        go.Scatter(
            x=fc["date"],
            y=fc["p90"],
            name="High (p90)",
            line=dict(color="#94a3b8", width=1, dash="dot"),
            hovertemplate="p90: %{y:$.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=fc["date"],
            y=fc["p10"],
            name="Low (p10)",
            line=dict(color="#94a3b8", width=1, dash="dot"),
            hovertemplate="p10: %{y:$.2f}<extra></extra>",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=hist["date"],
            y=hist["price"],
            name="History",
            line=dict(color="#0f766e", width=2.8),
            hovertemplate="History: %{y:$.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=fc["date"],
            y=fc["median"],
            name="Projected median",
            line=dict(color="#ea580c", width=3),
            hovertemplate="Median: %{y:$.2f}<extra></extra>",
        )
    )

    # ~Annual markers on the median for easy reading
    year_x, year_y = [], []
    for i in range(51, len(fc["date"]), 52):
        year_x.append(fc["date"][i])
        year_y.append(fc["median"][i])
    if fc["date"]:
        year_x.append(fc["date"][-1])
        year_y.append(fc["median"][-1])
    fig.add_trace(
        go.Scatter(
            x=year_x,
            y=year_y,
            mode="markers+text",
            name="Year marks",
            marker=dict(size=9, color="#ea580c", line=dict(width=1, color="white")),
            text=[f"${v:,.0f}" for v in year_y],
            textposition="top center",
            textfont=dict(size=11, color="#9a3412"),
            hovertemplate="%{x}<br>%{y:$.2f}<extra></extra>",
        )
    )

    # Scale Y to history + p90 (ignore crazy outliers that used to dominate)
    y_hist = hist["price"] or [s["last_price"]]
    y_max = max(max(y_hist), max(fc["p90"]), max(fc["median"])) * 1.08
    y_min = max(0.0, min(min(y_hist), min(fc["p10"])) * 0.92)

    as_of = hist["date"][-1] if hist["date"] else s.get("as_of")
    fig.add_vline(x=as_of, line_width=1, line_dash="dash", line_color="#cbd5e1")

    fig.update_layout(
        title=dict(
            text=f"{symbol} · 5-year outlook",
            x=0.01,
            xanchor="left",
            font=dict(size=18, color="#0f172a"),
        ),
        annotations=[
            dict(
                text=(
                    f"Model CAGR {s['expected_cagr']*100:.1f}% · vol {s['hist_vol_ann']*100:.0f}% · "
                    "not a prediction / not advice"
                ),
                xref="paper",
                yref="paper",
                x=0,
                y=1.12,
                showarrow=False,
                font=dict(size=12, color="#64748b"),
                xanchor="left",
            )
        ],
        template="plotly_white",
        height=560,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            x=0,
            bgcolor="rgba(255,255,255,0.8)",
            font=dict(size=12),
        ),
        margin=dict(l=55, r=24, t=70, b=90),
        yaxis=dict(
            title="Adj. price ($)",
            range=[y_min, y_max],
            gridcolor="#e2e8f0",
            zeroline=False,
            tickprefix="$",
            separatethousands=True,
        ),
        xaxis=dict(title="", gridcolor="#f1f5f9", showgrid=True),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    return fig


def forecast_symbol(symbol: str, metrics_dir: Path, plots_dir: Path) -> dict[str, Any] | None:
    path = metrics_dir / f"{symbol}_series.parquet"
    if not path.exists():
        return None
    series = pd.read_parquet(path)
    try:
        payload = build_forecast(series, years=5.0)
    except Exception:
        return None

    out = plots_dir / symbol
    out.mkdir(parents=True, exist_ok=True)
    fig = make_forecast_figure(symbol, payload)
    fig.write_html(out / "forecast.html", include_plotlyjs="cdn", full_html=True)
    (out / "forecast.json").write_text(json.dumps(payload["summary"], indent=2), encoding="utf-8")
    return payload["summary"]


def forecast_many(symbols: list[str], metrics_dir: Path, plots_dir: Path) -> list[str]:
    done: list[str] = []
    for sym in symbols:
        if forecast_symbol(sym, metrics_dir, plots_dir):
            done.append(sym)
    return done
