"""On-demand stock search by ticker or company name.

Storage model (intentionally lean):
- Do not download the whole market.
- Local hits: latest leaderboard + already-cached enrichment profiles.
- Remote hits: Yahoo Finance search API (name → symbol).
- When a ticker is opened, enrichment JSON is cached under data/enrichment/.
- Yahoo query responses are cached briefly under data/search/ so repeats are free.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Yahoo keeps exchange suffixes with a dot (ENR.DE) but US share classes with a hyphen (BRK-B).
_EXCHANGE_SUFFIXES = frozenset(
    {
        "DE",
        "F",
        "DU",
        "BE",
        "HM",
        "MU",
        "SG",
        "L",
        "PA",
        "AS",
        "BR",
        "MI",
        "MC",
        "SW",
        "ST",
        "OL",
        "CO",
        "HE",
        "VI",
        "TO",
        "V",
        "AX",
        "NZ",
        "HK",
        "T",
        "NS",
        "BO",
        "KS",
        "KQ",
        "TW",
        "SA",
        "MX",
        "SI",
        "JK",
        "KL",
    }
)

_QUOTE_TYPES = frozenset({"EQUITY", "ETF", "MUTUALFUND", "INDEX"})


def normalize_symbol(symbol: str) -> str:
    """Normalize a ticker for Yahoo / on-disk cache keys."""
    s = (symbol or "").strip().upper().replace(" ", "")
    if not s:
        return s
    if "." in s:
        base, suf = s.rsplit(".", 1)
        if suf in _EXCHANGE_SUFFIXES:
            return f"{base}.{suf}"
        # US share class style: BRK.B → BRK-B
        return f"{base}-{suf}"
    return s


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _score_match(query: str, symbol: str, name: str) -> float:
    q = (query or "").strip().lower()
    if not q:
        return 0.0
    sym = (symbol or "").upper()
    nm = (name or "").lower()
    qu = q.upper()
    if sym == qu or sym.replace("-", ".").replace(".", "") == qu.replace("-", "").replace(".", ""):
        return 100.0
    if sym.startswith(qu):
        return 90.0
    if qu in sym:
        return 80.0
    if nm == q:
        return 95.0
    if nm.startswith(q):
        return 85.0
    if q in nm:
        return 70.0
    q_toks = _tokens(q)
    n_toks = set(_tokens(nm))
    if q_toks and all(t in n_toks or any(nt.startswith(t) for nt in n_toks) for t in q_toks):
        return 65.0 + 2.0 * len(q_toks)
    return 0.0


def _cache_path(cache_dir: Path, query: str) -> Path:
    key = hashlib.sha1(query.strip().lower().encode("utf-8")).hexdigest()[:20]
    return cache_dir / f"{key}.json"


def _load_query_cache(cache_dir: Path, query: str, ttl_seconds: int) -> list[dict[str, Any]] | None:
    path = _cache_path(cache_dir, query)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - float(payload.get("ts", 0)) > ttl_seconds:
        return None
    hits = payload.get("hits")
    return hits if isinstance(hits, list) else None


def _save_query_cache(cache_dir: Path, query: str, hits: list[dict[str, Any]]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, query)
    path.write_text(
        json.dumps({"q": query, "ts": time.time(), "hits": hits}, indent=2),
        encoding="utf-8",
    )


def search_yahoo(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Resolve a free-text company/ticker query via Yahoo Finance search."""
    q = (query or "").strip()
    if len(q) < 1:
        return []
    url = (
        "https://query1.finance.yahoo.com/v1/finance/search?"
        + urllib.parse.urlencode({"q": q, "quotesCount": limit, "newsCount": 0})
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 trade_pl/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        logger.warning("yahoo search failed for %r: %s", q, exc)
        return []

    hits: list[dict[str, Any]] = []
    for item in data.get("quotes") or []:
        qtype = str(item.get("quoteType") or "").upper()
        if qtype and qtype not in _QUOTE_TYPES:
            continue
        symbol = normalize_symbol(str(item.get("symbol") or ""))
        if not symbol:
            continue
        name = item.get("longname") or item.get("shortname") or item.get("longName") or item.get("shortName")
        hits.append(
            {
                "symbol": symbol,
                "name": (str(name).strip() if name else None),
                "exchange": item.get("exchDisp") or item.get("exchange"),
                "type": item.get("typeDisp") or item.get("quoteType"),
                "source": "yahoo",
                "score": _score_match(q, symbol, str(name or "")),
            }
        )
    return hits


def search_local(
    query: str,
    *,
    leaderboard_rows: list[dict[str, Any]] | None = None,
    enrichment_dir: Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Match against the latest leaderboard and cached enrichment profiles."""
    q = (query or "").strip()
    if len(q) < 1:
        return []

    by_symbol: dict[str, dict[str, Any]] = {}

    for row in leaderboard_rows or []:
        symbol = normalize_symbol(str(row.get("symbol") or ""))
        if not symbol:
            continue
        name = str(row.get("name") or row.get("company") or "")
        score = _score_match(q, symbol, name)
        if score <= 0:
            continue
        by_symbol[symbol] = {
            "symbol": symbol,
            "name": name or None,
            "sector": row.get("sector"),
            "exchange": "leaderboard",
            "type": "Equity",
            "source": "local",
            "score": score,
            "rank": row.get("rank"),
            "in_leaderboard": True,
        }

    if enrichment_dir and enrichment_dir.exists():
        for path in enrichment_dir.glob("*.json"):
            try:
                profile = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            symbol = normalize_symbol(str(profile.get("symbol") or path.stem))
            name = str(profile.get("name") or "")
            score = _score_match(q, symbol, name)
            if score <= 0:
                continue
            existing = by_symbol.get(symbol)
            if existing and existing.get("score", 0) >= score:
                if not existing.get("name") and name:
                    existing["name"] = name
                continue
            by_symbol[symbol] = {
                "symbol": symbol,
                "name": name or (existing or {}).get("name"),
                "sector": profile.get("sector") or (existing or {}).get("sector"),
                "exchange": (existing or {}).get("exchange") or profile.get("country"),
                "type": "Equity",
                "source": "cache",
                "score": score,
                "rank": (existing or {}).get("rank"),
                "in_leaderboard": bool((existing or {}).get("in_leaderboard")),
                "has_profile": True,
            }

    hits = sorted(by_symbol.values(), key=lambda h: (-float(h.get("score") or 0), h["symbol"]))
    return hits[:limit]


def search_stocks(
    query: str,
    *,
    leaderboard_rows: list[dict[str, Any]] | None = None,
    enrichment_dir: Path | None = None,
    cache_dir: Path | None = None,
    limit: int = 12,
    yahoo: bool = True,
    cache_ttl_seconds: int = 6 * 3600,
) -> list[dict[str, Any]]:
    """Merge local + Yahoo hits; prefer local rank metadata when symbols collide."""
    q = (query or "").strip()
    if len(q) < 1:
        return []

    local = search_local(
        q,
        leaderboard_rows=leaderboard_rows,
        enrichment_dir=enrichment_dir,
        limit=limit,
    )
    remote: list[dict[str, Any]] = []
    if yahoo:
        if cache_dir is not None:
            cached = _load_query_cache(cache_dir, q, cache_ttl_seconds)
            if cached is not None:
                remote = cached
            else:
                remote = search_yahoo(q, limit=limit)
                _save_query_cache(cache_dir, q, remote)
        else:
            remote = search_yahoo(q, limit=limit)

    merged: dict[str, dict[str, Any]] = {}
    for hit in local + remote:
        symbol = normalize_symbol(str(hit.get("symbol") or ""))
        if not symbol:
            continue
        hit = {**hit, "symbol": symbol}
        prev = merged.get(symbol)
        if prev is None:
            merged[symbol] = hit
            continue
        # Keep best score; preserve leaderboard/cache flags and nicer name.
        if float(hit.get("score") or 0) > float(prev.get("score") or 0):
            for key in ("in_leaderboard", "has_profile", "rank", "sector"):
                if prev.get(key) and not hit.get(key):
                    hit[key] = prev[key]
            if not hit.get("name") and prev.get("name"):
                hit["name"] = prev["name"]
            merged[symbol] = hit
        else:
            if hit.get("name") and not prev.get("name"):
                prev["name"] = hit["name"]
            if hit.get("exchange") and prev.get("exchange") in {None, "leaderboard"}:
                prev["exchange"] = hit["exchange"]
            if hit.get("source") == "yahoo":
                prev["source"] = "local+yahoo" if prev.get("source") == "local" else prev.get("source")

    hits = sorted(merged.values(), key=lambda h: (-float(h.get("score") or 0), h["symbol"]))
    return hits[:limit]
