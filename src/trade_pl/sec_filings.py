from __future__ import annotations

"""Official SEC EDGAR filings (10-K / 10-Q / 8-K) with document links.

Uses the public data.sec.gov API. Not every non-US ticker has a CIK.
Primary documents are the files SEC lists as ``primaryDocument``; we open those
via the iXBRL viewer (or a real PDF when present) and list package exhibits.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# SEC requires a descriptive User-Agent with a contact address (403 otherwise).
SEC_UA = "trade_pl research bot contact@example.com"
_SKIP_DOC_NAMES = {
    "index.json",
    "filingsummary.xml",
    "metalinks.json",
    "reportingdisplay.xslt",
}
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


def _index_url(cik: str, accession: str) -> str:
    """EDGAR filing index — lists every exhibit (real PDFs live here when they exist)."""
    acc = accession.replace("-", "")
    cik_n = str(int(cik))
    # Keep dashed accession in the index filename (SEC convention).
    return f"https://www.sec.gov/Archives/edgar/data/{cik_n}/{acc}/{accession}-index.htm"


def _viewer_url(cik: str, accession: str) -> str:
    return (
        "https://www.sec.gov/cgi-bin/viewer?action=view"
        f"&cik={int(cik)}&accession_number={accession}&xbrl_type=v"
    )


def _ix_url(cik: str, accession: str, primary: str) -> str:
    """Inline XBRL viewer — readable rendering of modern .htm filings (raw .htm is XML-ish)."""
    acc = accession.replace("-", "")
    cik_n = str(int(cik))
    path = f"/Archives/edgar/data/{cik_n}/{acc}/{primary}"
    return f"https://www.sec.gov/ix?doc={path}"


def safe_doc_name(name: str) -> str | None:
    """Reject path traversal / junk; allow only a bare archive filename."""
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    if not re.match(r"^[\w.\-()+ ]+$", name):
        return None
    return name


# Back-compat alias used internally.
_safe_doc_name = safe_doc_name


def _list_package_files(cik: str, accession: str) -> list[dict[str, Any]]:
    """Files in the filing package via EDGAR index.json."""
    acc = accession.replace("-", "")
    cik_n = str(int(cik))
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_n}/{acc}/index.json"
    try:
        time.sleep(0.08)
        data = _http_get_json(url)
        items = ((data or {}).get("directory") or {}).get("item") or []
        return items if isinstance(items, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.debug("index.json failed %s %s: %s", cik, accession, exc)
        return []


def _is_presentable_doc(name: str) -> bool:
    n = name.lower()
    if n in _SKIP_DOC_NAMES:
        return False
    if re.match(r"^r\d+", n):  # XBRL report fragments
        return False
    if "-index" in n or n.endswith("-index.htm") or n.endswith("-index.html"):
        return False
    if n.endswith(("-index-headers.html", "-index-headers.htm")):
        return False
    if n.endswith((".xsd", ".xml", ".json", ".css", ".js", ".gif", ".jpg", ".jpeg", ".png", ".svg")):
        return False
    # Prefer human docs + exhibits; skip raw submission .txt / xbrl zip clutter.
    if n.endswith((".txt", ".zip")):
        return False
    return n.endswith((".htm", ".html", ".xhtml", ".pdf", ".xlsx", ".xls", ".csv"))


def doc_kind(name: str) -> str:
    n = name.lower()
    if n.endswith(".pdf"):
        return "pdf"
    if n.endswith((".htm", ".html", ".xhtml")):
        return "html"
    if n.endswith((".xlsx", ".xls", ".csv")):
        return "sheet"
    return "other"


_doc_kind = doc_kind


def open_url_for_doc(cik: str, accession: str, name: str, *, prefer_ix: bool = True) -> str:
    """Human-openable URL for a package file (ix viewer for HTML, direct otherwise)."""
    kind = doc_kind(name)
    if kind == "html" and prefer_ix:
        return _ix_url(cik, accession, name)
    return _doc_url(cik, accession, name)


_open_url_for_doc = open_url_for_doc


def _package_documents(
    cik: str,
    accession: str,
    primary: str,
    items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Curated list of openable documents from the filing package.

    Always includes the SEC-presented primary document first.
    """
    primary = (primary or "").strip()
    if items is None:
        items = _list_package_files(cik, accession)

    by_name: dict[str, dict[str, Any]] = {}
    for it in items:
        name = _safe_doc_name(str(it.get("name") or ""))
        if not name or not _is_presentable_doc(name):
            continue
        try:
            size = int(str(it.get("size") or "0") or 0)
        except Exception:
            size = 0
        by_name[name] = {
            "name": name,
            "size": size,
            "kind": _doc_kind(name),
            "primary": bool(primary) and name.lower() == primary.lower(),
            "url_sec": _doc_url(cik, accession, name),
            "url_open": _open_url_for_doc(cik, accession, name),
            "description": str(it.get("description") or it.get("type") or ""),
        }

    # Ensure the presented primary is listed even if index.json omitted it.
    if primary and _safe_doc_name(primary):
        if primary not in by_name:
            by_name[primary] = {
                "name": primary,
                "size": 0,
                "kind": _doc_kind(primary),
                "primary": True,
                "url_sec": _doc_url(cik, accession, primary),
                "url_open": _open_url_for_doc(cik, accession, primary),
                "description": "Primary document",
            }
        else:
            by_name[primary]["primary"] = True

    docs = list(by_name.values())
    docs.sort(
        key=lambda d: (
            0 if d.get("primary") else 1,
            0 if d.get("kind") == "pdf" else 1,
            -(d.get("size") or 0),
            str(d.get("name") or ""),
        )
    )
    return docs


