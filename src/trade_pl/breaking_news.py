from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Public RSS endpoints designed for syndication (stable vs scraping HTML).
RSS_SOURCES: list[dict[str, str]] = [
    {
        "name": "Yahoo Finance",
        "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
    },
    {
        "name": "CNBC Top News",
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    },
    {
        "name": "CNBC Markets",
        "url": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
    },
    {
        "name": "MarketWatch Top",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
    },
    {
        "name": "MarketWatch Markets",
        "url": "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    },
    {
        "name": "Reuters Business",
        "url": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best",
    },
    {
        "name": "BBC Business",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
    },
    {
        "name": "Nasdaq News",
        "url": "https://www.nasdaq.com/feed/rssoutbound?category=Stocks",
    },
]

TICKER_RE = re.compile(r"\b([A-Z]{1,5})(?:\s|\.|'s|/|,|:|$)", re.ASCII)
STOP_TICKERS = {
    "A", "I", "AM", "PM", "US", "USA", "CEO", "CFO", "IPO", "ETF", "AI", "EV",
    "GDP", "FED", "SEC", "EU", "UK", "NYSE", "DD", "PR", "Q", "QQQ", "SPY",
}


class BreakingNewsScanner:
    """Background scanner: polls finance RSS + Yahoo search while the site is up."""

    def __init__(
        self,
        cache_path: Path,
        refresh_seconds: int = 180,
        universe: list[str] | None = None,
    ) -> None:
        self.cache_path = cache_path
        self.refresh_seconds = max(60, refresh_seconds)
        self.universe = {t.upper() for t in (universe or [])}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "updated_at": None,
            "scanning": False,
            "items": [],
            "sources_ok": [],
            "sources_failed": [],
            "error": None,
        }
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_path.exists():
            try:
                self._state = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def set_universe(self, tickers: list[str]) -> None:
        self.universe = {t.upper() for t in tickers}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="breaking-news", daemon=True)
        self._thread.start()
        logger.info("Breaking news scanner started (every %ss)", self.refresh_seconds)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # immediate first scan, then interval
        while not self._stop.is_set():
            try:
                self.refresh_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("breaking news scan failed: %s", exc)
                with self._lock:
                    self._state["error"] = str(exc)
                    self._state["scanning"] = False
            self._stop.wait(self.refresh_seconds)

    def refresh_once(self) -> dict[str, Any]:
        with self._lock:
            self._state["scanning"] = True
            self._state["error"] = None

        items: list[dict[str, Any]] = []
        ok: list[str] = []
        failed: list[str] = []

        for src in RSS_SOURCES:
            try:
                fetched = _fetch_rss(src["url"], src["name"], limit=12)
                items.extend(fetched)
                ok.append(src["name"])
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{src['name']}: {exc}")
                logger.debug("RSS fail %s: %s", src["name"], exc)

        # Extra Yahoo headlines for top universe names (if available)
        for sym in list(self.universe)[:25]:
            try:
                items.extend(_fetch_yahoo_search_news(sym, limit=3))
            except Exception:
                continue

        for it in items:
            it["tickers"] = _match_tickers(it.get("title", ""), self.universe)
            it["breaking"] = _is_breaking(it)

        deduped = _dedupe(items)
        deduped.sort(key=lambda x: (x.get("breaking", False), x.get("published_ts") or 0), reverse=True)

        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "scanning": False,
            "items": deduped[:80],
            "sources_ok": ok,
            "sources_failed": failed,
            "error": None,
            "count": len(deduped[:80]),
        }
        with self._lock:
            self._state = payload
            self._save_cache()
        return payload


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; trade_pl/0.1; +local-research)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_date(value: str | None) -> tuple[str | None, float]:
    if not value:
        return None, 0.0
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), dt.timestamp()
    except Exception:
        try:
            # ISO-ish
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), dt.timestamp()
        except Exception:
            return value[:19], 0.0


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return re.sub(r"\s+", " ", el.text).strip()


