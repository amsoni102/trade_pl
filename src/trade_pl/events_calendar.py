from __future__ import annotations

"""Aggregate upcoming / recent market events with importance scores for fast triage."""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Higher = more urgent for a trader/research desk
IMPORTANCE = {
    "earnings": 95,
    "earnings_soon": 90,
    "annual_filing": 80,
    "quarterly_filing": 75,
    "material_8k": 85,
    "ex_dividend": 55,
    "guidance": 70,
    "other": 40,
}


def _parse_day(value: Any) -> date | None:
    if value is None:
        return None
    s = str(value)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _importance_for_earnings(when: date, today: date) -> tuple[int, str]:
    delta = (when - today).days
    if delta < -2:
        return 35, "past"
    if delta < 0:
        return 88, "just reported"
    if delta == 0:
        return 100, "TODAY"
    if delta == 1:
        return 98, "tomorrow"
    if delta <= 7:
        return 92, "this week"
    if delta <= 30:
        return 78, "this month"
    if delta <= 90:
        return 60, "upcoming"
    return 45, "scheduled"


def build_events_from_profile(symbol: str, profile: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    events: list[dict[str, Any]] = []
    name = profile.get("name") or symbol

    ed = _parse_day(profile.get("earnings_date"))
    if ed:
        score, timing = _importance_for_earnings(ed, today)
        events.append(
            {
                "symbol": symbol,
                "company": name,
                "type": "earnings",
                "title": f"{symbol} earnings",
                "date": ed.isoformat(),
                "timing": timing,
                "importance": score,
                "source": "yahoo_calendar",
                "url": f"/stock/{symbol}",
            }
        )

    for row in profile.get("earnings_dates") or []:
        # yfinance rows vary; try common keys
        raw = row.get("Earnings Date") or row.get("earningsDate") or row.get("Date") or next(iter(row.values()), None)
        d = _parse_day(raw)
        if not d or (ed and d == ed):
            continue
        if abs((d - today).days) > 180:
            continue
        score, timing = _importance_for_earnings(d, today)
        events.append(
            {
                "symbol": symbol,
                "company": name,
                "type": "earnings",
                "title": f"{symbol} earnings (history/estimate)",
                "date": d.isoformat(),
                "timing": timing,
                "importance": min(score, 70),
                "source": "yahoo_earnings_dates",
                "url": f"/stock/{symbol}",
            }
        )

    xd = _parse_day(profile.get("ex_dividend_date"))
    if xd and -7 <= (xd - today).days <= 60:
        events.append(
            {
                "symbol": symbol,
                "company": name,
                "type": "ex_dividend",
                "title": f"{symbol} ex-dividend",
                "date": xd.isoformat(),
                "timing": "upcoming" if xd >= today else "recent",
                "importance": IMPORTANCE["ex_dividend"] + (10 if 0 <= (xd - today).days <= 3 else 0),
                "source": "yahoo_calendar",
                "url": f"/stock/{symbol}",
            }
        )
    return events


def build_events_from_filings(symbol: str, filings_payload: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    events: list[dict[str, Any]] = []
    company = filings_payload.get("company_name") or symbol
    for f in filings_payload.get("filings") or []:
        d = _parse_day(f.get("filing_date"))
        if not d:
            continue
        # surface recent filings + anything in last 120 days
        if (today - d).days > 120:
            continue
        kind = f.get("kind") or "other"
        base = {
            "annual": IMPORTANCE["annual_filing"],
            "quarterly": IMPORTANCE["quarterly_filing"],
            "material": IMPORTANCE["material_8k"],
        }.get(kind, IMPORTANCE["other"])
        freshness = max(0, 15 - (today - d).days)  # newer = higher
        events.append(
            {
                "symbol": symbol,
                "company": company,
                "type": f"filing_{kind}",
                "title": f"{symbol} {f.get('label') or f.get('form')}",
                "date": d.isoformat(),
                "timing": "filed",
                "importance": min(99, base + freshness),
                "source": "sec_edgar",
                "form": f.get("form"),
                "accession": f.get("accession"),
                "document": f.get("document"),
                # In-app reader includes the presented primary + package docs.
                "url": (
                    f"/stock/{symbol}/filing?accession={f.get('accession')}"
                    if f.get("accession")
                    else (f.get("url_open") or f.get("url_html") or f.get("url_index") or f.get("url_pdf"))
                ),
                "url_open": f.get("url_open"),
                "url_html": f.get("url_html") or f.get("url_index"),
                "url_index": f.get("url_index"),
                "url_pdf": f.get("url_pdf"),
            }
        )
    return events


def build_calendar(
    enrich_dir: Path,
    filings_dir: Path,
    symbols: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    today = date.today()
    events: list[dict[str, Any]] = []

    paths = list(enrich_dir.glob("*.json")) if enrich_dir.exists() else []
    if symbols:
        want = {s.upper().replace(".", "-") for s in symbols}
        paths = [p for p in paths if p.stem in want]

    for path in paths:
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sym = profile.get("symbol") or path.stem
        events.extend(build_events_from_profile(sym, profile, today))
        fpath = filings_dir / f"{sym}.json"
        if fpath.exists():
            try:
                filings = json.loads(fpath.read_text(encoding="utf-8"))
                events.extend(build_events_from_filings(sym, filings, today))
            except Exception:
                pass

    # de-dupe by symbol+type+date+title
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for e in events:
        key = f"{e.get('symbol')}|{e.get('type')}|{e.get('date')}|{e.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)

    uniq.sort(key=lambda x: (-int(x.get("importance") or 0), x.get("date") or ""))

    critical = [e for e in uniq if int(e.get("importance") or 0) >= 90][:40]
    this_week_end = today + timedelta(days=7)
    upcoming = [
        e
        for e in uniq
        if (_parse_day(e.get("date")) or today) >= today
        and (_parse_day(e.get("date")) or today) <= this_week_end
    ][:40]
    filings_recent = [e for e in uniq if str(e.get("type", "")).startswith("filing_")][:40]

    return {
        "as_of": today.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "count": len(uniq[:limit]),
        "critical": critical,
        "this_week": upcoming,
        "filings_recent": filings_recent,
        "all": uniq[:limit],
        "legend": [
            {"level": "Critical (90–100)", "meaning": "Earnings today / tomorrow / this week"},
            {"level": "High (75–89)", "meaning": "Near-term earnings, fresh 8-K / 10-Q / 10-K"},
            {"level": "Medium (55–74)", "meaning": "Ex-div, scheduled earnings later this month"},
            {"level": "Watch (<55)", "meaning": "Further out or historical context"},
        ],
    }


def write_calendar(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
