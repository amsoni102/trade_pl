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
    # Broad market
    {"name": "Yahoo · S&P 500", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"},
    {"name": "Yahoo · Nasdaq", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EIXIC&region=US&lang=en-US"},
    {"name": "Yahoo · Dow", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EDJI&region=US&lang=en-US"},
    {"name": "Yahoo · Russell 2000", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5ERUT&region=US&lang=en-US"},
    {"name": "Yahoo · Oil", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=CL%3DF&region=US&lang=en-US"},
    {"name": "Yahoo · Gold", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC%3DF&region=US&lang=en-US"},
    {"name": "Yahoo · Treasury", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5ETNX&region=US&lang=en-US"},
    # Mega-cap / sector proxies
    {"name": "Yahoo · AAPL", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US"},
    {"name": "Yahoo · MSFT", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MSFT&region=US&lang=en-US"},
    {"name": "Yahoo · NVDA", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US"},
    {"name": "Yahoo · TSLA", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSLA&region=US&lang=en-US"},
    {"name": "Yahoo · AMZN", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AMZN&region=US&lang=en-US"},
    {"name": "Yahoo · META", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=META&region=US&lang=en-US"},
    {"name": "Yahoo · GOOGL", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GOOGL&region=US&lang=en-US"},
    {"name": "Yahoo · XOM", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=XOM&region=US&lang=en-US"},
    {"name": "Yahoo · JPM", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=JPM&region=US&lang=en-US"},
    {"name": "Yahoo · LLY", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=LLY&region=US&lang=en-US"},
    # CNBC
    {"name": "CNBC Top News", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/15839069/device/rss/rss.html"},
    {"name": "CNBC Finance", "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html"},
    {"name": "CNBC Tech", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html"},
    {"name": "CNBC Energy", "url": "https://www.cnbc.com/id/19836768/device/rss/rss.html"},
    {"name": "CNBC Health Care", "url": "https://www.cnbc.com/id/10000108/device/rss/rss.html"},
    {"name": "CNBC Earnings", "url": "https://www.cnbc.com/id/15839135/device/rss/rss.html"},
    {"name": "CNBC Investing", "url": "https://www.cnbc.com/id/15839069/device/rss/rss.html"},
    # MarketWatch / Nasdaq / BBC
    {"name": "MarketWatch Top", "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
    {"name": "MarketWatch Markets", "url": "https://feeds.marketwatch.com/marketwatch/marketpulse/"},
    {"name": "MarketWatch Breaking", "url": "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/"},
    {"name": "MarketWatch Bulletins", "url": "https://feeds.marketwatch.com/marketwatch/bulletins/"},
    {"name": "Nasdaq Stocks", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Stocks"},
    {"name": "Nasdaq Markets", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Markets"},
    {"name": "Nasdaq Cryptocurrency", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Cryptocurrencies"},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Guardian Business", "url": "https://www.theguardian.com/uk/business/rss"},
    {"name": "Guardian Markets", "url": "https://www.theguardian.com/business/stock-markets/rss"},
    {"name": "Reuters Business", "url": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news.rss"},
    {"name": "Investing.com Stock", "url": "https://www.investing.com/rss/news_25.rss"},
    {"name": "Investing.com Economy", "url": "https://www.investing.com/rss/news_95.rss"},
    {"name": "Investing.com Commodities", "url": "https://www.investing.com/rss/news_11.rss"},
    {"name": "Fed Press", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"name": "SEC Press", "url": "https://www.sec.gov/news/pressreleases.rss"},
]

# Topic queries via Yahoo search API (broader than ticker-only).
TOPIC_QUERIES: list[str] = [
    "stock market",
    "Federal Reserve",
    "interest rates",
    "inflation CPI",
    "earnings season",
    "oil prices",
    "semiconductor chips",
    "AI stocks",
    "bank stocks",
    "merger acquisition",
    "IPO",
    "treasury yields",
    "recession",
    "China stocks",
    "Europe markets",
    "biotech FDA",
    "electric vehicles",
    "cryptocurrency bitcoin",
    "housing market",
    "consumer spending",
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
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with self._lock:
            self._state["scanning"] = True
            self._state["error"] = None

        items: list[dict[str, Any]] = []
        ok: list[str] = []
        failed: list[str] = []

        def _rss_job(src: dict[str, str]) -> tuple[str, list[dict[str, Any]] | None, str | None]:
            try:
                return src["name"], _fetch_rss(src["url"], src["name"], limit=18), None
            except Exception as exc:  # noqa: BLE001
                return src["name"], None, str(exc)

        def _yahoo_job(query: str, limit: int, label: str) -> tuple[str, list[dict[str, Any]] | None, str | None]:
            try:
                return label, _fetch_yahoo_search_news(query, limit=limit, source_label=label), None
            except Exception as exc:  # noqa: BLE001
                return label, None, str(exc)

        with ThreadPoolExecutor(max_workers=16) as pool:
            futs = [pool.submit(_rss_job, src) for src in RSS_SOURCES]
            for topic in TOPIC_QUERIES:
                futs.append(pool.submit(_yahoo_job, topic, 6, f"Yahoo · {topic}"))
            ticker_slice = list(self.universe)[:120]
            for sym in ticker_slice:
                futs.append(pool.submit(_yahoo_job, sym, 4, f"Yahoo · {sym}"))

            topic_ok = False
            ticker_ok = 0
            for fut in as_completed(futs):
                name, fetched, err = fut.result()
                if err or fetched is None:
                    failed.append(f"{name}: {err}")
                    continue
                items.extend(fetched)
                if name.startswith("Yahoo · ") and name.split(" · ", 1)[-1] in TOPIC_QUERIES:
                    topic_ok = True
                elif name.startswith("Yahoo · ") and name.split(" · ", 1)[-1] in self.universe:
                    ticker_ok += 1
                elif not name.startswith("Yahoo · "):
                    ok.append(name)

        if topic_ok:
            ok.append("Yahoo topics")
        if ticker_ok:
            ok.append(f"Yahoo tickers ({ticker_ok})")

        for it in items:
            it["tickers"] = _match_tickers(it.get("title", ""), self.universe)
            it["breaking"] = _is_breaking(it)
            it["category"] = _categorize(it)
            it["noise"] = _is_noise(it)
            it["priority"] = _priority_score(it)

        deduped = [x for x in _dedupe(items) if not x.get("noise")]
        deduped.sort(
            key=lambda x: (x.get("priority", 0), x.get("published_ts") or 0),
            reverse=True,
        )
        top = deduped[:220]
        groups = _group_items(top)

        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "scanning": False,
            "items": top,
            "groups": groups,
            "breaking": [x for x in top if x.get("breaking")][:40],
            "with_tickers": [x for x in top if x.get("tickers")][:60],
            "sources_ok": ok,
            "sources_failed": failed,
            "error": None,
            "count": len(top),
            "raw_fetched": len(items),
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


def _fetch_yahoo_search_news(
    query: str,
    limit: int = 3,
    source_label: str | None = None,
) -> list[dict[str, Any]]:
    from urllib.parse import quote_plus

    url = (
        "https://query1.finance.yahoo.com/v1/finance/search?"
        f"q={quote_plus(query)}&quotesCount=0&newsCount={limit}"
    )
    data = json.loads(_http_get(url).decode("utf-8", errors="ignore"))
    out: list[dict[str, Any]] = []
    label = source_label or f"Yahoo · {query}"
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
                n.get("publisher") or label,
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


def _is_noise(item: dict[str, Any]) -> bool:
    title = (item.get("title") or "").lower()
    noise_bits = (
        "should you buy",
        "should you sell",
        "stocks to watch",
        "top 10",
        "top 5",
        "money-saving",
        "money saving",
        "from our readers",
        "newsletter",
        "sponsored",
        "what to expect from",
        "is a better buy",
        "outpacing its",
        "outperforming other",
    )
    return any(b in title for b in noise_bits)


def _categorize(item: dict[str, Any]) -> str:
    title = (item.get("title") or "").lower()
    source = (item.get("source") or "").lower()
    rules = [
        ("Earnings", ("earnings", "q1 ", "q2 ", "q3 ", "q4 ", "results", "eps", "guidance")),
        ("Fed / Macro", ("fed ", "inflation", "cpi", "gdp", "treasury", "rate cut", "rate hike", "jobs report", "unemployment", "mortgage")),
        ("M&A / Deals", ("acquires", "acquisition", "merger", "buyout", "takeover", "deal")),
        ("Markets", ("stock market", "s&p", "nasdaq", "dow ", "futures", "rally", "selloff", "wall street")),
        ("Tech / AI", ("ai ", "artificial intelligence", "chip", "semiconductor", "nvidia", "cloud", "software")),
        ("Energy", ("oil", "crude", "opec", "gas ", "refiner", "energy")),
        ("Healthcare", ("fda", "drug", "vaccine", "biotech", "pharma", "cancer")),
    ]
    for name, keys in rules:
        if any(k in title for k in keys):
            return name
    if "cnbc" in source or "marketwatch" in source or "yahoo" in source:
        return "Markets"
    if item.get("tickers"):
        return "Company news"
    return "General"


def _priority_score(item: dict[str, Any]) -> float:
    score = 0.0
    if item.get("breaking"):
        score += 5.0
    if item.get("tickers"):
        score += 2.0 + min(2.0, 0.5 * len(item["tickers"]))
    cat = item.get("category") or ""
    if cat in {"Earnings", "Fed / Macro", "M&A / Deals", "Markets"}:
        score += 1.5
    ts = item.get("published_ts") or 0
    if ts:
        age_h = max(0.0, (time.time() - ts) / 3600.0)
        score += max(0.0, 3.0 - age_h)  # fresher = higher
    return score


def _group_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    order = [
        "Fed / Macro",
        "Markets",
        "Earnings",
        "M&A / Deals",
        "Tech / AI",
        "Energy",
        "Healthcare",
        "Company news",
        "General",
    ]
    groups: dict[str, list[dict[str, Any]]] = {k: [] for k in order}
    for it in items:
        cat = it.get("category") or "General"
        groups.setdefault(cat, []).append(it)
    # drop empty
    return {k: v for k, v in groups.items() if v}


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
