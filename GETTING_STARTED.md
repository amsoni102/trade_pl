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

```bash
python -m trade_pl.web.app
# or: trade-pl-web
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

| Page | URL |
|------|-----|
| Leaderboard + horizon cards | `/` |
| Week / month / year leaders | `/horizons` |
| Sector boards | `/sectors` |
| Ticker detail (news, financials, dates, charts) | `/stock/AAPL` |
| Compare | `/compare?symbols=AAPL,MSFT,XOM` |

## Notes

- Curated universe is **~1000** liquid names across 11 sectors.
- News / financials / calendar are fetched for **top names only** (configurable `enrich_top_k`) to stay within Yahoo rate limits.
- Website only **reads** report artifacts; it does not re-download the market on each page view.
- Rankings are research candidates, not financial advice.