def _pick_real_pdf(items: list[dict[str, Any]], primary: str) -> str | None:
    """Return a real PDF filename from the package, if any (never invent one)."""
    pdfs: list[tuple[int, str]] = []
    for it in items:
        name = str(it.get("name") or "")
        if not name.lower().endswith(".pdf"):
            continue
        try:
            size = int(str(it.get("size") or "0") or 0)
        except Exception:
            size = 0
        pdfs.append((size, name))
    if not pdfs:
        return None
    stem = primary.rsplit(".", 1)[0].lower() if primary else ""
    # Prefer a PDF whose name matches the primary document stem.
    for size, name in sorted(pdfs, key=lambda x: -x[0]):
        if stem and stem in name.lower():
            return name
    # Otherwise largest PDF (likely the full filing / press kit).
    pdfs.sort(key=lambda x: -x[0])
    return pdfs[0][1]


def _filing_urls(
    cik: str,
    accession: str,
    primary: str,
    *,
    resolve_package: bool = False,
) -> dict[str, Any]:
    """Build working EDGAR links + optional package document list.

    Modern 10-K/10-Q primaries are iXBRL ``.htm`` files. Opening them directly
    often shows raw XML / empty Adobe tabs. Use the SEC **ix** viewer instead.
    Only expose ``url_pdf`` when a real ``.pdf`` exists in the filing package.
    """
    primary = (primary or "").strip()
    doc = _doc_url(cik, accession, primary) if primary else None
    index = _index_url(cik, accession)
    viewer = _viewer_url(cik, accession)
    is_pdf = primary.lower().endswith(".pdf")
    is_html = primary.lower().endswith((".htm", ".html", ".xhtml"))

    items: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    pdf_name: str | None = primary if is_pdf else None
    if resolve_package:
        items = _list_package_files(cik, accession)
        documents = _package_documents(cik, accession, primary, items)
        if not pdf_name:
            pdf_name = _pick_real_pdf(items, primary)

    pdf_url = _doc_url(cik, accession, pdf_name) if pdf_name else None
    ix = _ix_url(cik, accession, primary) if (primary and is_html) else None

    # Prefer the presented primary (ix / pdf), not a side exhibit PDF.
    if is_pdf and pdf_url:
        open_url = pdf_url
    elif ix:
        open_url = ix
    elif pdf_url:
        open_url = pdf_url
    else:
        open_url = viewer if accession else (doc or index)

    return {
        "html": ix or doc or index,
        "raw": doc,
        "pdf": pdf_url,
        "index": index,
        "viewer": viewer,
        "open": open_url,
        "documents": documents,
    }


def fetch_sec_bytes(cik: str, accession: str, name: str, timeout: int = 60) -> tuple[bytes, str]:
    """Download a single archive file (for in-app reader / proxy)."""
    safe = _safe_doc_name(name)
    if not safe:
        raise ValueError("invalid document name")
    url = _doc_url(cik, accession, safe)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": SEC_UA,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ctype = (resp.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
            return data, ctype
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"SEC fetch HTTP {exc.code}") from exc


