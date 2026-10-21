# Getting started

## 1. Install

```bash
cd /home/z004wr1v/Desktop/t123/trade_pl
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2. Run the pipeline

```bash
# Full board (default): ~1000 names, enrich top 300 with news/financials
python -m trade_pl.run

# Quicker test still with hundreds of names
python -m trade_pl.run --universe-size 300 --enrich-top-k 150

# Later: score from cache only (fast)
python -m trade_pl.run --from-cache
```

Outputs:

- `reports/latest/leaderboard.csv` — composite ranking with sectors + 1W/1M/3M/6M/1Y
- `reports/latest/leaders_by_horizon.json` — best week / month / half-year / year
- `reports/latest/leaders_by_sector.json` — Tech, Energy, etc.
- `reports/latest/suggested_pick.json`
- `data/enrichment/{TICKER}.json` — news, earnings dates, financial statements
- `plots/{TICKER}/dashboard.html`

## 3. Start the local website

From the repo root, with the virtualenv already installed (`pip install -e .`):

```bash
make start    # pipeline (--universe-size 80) then site at http://127.0.0.1:8000
make status
make stop
```

`make start` runs `python -m trade_pl.run --universe-size 80`, then starts the site in the background. Override with `make start UNIVERSE_SIZE=40`. Uses `.venv/bin/python` when present. Logs: `.run/pipeline.log`, `.run/web.log`.

The same server alone is `python -m trade_pl.web.app` or `trade-pl-web`.

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

| Page | URL |
|------|-----|
| Leaderboard + horizon cards | `/` |
| Search by name or ticker | `/search?q=Siemens+Energy` |
| Week / month / year leaders | `/horizons` |
| Sector boards | `/sectors` |
| Ticker detail (news, financials, dates, charts) | `/stock/AAPL` or `/stock/ENR.DE` |
| Compare | `/compare?symbols=AAPL,MSFT,XOM` |

### Stock search (smart storage)

We do **not** store the whole market. Search works like this:

1. Match against the latest leaderboard + any profiles already in `data/enrichment/`.
2. If needed, ask Yahoo Finance for name→ticker hits (e.g. “Siemens Energy” → `ENR.DE`) and cache that query briefly in `data/search/`.
3. Opening a ticker **lazy-loads** financials/news into `data/enrichment/{SYMBOL}.json` once, then reuses the file.

## Notes

- Curated universe is **~1000** liquid names across 11 sectors.
- News / financials / calendar are fetched for **top names only** (configurable `enrich_top_k`) to stay within Yahoo rate limits.
- Website only **reads** report artifacts; it does not re-download the market on each page view.
- Rankings are research candidates, not financial advice.
