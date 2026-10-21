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
# First time: download + score + plot top names (~few minutes for ~40 tickers)
python -m trade_pl.run --universe-size 40

# Later: score from cache only
python -m trade_pl.run --from-cache --universe-size 40
```

Outputs:

- `reports/latest/leaderboard.csv`
- `reports/latest/suggested_pick.json`
- `plots/{TICKER}/dashboard.html`

## 3. Start the local website

```bash
python -m trade_pl.web.app
# or: trade-pl-web
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Notes

- Data comes from Yahoo Finance via `yfinance` (fine for local research; rate-limited for huge universes).
- Website only **reads** report artifacts; it does not re-download the market on each page view.
- Rankings are research candidates, not financial advice.
