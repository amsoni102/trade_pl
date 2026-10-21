from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

# Liquid US large/mid universe for v1 (no network required to list names).
# Expand via config or external CSV later.
DEFAULT_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "AVGO", "JPM",
    "TSLA", "UNH", "XOM", "V", "MA", "COST", "HD", "PG", "JNJ", "ABBV",
    "CRM", "BAC", "NFLX", "KO", "PEP", "MRK", "CVX", "WMT", "AMD", "ORCL",
    "ADBE", "CSCO", "ACN", "TMO", "MCD", "ABT", "LIN", "DHR", "INTC", "TXN",
    "PM", "IBM", "QCOM", "CAT", "GE", "AMAT", "ISRG", "NOW", "BX", "GS",
    "SPGI", "BKNG", "AXP", "MS", "UBER", "PFE", "DIS", "NEE", "T", "CMCSA",
    "LOW", "UNP", "HON", "AMGN", "RTX", "SYK", "PGR", "BLK", "TJX", "VRTX",
    "PLD", "COP", "ADP", "C", "BMY", "DE", "MDT", "SBUX", "LMT", "ADI",
    "GILD", "REGN", "PANW", "MU", "FI", "ETN", "SCHW", "CB", "MMC", "SO",
    "BSX", "CI", "DUK", "EQIX", "SHW", "KLAC", "CDNS", "SNPS", "ANET", "APH",
    "CME", "ICE", "WM", "ITW", "MO", "ZTS", "PH", "BDX", "SLB", "CSX",
    "MCK", "TDG", "EMR", "ORLY", "HCA", "CTAS", "MCO", "NOC", "PYPL", "MAR",
    "AON", "USB", "PNC", "GM", "F", "NKE", "BA", "CVS", "WFC", "TGT",
]


def default_universe(limit: int | None = None) -> list[str]:
    tickers = list(DEFAULT_TICKERS)
    if limit is not None:
        tickers = tickers[: max(1, limit)]
    return tickers


def load_universe_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").strip()
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict) and "tickers" in data:
            return [str(t).upper() for t in data["tickers"]]
        if isinstance(data, list):
            return [str(t).upper() for t in data]
        raise ValueError(f"Unsupported universe JSON in {path}")
    return [line.strip().upper() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def save_universe(tickers: list[str], out_dir: Path, criteria: dict[str, Any] | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date.today().isoformat()}.json"
    payload = {
        "as_of": date.today().isoformat(),
        "count": len(tickers),
        "tickers": tickers,
        "criteria": criteria or {},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