def _fetch_rss(url: str, source: str, limit: int = 12) -> list[dict[str, Any]]:
    raw = _http_get(url)
    root = ET.fromstring(raw)
    items: list[dict[str, Any]] = []

    # RSS 2.0
    channel_items = root.findall("./channel/item")
    # Atom
    if not channel_items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns)[:limit]:
            title = _text(entry.find("a:title", ns))
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            published, ts = _parse_date(_text(entry.find("a:updated", ns)) or _text(entry.find("a:published", ns)))
            if title:
                items.append(_item(title, link, source, published, ts))
        return items[:limit]

    for node in channel_items[:limit]:
        title = _text(node.find("title"))
        link = _text(node.find("link"))
        published, ts = _parse_date(_text(node.find("pubDate")))
        if title:
            items.append(_item(title, link, source, published, ts))
    return items


def _fetch_yahoo_search_news(symbol: str, limit: int = 3) -> list[dict[str, Any]]:
    url = (
        "https://query1.finance.yahoo.com/v1/finance/search?"
        f"q={symbol}&quotesCount=0&newsCount={limit}"
    )
    data = json.loads(_http_get(url).decode("utf-8", errors="ignore"))
    out: list[dict[str, Any]] = []
    for n in data.get("news") or []:
        title = n.get("title")
        if not title:
            continue
        ts = float(n.get("providerPublishTime") or 0)
        published = (
            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else None
        )
        out.append(
            _item(
                title,
                n.get("link") or "",
                n.get("publisher") or f"Yahoo · {symbol}",
                published,
                ts,
            )
        )
    return out


def _item(title: str, link: str, source: str, published: str | None, ts: float) -> dict[str, Any]:
    key = hashlib.sha1(f"{title}|{link}".encode("utf-8")).hexdigest()[:16]
    return {
        "id": key,
        "title": title,
        "link": link,
        "source": source,
        "published": published,
        "published_ts": ts,
        "tickers": [],
        "breaking": False,
    }


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        # normalize title for near-dupes
        norm = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:80]
        if norm in seen:
            continue
        seen.add(norm)
        out.append(it)
    return out


def _match_tickers(title: str, universe: set[str]) -> list[str]:
    found: list[str] = []
    upper = title.upper()
    # Prefer explicit $TICKER
    for m in re.findall(r"\$([A-Z]{1,5})\b", upper):
        if m in universe and m not in found:
            found.append(m)
    if universe:
        for t in universe:
            if len(t) < 2:
                continue
            if re.search(rf"\b{re.escape(t)}\b", upper) and t not in found and t not in STOP_TICKERS:
                found.append(t)
            if len(found) >= 4:
                break
    return found


def _is_breaking(item: dict[str, Any]) -> bool:
    title = (item.get("title") or "").lower()
    keywords = (
        "breaking",
        "just in",
        "plunges",
        "crash",
        "soars",
        "halted",
        "trading halt",
        "emergency",
        "rate cut",
        "rate hike",
        "acquires",
        "merger",
        "bankruptcy",
        "sec charges",
        "investigation",
    )
    if any(k in title for k in keywords):
        return True
    # Only brand-new headlines get the badge (last ~45 minutes)
    ts = item.get("published_ts") or 0
    if ts and (time.time() - ts) < 45 * 60:
        return True
    return False


# Process-wide singleton used by the web app
_SCANNER: BreakingNewsScanner | None = None


def get_scanner(cache_path: Path | None = None, universe: list[str] | None = None) -> BreakingNewsScanner:
    global _SCANNER
    if _SCANNER is None:
        path = cache_path or Path("data/breaking_news.json")
        _SCANNER = BreakingNewsScanner(cache_path=path, refresh_seconds=180, universe=universe)
    elif universe is not None:
        _SCANNER.set_universe(universe)
    return _SCANNER
