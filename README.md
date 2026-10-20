# Automated Stock Trading Analysis

Procedure and architecture plan for screening hundreds of top-performing stocks, computing trading metrics, and plotting results per ticker. This document is the source of truth for a later detailed analysis website.

---

## Goal

Build a repeatable pipeline that:

1. Selects a universe of **best-performing stocks** (e.g. top N by return, liquidity, or sector leaders).
2. Pulls historical and (optionally) near-real-time market data for each ticker.
3. Computes a standard set of **technical and risk metrics**.
4. Generates **per-stock charts** and a cross-stock ranking / comparison layer.
5. Stores outputs so a future **web app** can browse tickers, metrics, and plots in detail.

This is **analysis and decision support**, not live order execution. Live trading can be a later phase once signals are validated.

---

## Scale Target: 10k–30k Stocks in ~10 Minutes

### Short answer

**Yes, scoring 10k–30k tickers in about 10 minutes is realistic** — but only if market data is **already on disk** (or streamed in bulk). The bottleneck is almost never the math; it is downloading prices under API rate limits.

**No system can reliably name “the single best stock for next month.”** What we can do is rank a huge universe with a transparent score and surface a **small shortlist** (e.g. top 5–20) plus one **suggested pick** for the UI. Treat that as a research candidate, not a guarantee.

### What fits in 10 minutes vs what does not

| Work | In ~10 min? | Notes |
|------|-------------|--------|
| Score 10k–30k tickers from **local Parquet/DB** (returns, RSI, Sharpe, etc.) | Yes | Vectorized pandas/polars; often 1–5 minutes on a decent machine |
| Generate full plot dashboards for **all** 30k | No | Too much I/O; plot **top K only** (e.g. top 50) |
| Fresh download of 30k histories from free APIs every run | No | Rate limits; can take hours |
| Nightly bulk refresh of the full universe | Yes (offline) | Separate job; scoring job then stays fast |

### Recommended architecture for this scale

```
Nightly (or weekend)          On demand / morning (~10 min)
─────────────────────         ─────────────────────────────
Bulk ingest 10k–30k    ──▶    Load cache
Write data/raw/*.parquet      Compute metrics (all tickers)
                              Rank → top K
                              Plots + detail only for top K
                              Output: leaderboard + “suggested pick”
```

1. **Universe:** US listed equities first (~5k–10k liquid names). Push toward 30k only with a paid bulk vendor (includes thin OTC names that need strong liquidity filters).
2. **Cache first:** Daily OHLCV for the full universe lives locally. Scoring never waits on the network.
3. **Two-stage select:** Cheap filters (price, volume, listing status) → full metrics on survivors → rank.
4. **Plots on demand:** Full charts only for the shortlist (or when a user opens a ticker on the website).
5. **Output contract:** `leaderboard.csv` + `suggested_pick.json` (ticker, score, metric breakdown, as-of date, model version). Never claim certainty about next month’s winner.

### AI in the loop vs automated Python — recommendation

| Approach | Use for 10k–30k screen? | Role |
|----------|-------------------------|------|
| **Deterministic Python (factors / rules)** | **Primary — yes** | Fast, cheap, reproducible, testable. Rank by momentum, quality, risk-adjusted return, liquidity. |
| **Classical ML (optional later)** | Maybe | Learn a ranking model from historical features; still runs as batch Python, not chat. Needs careful backtests to avoid overfitting. |
| **LLM / generative AI on every ticker** | **No** | Too slow/expensive at 30k; adds little over numbers for ranking. |
| **LLM on the shortlist only** | Optional | Explain *why* the top 5 scored well, summarize risks, draft a human-readable note for the website. |

**Suggestion: keep AI out of the ranking loop for v1.** Use automated Python for the full universe. If you want AI later, add it as an **explanation layer on the top N**, not as the thing that picks winners from 30k names.

Why: at this scale, speed, auditability, and cost matter more than narrative. Factor scores are easy to version (`metrics_v1`) and backtest; LLM picks are not.

### How we are supposed to run things

**Local / early development**

```bash
# 1) Refresh cache (slow; run overnight or weekly at first)
python -m trade_pl.ingest --universe all --mode bulk

# 2) Score everything from cache (target: ≤ 10 minutes)
python -m trade_pl.run --from-cache --out reports/$(date +%F)

# 3) Inspect
# reports/.../leaderboard.csv
# reports/.../suggested_pick.json
# plots/{TOP_TICKERS}/...
```

**Steady state (when serious)**

