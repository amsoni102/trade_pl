from __future__ import annotations

"""Official SEC EDGAR filings (10-K / 10-Q / 8-K) with document links.

Uses the public data.sec.gov API. Not every non-US ticker has a CIK.
"""

import json
import logging
import re
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# SEC requires a descriptive User-Agent with a contact address (403 otherwise).
SEC_UA = "trade_pl research bot contact@example.com"
IMPORTANT_FORMS = {
    "10-K": {"label": "Annual report (10-K)", "importance": 5, "kind": "annual"},
    "10-K/A": {"label": "Annual report amendment", "importance": 4, "kind": "annual"},
    "10-Q": {"label": "Quarterly report (10-Q)", "importance": 5, "kind": "quarterly"},
    "10-Q/A": {"label": "Quarterly report amendment", "importance": 4, "kind": "quarterly"},
    "8-K": {"label": "Current report (8-K)", "importance": 4, "kind": "material"},
    "8-K/A": {"label": "Current report amendment", "importance": 3, "kind": "material"},
    "20-F": {"label": "Foreign annual (20-F)", "importance": 5, "kind": "annual"},
    "6-K": {"label": "Foreign current (6-K)", "importance": 4, "kind": "material"},
    "DEF 14A": {"label": "Proxy statement", "importance": 3, "kind": "governance"},
    "S-1": {"label": "Registration (S-1)", "importance": 4, "kind": "offering"},
    "424B2": {"label": "Prospectus", "importance": 2, "kind": "offering"},
}


def _http_get_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": SEC_UA,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def load_ticker_cik_map(cache_path: Path) -> dict[str, str]:
    """Return TICKER -> zero-padded CIK map (cached)."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = False
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        fresh = age < 7 * 24 * 3600
    if fresh:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        data = _http_get_json("https://www.sec.gov/files/company_tickers.json")
        mapping: dict[str, str] = {}
        for row in data.values() if isinstance(data, dict) else []:
            tick = str(row.get("ticker") or "").upper().replace(".", "-")
            cik = str(row.get("cik_str") or "").zfill(10)
            if tick and cik:
                mapping[tick] = cik
        cache_path.write_text(json.dumps(mapping), encoding="utf-8")
        return mapping
    except Exception as exc:  # noqa: BLE001
        logger.warning("SEC ticker map failed: %s", exc)
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}


def _doc_url(cik: str, accession: str, primary: str) -> str:
    acc = accession.replace("-", "")
    cik_n = str(int(cik))  # drop leading zeros for path
    return f"https://www.sec.gov/Archives/edgar/data/{cik_n}/{acc}/{primary}"


def _pdf_or_html_url(cik: str, accession: str, primary: str) -> dict[str, str]:
    """Prefer PDF sibling when primary is HTML; always expose HTML filing."""
    html = _doc_url(cik, accession, primary)
    pdf = None
    if primary.lower().endswith(".htm") or primary.lower().endswith(".html"):
        stem = re.sub(r"\.(html?)$", "", primary, flags=re.I)
        pdf = _doc_url(cik, accession, f"{stem}.pdf")
    elif primary.lower().endswith(".pdf"):
        pdf = html
        html = html  # already pdf
    return {"html": html, "pdf": pdf or html}


def fetch_filings_for_symbol(
    symbol: str,
    cik_map: dict[str, str],
    year: int | None = None,
    limit: int = 24,
) -> dict[str, Any]:
    symbol = symbol.upper().replace(".", "-")
    year = year or date.today().year
    cik = cik_map.get(symbol)
    if not cik:
        # try without hyphen (BRKB)
        cik = cik_map.get(symbol.replace("-", ""))
    if not cik:
        return {
            "symbol": symbol,
            "cik": None,
            "as_of": date.today().isoformat(),
            "filings": [],
            "note": "No SEC CIK mapping (common for some non-US names).",
        }

    try:
        # gentle pacing for SEC
        time.sleep(0.12)
        data = _http_get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    except Exception as exc:  # noqa: BLE001
        logger.warning("SEC submissions %s failed: %s", symbol, exc)
        return {
            "symbol": symbol,
            "cik": cik,
            "as_of": date.today().isoformat(),
            "filings": [],
            "note": f"SEC fetch failed: {exc}",
        }

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    primaries = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []

    filings: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        meta = IMPORTANT_FORMS.get(form)
        if not meta:
            continue
        fdate = dates[i] if i < len(dates) else None
        if not fdate:
            continue
        try:
            y = int(str(fdate)[:4])
        except Exception:
            y = 0
        # current year + prior year (so Q4/annual always available early in year)
        if y < year - 1:
            continue
        accession = accessions[i] if i < len(accessions) else ""
        primary = primaries[i] if i < len(primaries) else ""
        if not accession or not primary:
            continue
        links = _pdf_or_html_url(cik, accession, primary)
        filings.append(
            {
                "form": form,
                "label": meta["label"],
                "kind": meta["kind"],
                "importance": meta["importance"],
                "filing_date": fdate,
                "description": descriptions[i] if i < len(descriptions) else "",
                "accession": accession,
                "document": primary,
                "url_html": links["html"],
                "url_pdf": links["pdf"],
                "sec_company": f"https://www.sec.gov/edgar/browse/?CIK={cik}",
            }
        )
        if len(filings) >= limit:
            break

    filings.sort(key=lambda x: (x.get("filing_date") or "", x.get("importance", 0)), reverse=True)
    return {
        "symbol": symbol,
        "cik": cik,
        "company_name": data.get("name"),
        "as_of": date.today().isoformat(),
        "filings": filings,
        "annual": [f for f in filings if f["kind"] == "annual"],
        "quarterly": [f for f in filings if f["kind"] == "quarterly"],
        "material": [f for f in filings if f["kind"] == "material"],
        "note": None if filings else "No recent 10-K/10-Q/8-K found for this year window.",
    }


def save_filings(out_dir: Path, payload: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{payload['symbol']}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_filings(out_dir: Path, symbol: str) -> dict[str, Any] | None:
    path = out_dir / f"{symbol.upper().replace('.', '-')}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_filings_many(
    symbols: list[str],
    out_dir: Path,
    cik_cache: Path,
    workers: int = 4,
) -> list[str]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cik_map = load_ticker_cik_map(cik_cache)
    done: list[str] = []

    def _one(sym: str) -> str | None:
        try:
            payload = fetch_filings_for_symbol(sym, cik_map)
            save_filings(out_dir, payload)
            return sym
        except Exception as exc:  # noqa: BLE001
            logger.warning("filings %s: %s", sym, exc)
            return None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(_one, s) for s in symbols]
        for fut in as_completed(futs):
            sym = fut.result()
            if sym:
                done.append(sym)
    return done
