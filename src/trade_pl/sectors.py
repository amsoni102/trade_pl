"""Sector / market helpers over the curated multi-market catalog."""

from __future__ import annotations

from trade_pl.search import normalize_symbol
from trade_pl.universe_catalog import CATALOG

# Back-compat alias: sector -> tickers (deduped, catalog order).
SECTOR_TICKERS: dict[str, list[str]] = {}
for _sym, _sector, _market in CATALOG:
    SECTOR_TICKERS.setdefault(_sector, []).append(normalize_symbol(_sym))


def all_tickers_with_sector() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for sym, sector, _market in CATALOG:
        mapping.setdefault(normalize_symbol(sym), sector)
    return mapping


def all_tickers_with_market() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for sym, _sector, market in CATALOG:
        mapping.setdefault(normalize_symbol(sym), market)
    return mapping


def catalog_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for sym, sector, market in CATALOG:
        key = normalize_symbol(sym)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"symbol": key, "sector": sector, "market": market})
    return rows


def tickers_for_limit(limit: int | None = None) -> list[str]:
    """Round-robin across sectors so small universes stay diversified."""
    mapping = all_tickers_with_sector()
    if limit is None or limit >= len(mapping):
        return list(mapping.keys())

    by_sector: dict[str, list[str]] = {}
    for sym, sector in mapping.items():
        by_sector.setdefault(sector, []).append(sym)

    ordered: list[str] = []
    queues = {s: list(syms) for s, syms in by_sector.items()}
    while len(ordered) < limit and queues:
        empty: list[str] = []
        for sector in list(queues.keys()):
            if not queues[sector]:
                empty.append(sector)
                continue
            ordered.append(queues[sector].pop(0))
            if len(ordered) >= limit:
                break
        for sector in empty:
            queues.pop(sector, None)
    return ordered


def sector_of(symbol: str, fallback: str = "Unknown") -> str:
    return all_tickers_with_sector().get(normalize_symbol(symbol), fallback)


def market_of(symbol: str, fallback: str = "Unknown") -> str:
    return all_tickers_with_market().get(normalize_symbol(symbol), fallback)