| Job | When | What |
|-----|------|------|
| **Ingest worker** | Nightly after market close | Update OHLCV for full universe into Parquet/DB |
| **Score worker** | After ingest (or on button click) | Metrics + rank + suggested pick; ≤ ~10 min |
| **Plot worker** | After score, or lazy on web request | Charts for top K / requested ticker |
| **Web app** | Always on | Reads latest report; does not re-download the market |

Orchestration options (pick one later): local cron, GitHub Actions (small universes), or a small cloud VM / container with a scheduler. The website should **consume artifacts**, not re-run 30k downloads per page view.

### Honest expectation for “best for next month”

- Automated ranking can encode a **hypothesis** (e.g. “high 3–6m momentum, adequate liquidity, controlled drawdown”).
- That hypothesis must be **backtested** (does the top pick / top decile tend to beat a benchmark next month?).
- Even a good process will often be wrong on any single name; prefer **diversified top-N** or position sizing rules over all-in on one ticker.
- Label UI copy as **“model suggested candidate”**, not financial advice.

---

## High-Level Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Universe       │────▶│  Data ingest     │────▶│  Metric engine  │
│  selection      │     │  (OHLCV, volume) │     │  (per ticker)   │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                         │
                        ┌──────────────────┐             │
                        │  Ranking /       │◀────────────┤
                        │  cross-stock     │             │
                        │  summary         │             │
                        └────────┬─────────┘             │
                                 │                       │
                        ┌────────▼─────────┐     ┌───────▼────────┐
                        │  Plot generation │     │  Storage       │
                        │  (1 folder /     │────▶│  (DB + files)  │
                        │   ticker)        │     └───────┬────────┘
                        └──────────────────┘             │
                                                 ┌───────▼────────┐
                                                 │  Future web UI │
                                                 │  (detail views)│
                                                 └────────────────┘