def prepare_html_for_embed(html: bytes, cik: str, accession: str) -> bytes:
    """Inject <base href> so relative SEC assets resolve when we proxy HTML."""
    acc = accession.replace("-", "")
    cik_n = str(int(cik))
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_n}/{acc}/"
    text = html.decode("utf-8", errors="replace")
    base_tag = f'<base href="{base}">'
    low = text.lower()
    if "<base " in low:
        return text.encode("utf-8")
    if "<head" in low:
        # Insert after <head ...>
        m = re.search(r"<head[^>]*>", text, flags=re.IGNORECASE)
        if m:
            i = m.end()
            text = text[:i] + base_tag + text[i:]
            return text.encode("utf-8")
    text = base_tag + text
    return text.encode("utf-8")


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
        # Resolve package docs / real PDFs for the first handful (extra SEC round-trips).
        links = _filing_urls(
            cik,
            accession,
            primary,
            resolve_package=len(filings) < 12,
        )
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
                "url_raw": links["raw"],
                "url_pdf": links["pdf"],
                "url_index": links["index"],
                "url_viewer": links["viewer"],
                "url_open": links["open"],
                "documents": links.get("documents") or [],
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


def _repair_filing_links(payload: dict[str, Any], *, resolve_package: bool = False) -> dict[str, Any]:
    """Rewrite cached bad links to ix-viewer / real PDF / package docs."""
    cik = payload.get("cik")
    if not cik:
        return payload
    changed = False
    for bucket in ("filings", "annual", "quarterly", "material"):
        rows = payload.get(bucket) or []
        if not isinstance(rows, list):
            continue
        for i, f in enumerate(rows):
            accession = f.get("accession") or ""
            primary = f.get("document") or ""
            if not accession:
                continue
            need_docs = resolve_package or not (f.get("documents") or [])
            links = _filing_urls(
                str(cik),
                accession,
                primary,
                resolve_package=need_docs and i < 8,
            )
            for key in ("url_html", "url_raw", "url_pdf", "url_index", "url_viewer", "url_open"):
                if f.get(key) != links.get(key):
                    f[key] = links.get(key)
                    changed = True
            docs = links.get("documents") or []
            if docs and f.get("documents") != docs:
                f["documents"] = docs
                changed = True
            # Drop invented .pdf that just swap .htm → .pdf
            pdf = str(f.get("url_pdf") or "")
            raw = str(f.get("url_raw") or f.get("document") or "")
            if pdf.endswith(".pdf") and (raw.endswith(".htm") or raw.endswith(".html")):
                if not links.get("pdf"):
                    stem_pdf = pdf.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    stem_raw = raw.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    if stem_pdf == stem_raw:
                        f["url_pdf"] = None
                        f["url_open"] = links.get("open")
                        changed = True
    if changed:
        payload["_links_repaired"] = True
    return payload


def find_filing(payload: dict[str, Any], accession: str) -> dict[str, Any] | None:
    acc = (accession or "").strip()
    if not acc:
        return None
    for bucket in ("filings", "annual", "quarterly", "material"):
        for f in payload.get(bucket) or []:
            if str(f.get("accession") or "") == acc:
                return f
    return None


def ensure_filing_documents(payload: dict[str, Any], accession: str) -> dict[str, Any] | None:
    """Ensure package document list is populated for one accession (mutates payload)."""
    f = find_filing(payload, accession)
    if not f:
        return None
    cik = payload.get("cik")
    primary = f.get("document") or ""
    if not cik or not primary:
        return f
    if f.get("documents"):
        return f
    links = _filing_urls(str(cik), accession, primary, resolve_package=True)
    f["documents"] = links.get("documents") or []
    for key in ("url_html", "url_raw", "url_pdf", "url_index", "url_viewer", "url_open"):
        f[key] = links.get(key)
    return f


def load_filings(out_dir: Path, symbol: str) -> dict[str, Any] | None:
    path = out_dir / f"{symbol.upper().replace('.', '-')}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    repaired = _repair_filing_links(payload)
    if repaired.get("_links_repaired"):
        try:
            path.write_text(json.dumps(repaired, indent=2), encoding="utf-8")
        except Exception:
            pass
    return repaired


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
