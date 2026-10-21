from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from trade_pl.sectors import all_tickers_with_sector, sector_of, tickers_for_limit


def default_universe(limit: int | None = None) -> list[str]:
    return tickers_for_limit(limit)


def sector_map_for(tickers: list[str] | None = None) -> dict[str, str]:
    full = all_tickers_with_sector()
    if tickers is None:
        return full
    return {t: full.get(t, sector_of(t)) for t in tickers}


def load_universe_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").strip()
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict) and "tickers" in data:
            return [str(t).upper().replace(".", "-") for t in data["tickers"]]
        if isinstance(data, list):
            return [str(t).upper().replace(".", "-") for t in data]
        raise ValueError(f"Unsupported universe JSON in {path}")
    if path.suffix.lower() == ".csv":
        import pandas as pd

        df = pd.read_csv(path)
        col = "symbol" if "symbol" in df.columns else df.columns[0]
        return [str(t).upper().replace(".", "-") for t in df[col].tolist()]
    return [
        line.strip().upper().replace(".", "-")
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


def save_universe(tickers: list[str], out_dir: Path, criteria: dict[str, Any] | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date.today().isoformat()}.json"
    smap = sector_map_for(tickers)
    payload = {
        "as_of": date.today().isoformat(),
        "count": len(tickers),
        "tickers": tickers,
        "sectors": smap,
        "criteria": criteria or {},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