```

---

## Phase 0 — Decisions Before Coding

| Decision | Options | Recommendation |
|----------|---------|----------------|
| Market | US equities first | Start with S&P 500 / Nasdaq-100 |
| “Best performing” definition | 1m / 3m / 6m / 1y return; volume filter | Composite score: return + liquidity + volatility cap |
| Universe size | 50 → 200 → 500+ | Start with ~100, scale to hundreds |
| Data source | Yahoo Finance, Polygon, Alpha Vantage, Tiingo | Free/dev: Yahoo; production: paid API with rate limits |
| Cadence | On-demand, daily batch, intraday | Daily batch first |
| Language | Python | Python (pandas, numpy, matplotlib/plotly) |
| Storage | CSV/Parquet + SQLite → Postgres | Parquet + SQLite for v1; Postgres for web |

---

## Phase 1 — Universe Selection

**Purpose:** Decide *which* stocks enter the analysis run.

### Procedure

1. Load a broad list (e.g. S&P 500 constituents or a static CSV of tickers).
2. Fetch recent price/volume for the lookback window (e.g. 6 months).
3. Filter out illiquid names (min average daily volume, min price).
4. Rank by a composite score, for example:
   - **Momentum:** trailing return over 1m / 3m / 6m
   - **Quality filters:** max drawdown not worse than threshold; ATR or realized vol within band
   - **Liquidity:** average dollar volume
5. Take the top **N** (configurable, default 100–300).
6. Persist the selected universe with timestamp: `universes/YYYY-MM-DD.json`.

### Output

- List of tickers for this run
- Selection criteria snapshot (so results are reproducible)

---

## Phase 2 — Data Ingest

**Purpose:** Reliable OHLCV (and optional fundamentals) for every selected ticker.

### Procedure

1. For each ticker, download (or load cached) daily OHLCV for the analysis window (e.g. 1–2 years).
2. Normalize columns: `date`, `open`, `high`, `low`, `close`, `volume`, `adj_close`.
3. Handle corporate actions via adjusted close where available.
4. Cache raw data under `data/raw/{TICKER}.parquet` to avoid re-hitting APIs.
5. Log failed tickers; continue the batch (do not fail the whole run).

### Data quality checks

- No gaps longer than X trading days without a note
- Positive volume on most days
- Prices > 0; OHLC consistency (`low ≤ open/close ≤ high`)

---

## Phase 3 — Metric Engine (Per Stock)

**Purpose:** Same metric set for every ticker so comparison is fair.

### Core metrics (v1)

| Category | Metrics |
|----------|---------|
| Returns | Daily return, cumulative return, 1m/3m/6m/1y return |
| Trend | SMA(20/50/200), EMA(12/26), price vs SMA |
| Momentum | RSI(14), MACD, rate of change |
| Volatility | Realized vol (20d), ATR(14), Bollinger width |
| Risk | Max drawdown, Sharpe (simple), Sortino (optional) |
| Volume | Relative volume, volume SMA |
| Signals (optional) | Golden/death cross flags, RSI overbought/oversold |

### Procedure

1. Load cleaned series for ticker.
2. Compute indicators as columns on a DataFrame.
3. Compute summary scalars (latest RSI, 3m return, Sharpe, max DD, etc.).
4. Write:
   - Time series: `data/metrics/{TICKER}_series.parquet`
   - Summary row: append to `data/metrics/summary.csv` (or DB table)

### Cross-stock layer

After all tickers finish:

- Rank by each summary metric
- Build a leaderboard table (top return, best Sharpe, lowest drawdown among winners, etc.)
- Export `reports/YYYY-MM-DD/leaderboard.csv`

---

## Phase 4 — Plotting (Per Stock)

**Purpose:** Visual artifacts for human review and for the future website.

### Charts per ticker (minimum set)

1. **Price + moving averages** — close with SMA 20/50/200  
2. **Volume** — bar chart under price or separate panel  
3. **RSI** — with 30/70 guides  
4. **MACD** — line, signal, histogram  
5. **Drawdown** — underwater chart from peak  
6. **Cumulative return** — vs optional benchmark (e.g. SPY)

### Implementation notes

- Prefer **Plotly** (interactive HTML) for the future website; Matplotlib PNG for static reports.
- One folder per ticker: `plots/{TICKER}/price.html`, `rsi.html`, …
- Also generate a single multi-panel “dashboard” image/HTML per ticker: `plots/{TICKER}/dashboard.html`
- Use consistent date ranges and styling across all tickers.

### Batch behavior

- Parallelize plot generation carefully (I/O bound); cap workers.
- Skip plot if series too short; log reason.

---

## Phase 5 — Run Orchestration

**Single entrypoint** (conceptual):

```bash
python -m trade_pl.run --universe-size 200 --lookback-days 365 --out reports/2026-10-20
```

### Pipeline steps in order

1. Select universe  
2. Ingest / refresh data  
3. Compute metrics  
4. Generate plots  
5. Write leaderboard + run manifest  

### Run manifest (`reports/.../manifest.json`)

Record:

- Timestamp, config (N, lookback, metric params)
- Tickers included / failed
- Data source and version
- Paths to summary, plots, leaderboard

Re-runs with the same config should be comparable.

---

## Suggested Repo Layout (when implementing)

```
trade_pl/
├── README.md                 # this document
├── LICENSE.md
├── requirements.txt
├── config/
│   └── default.yaml          # N, lookbacks, thresholds, API keys via env
├── src/trade_pl/
│   ├── universe.py           # selection / ranking
│   ├── ingest.py             # data download + cache
│   ├── metrics.py            # indicators + summaries
│   ├── plots.py              # per-ticker charts
│   ├── rank.py               # cross-stock leaderboard
│   └── run.py                # CLI orchestration
├── data/
│   ├── raw/                  # cached OHLCV
│   └── metrics/              # computed series + summary
├── plots/                    # per-ticker plot outputs
├── reports/                  # dated run outputs + manifest
└── tests/
    └── ...
```

---

## Future Website (Detail Analysis)

When you turn this into a web product, reuse the same pipeline outputs; the UI should mostly **read** stored artifacts.

### Recommended product surfaces

| Page | Content |
|------|---------|
| **Home / Leaderboard** | Top stocks by selected metric; filters (sector, return window, Sharpe) |
| **Ticker detail** | All plots + latest metric cards + short narrative stats |
| **Compare** | 2–5 tickers side by side (returns, vol, drawdown) |
| **Run history** | Past daily batches; diff who entered/left the top N |
| **Methodology** | Link back to this README (how scores and charts are defined) |

### Backend sketch

- Batch job (cron / GitHub Action / cloud worker) runs the pipeline daily.
- Results land in Postgres (summaries) + object storage or static hosting (HTML/PNG plots).
- API: `GET /api/runs/latest`, `GET /api/tickers/{symbol}`, `GET /api/leaderboard?metric=sharpe`.
- Frontend: Next.js or similar; embed Plotly HTML or re-render from series JSON.

### What to keep stable for the website

- **Metric definitions** (document formulas; version them, e.g. `metrics_v1`)
- **File/DB schema** for summary rows and series
- **Ticker detail URL shape:** `/stock/{SYMBOL}` → maps 1:1 to `plots/{SYMBOL}/` + summary row

Do not redesign metrics casually once the site is live; add new versions instead.

### Selling access (price or invite code) — is it sensible?

**Yes, as a paid research/screener product** — not as “we tell you the stock that will win next month.” People already pay for screeners, ranking dashboards, and factor reports. A site that shows your daily/weekly ranked universe, ticker detail, and charts is a normal SaaS shape.

| Model | How it works | Good for |
|-------|----------------|----------|
| **Fixed price / subscription** | Monthly or yearly fee unlocks leaderboard + detail | Public product, recurring revenue |
| **Access / invite code** | User enters a code (beta, friends, promo, one-time purchase) | Soft launch, private beta, gifts, limited seats |
| **Freemium** | Free: delayed or top-10 only; paid: full universe, history, compare | Growth + conversion |
| **One-time report code** | Pay once → code unlocks that week’s pack | Simple early monetization |

**What makes it worth paying for** (vs free Yahoo/Finviz):

- Your **full-universe score** and transparent methodology (versioned).
- **Fresh batch** after each market day + run history (“who entered/left the top list”).
- Clean **ticker detail** (metrics + plots) without users running Python.
- Optional later: alerts, compare view, export CSV, “suggested pick” with full score breakdown.

**What to avoid claiming in marketing / UI copy:**

- Guaranteed profit, “best stock for next month,” or personalized investment advice unless you have proper licensing and legal review in your jurisdiction.
- Prefer: *research rankings*, *model candidates*, *not financial advice*, past performance ≠ future results.

**Practical product split**

1. **Engine (this repo)** — free to develop privately; produces reports.
2. **Website** — gated; users never hit your market-data API; they only see scored results you already computed.
3. **Auth** — accounts + Stripe (or similar) for price, **or** simple access-code table for early users.

That split is sensible: heavy compute stays offline/batch; the site is mostly read-only, which keeps hosting cheap and the product easy to sell.

---

## Configuration Checklist

Before a production-style run, set:

- [ ] Universe source and size N  
- [ ] Lookback and return windows  
- [ ] Liquidity / price filters  
- [ ] Data provider + API key (env var, never commit)  
- [ ] Metric parameters (RSI period, SMA lengths, risk-free rate for Sharpe)  
- [ ] Output directory / report date  
- [ ] Parallelism limits (respect API rate limits)  

---

## Operational Procedure (Day-to-Day)

1. Confirm config (`config/default.yaml` or CLI flags).  
2. Run the pipeline.  
3. Check `manifest.json` for failures.  
4. Open `leaderboard.csv` — sanity-check extremes (data errors often show as absurd returns).  
5. Spot-check 5–10 ticker dashboards.  
6. Archive the `reports/YYYY-MM-DD/` folder.  
7. (Later) Trigger site rebuild / cache invalidation from that report.

---

## Risks and Guardrails

- **Survivorship / look-ahead bias:** Universe chosen on recent winners will look strong historically; label this as momentum screening, not proof of future alpha.
- **API limits:** Cache aggressively; backoff on 429s.
- **Delisted / renamed tickers:** Soft-fail and log.
- **Not financial advice:** Outputs are research tooling; no auto-trading without separate risk, compliance, and paper-trading validation.
- **Secrets:** API keys only via environment variables.

---

## Implementation Roadmap

| Step | Deliverable | Done when |
|------|-------------|-----------|
| 1 | Config + project skeleton | `run` CLI exists and prints plan |
| 2 | Ingest + cache for one ticker | Parquet written and validated |
| 3 | Metrics for one ticker | Summary row + series file |
| 4 | Plots for one ticker | Dashboard HTML/PNG |
| 5 | Batch over top N | Leaderboard + manifest |
| 6 | Hardening | Tests, retries, logging |
| 7 | Web read API + ticker pages | Site serves latest run detail |

---

## Success Criteria (v1)

- Analyzes **≥ 100** stocks in one unattended run.
- Every successful ticker has **summary metrics + at least the core plot set**.
- A dated **leaderboard** and **manifest** exist for reproducibility.
- Layout and schemas are clear enough to plug into a **ticker detail website** without rewriting the metric engine.

---

## Next Action When You Start Building

1. Add `requirements.txt` and the `src/trade_pl/` package layout above.  
2. Implement ingest → metrics → plots for a single liquid ticker (e..g. `AAPL`).  
3. Wrap in a loop over a small universe (10 tickers), then scale to hundreds.  
4. Keep this README updated when metric definitions or folder contracts change.

---

*Last updated: 2026-10-20 — planning document only; no trading execution.*
