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

def _yahoo_rss(symbol: str, label: str | None = None) -> dict[str, str]:
    from urllib.parse import quote

    name = label or f"Yahoo · {symbol}"
    return {
        "name": name,
        "url": f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(symbol)}&region=US&lang=en-US",
    }


# Public RSS endpoints designed for syndication (stable vs scraping HTML).
RSS_SOURCES: list[dict[str, str]] = [
    # Indexes / macro
    _yahoo_rss("^GSPC", "Yahoo · S&P 500"),
    _yahoo_rss("^IXIC", "Yahoo · Nasdaq"),
    _yahoo_rss("^DJI", "Yahoo · Dow"),
    _yahoo_rss("^RUT", "Yahoo · Russell 2000"),
    _yahoo_rss("^VIX", "Yahoo · VIX"),
    _yahoo_rss("^TNX", "Yahoo · Treasury"),
    _yahoo_rss("^FVX", "Yahoo · 5Y Yield"),
    _yahoo_rss("CL=F", "Yahoo · Oil"),
    _yahoo_rss("GC=F", "Yahoo · Gold"),
    _yahoo_rss("SI=F", "Yahoo · Silver"),
    _yahoo_rss("NG=F", "Yahoo · NatGas"),
    _yahoo_rss("BTC-USD", "Yahoo · Bitcoin"),
    _yahoo_rss("ETH-USD", "Yahoo · Ethereum"),
    _yahoo_rss("EURUSD=X", "Yahoo · EUR/USD"),
    _yahoo_rss("JPY=X", "Yahoo · USD/JPY"),
    # ETFs / sectors
    *[_yahoo_rss(s) for s in (
        "SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XLE", "XLV", "XLI", "XLY",
        "XLP", "XLU", "XLB", "XLRE", "SMH", "SOXX", "ARKK", "XBI", "IBB", "KRE",
        "GLD", "USO", "TLT", "HYG", "EEM", "EFA", "FXI", "EWJ", "EWZ", "VWO",
    )],
    # Mega / large liquid names
    *[_yahoo_rss(s) for s in (
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "GOOG", "BRK-B",
        "AVGO", "JPM", "V", "MA", "XOM", "CVX", "LLY", "UNH", "JNJ", "WMT", "PG",
        "ORCL", "CRM", "AMD", "INTC", "QCOM", "NFLX", "DIS", "BA", "CAT", "GE",
        "COST", "HD", "KO", "PEP", "ABBV", "MRK", "PFE", "TMO", "DHR", "ISRG",
        "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "BX", "UBER", "ABNB",
        "SHOP", "SNOW", "PLTR", "COIN", "HOOD", "SQ", "PYPL", "NKE", "SBUX",
        "TSM", "ASML", "SAP", "BABA", "PDD", "JD", "NIO", "SONY", "TM", "SHEL",
        "BP", "TTE", "RIO", "BHP", "VALE", "NEM", "FCX", "SLB", "COP", "OXY",
    )],
    # CNBC
    {"name": "CNBC Top News", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/15839069/device/rss/rss.html"},
    {"name": "CNBC Finance", "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html"},
    {"name": "CNBC Tech", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html"},
    {"name": "CNBC Energy", "url": "https://www.cnbc.com/id/19836768/device/rss/rss.html"},
    {"name": "CNBC Health Care", "url": "https://www.cnbc.com/id/10000108/device/rss/rss.html"},
    {"name": "CNBC Earnings", "url": "https://www.cnbc.com/id/15839135/device/rss/rss.html"},
    {"name": "CNBC World Economy", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"name": "CNBC Politics", "url": "https://www.cnbc.com/id/10000113/device/rss/rss.html"},
    {"name": "CNBC Real Estate", "url": "https://www.cnbc.com/id/10000115/device/rss/rss.html"},
    # MarketWatch / Nasdaq / wire-ish
    {"name": "MarketWatch Top", "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
    {"name": "MarketWatch Markets", "url": "https://feeds.marketwatch.com/marketwatch/marketpulse/"},
    {"name": "MarketWatch Breaking", "url": "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/"},
    {"name": "MarketWatch Bulletins", "url": "https://feeds.marketwatch.com/marketwatch/bulletins/"},
    {"name": "MarketWatch Personal Finance", "url": "https://feeds.marketwatch.com/marketwatch/pf/"},
    {"name": "Nasdaq Stocks", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Stocks"},
    {"name": "Nasdaq Markets", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Markets"},
    {"name": "Nasdaq Cryptocurrency", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Cryptocurrencies"},
    {"name": "Nasdaq Originals", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Nasdaq%20Originals"},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "BBC Technology", "url": "https://feeds.bbci.co.uk/news/technology/rss.xml"},
    {"name": "Guardian Business", "url": "https://www.theguardian.com/uk/business/rss"},
    {"name": "Guardian Markets", "url": "https://www.theguardian.com/business/stock-markets/rss"},
    {"name": "Guardian Technology", "url": "https://www.theguardian.com/uk/technology/rss"},
    {"name": "Reuters Business", "url": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news.rss"},
    {"name": "Investing.com Stock", "url": "https://www.investing.com/rss/news_25.rss"},
    {"name": "Investing.com Economy", "url": "https://www.investing.com/rss/news_95.rss"},
    {"name": "Investing.com Commodities", "url": "https://www.investing.com/rss/news_11.rss"},
    {"name": "Investing.com Crypto", "url": "https://www.investing.com/rss/news_301.rss"},
    {"name": "Investing.com Forex", "url": "https://www.investing.com/rss/news_1.rss"},
    {"name": "Fed Press", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"name": "Fed Speeches", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"name": "SEC Press", "url": "https://www.sec.gov/news/pressreleases.rss"},
    {"name": "SEC Speeches", "url": "https://www.sec.gov/news/speeches-statements.rss"},
    {"name": "NYT Business", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"},
    {"name": "NYT Economy", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml"},
    {"name": "NYT Technology", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"},
    {"name": "WSJ Markets", "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"},
    {"name": "Seeking Alpha", "url": "https://seekingalpha.com/market_currents.xml"},
    {"name": "Motley Fool", "url": "https://www.fool.com/a/feeds/partner/google-news.xml"},
]

# Topic queries via Yahoo search API (broader than ticker-only).
TOPIC_QUERIES: list[str] = [
    "stock market today",
    "Federal Reserve",
    "Jerome Powell",
    "interest rates",
    "inflation CPI",
    "PCE inflation",
    "jobs report NFP",
    "earnings season",
    "guidance raise",
    "guidance cut",
    "oil prices OPEC",
    "natural gas prices",
    "gold prices",
    "copper prices",
    "semiconductor chips",
    "NVIDIA AI",
    "AI stocks",
    "cloud computing stocks",
    "cybersecurity stocks",
    "bank stocks",
    "regional banks",
    "credit card spending",
    "merger acquisition",
    "activist investor",
    "IPO listing",
    "SPACs",
    "treasury yields",
    "bond market",
    "dollar index",
    "recession risk",
    "soft landing",
    "China stocks",
    "Japan stocks Nikkei",
    "Europe markets Stoxx",
    "emerging markets",
    "biotech FDA approval",
    "pharma pipeline",
    "electric vehicles",
    "autonomous driving",
    "solar energy stocks",
    "nuclear energy uranium",
    "cryptocurrency bitcoin",
    "ethereum ETF",
    "housing market mortgage",
    "commercial real estate",
    "consumer spending retail",
    "airline stocks travel",
    "defense stocks",
    "tariffs trade war",
    "sanctions markets",
    "short squeeze",
    "options unusual activity",
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
        ordered = [str(t).upper() for t in (universe or [])]
        self.universe_ordered = ordered
        self.universe = set(ordered)
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
        ordered = [str(t).upper() for t in tickers]
        self.universe_ordered = ordered
        self.universe = set(ordered)

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
                return src["name"], _fetch_rss(src["url"], src["name"], limit=25), None
            except Exception as exc:  # noqa: BLE001
                return src["name"], None, str(exc)

        def _yahoo_job(query: str, limit: int, label: str) -> tuple[str, list[dict[str, Any]] | None, str | None]:
            try:
                return label, _fetch_yahoo_search_news(query, limit=limit, source_label=label), None
            except Exception as exc:  # noqa: BLE001
                return label, None, str(exc)

        topic_set = set(TOPIC_QUERIES)
        # Ranked order from leaderboard (fallback: sorted set)
        ticker_slice = (self.universe_ordered or sorted(self.universe))[:400]

        with ThreadPoolExecutor(max_workers=24) as pool:
            futs = [pool.submit(_rss_job, src) for src in RSS_SOURCES]
            for topic in TOPIC_QUERIES:
                futs.append(pool.submit(_yahoo_job, topic, 8, f"Yahoo · {topic}"))
            for sym in ticker_slice:
                futs.append(pool.submit(_yahoo_job, sym, 5, f"Yahoo · {sym}"))

            topic_ok = False
            ticker_ok = 0
            for fut in as_completed(futs):
                name, fetched, err = fut.result()
                if err or fetched is None:
                    failed.append(f"{name}: {err}")
                    continue
                items.extend(fetched)
                suffix = name.split(" · ", 1)[-1] if " · " in name else name
                if name.startswith("Yahoo · ") and suffix in topic_set:
                    topic_ok = True
                elif name.startswith("Yahoo · ") and suffix in self.universe:
                    ticker_ok += 1
                else:
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

        deduped = [
            x
            for x in _dedupe(items)
            if not x.get("noise") and (not _is_stale(x, 96) or x.get("breaking"))
        ]
        # Prefer relevant+fresh: require ticker OR high-signal category OR breaking for the main desk
        relevant = [
            x
            for x in deduped
            if x.get("tickers")
            or x.get("breaking")
            or (x.get("category") in {"Earnings", "Fed / Macro", "M&A / Deals", "Markets", "Company news"})
        ]
        # If filter is too aggressive (cold start), fall back
        pool = relevant if len(relevant) >= 40 else deduped
        pool.sort(
            key=lambda x: (x.get("priority", 0), x.get("published_ts") or 0),
            reverse=True,
        )
        top = pool[:400]
        groups = _group_items(top)

        # Urgent = breaking OR very fresh (<6h) with tickers / earnings / macro
        urgent = [
            x
            for x in top
            if x.get("breaking")
            or (
                (x.get("published_ts") or 0) >= time.time() - 6 * 3600
                and (x.get("tickers") or x.get("category") in {"Earnings", "Fed / Macro", "M&A / Deals"})
            )
        ][:60]

        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "scanning": False,
            "items": top,
            "groups": groups,
            "breaking": urgent,
            "with_tickers": [x for x in top if x.get("tickers")][:120],
            "sources_ok": ok,
            "sources_failed": failed,
            "error": None,
            "count": len(top),
            "raw_fetched": len(items),
            "rss_configured": len(RSS_SOURCES),
            "topics_configured": len(TOPIC_QUERIES),
            "tickers_queried": len(ticker_slice),
            "relevance": "ticker+fresh+category filter; stale >96h demoted",
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
        score += 3.0 + min(3.0, 0.75 * len(item["tickers"]))
    cat = item.get("category") or ""
    if cat in {"Earnings", "Fed / Macro", "M&A / Deals", "Markets"}:
        score += 2.0
    if cat in {"Company news", "Tech / AI", "Energy", "Healthcare"}:
        score += 1.0
    ts = item.get("published_ts") or 0
    if ts:
        age_h = max(0.0, (time.time() - ts) / 3600.0)
        # Strong freshness preference: last 6h >> last day >> older
        if age_h <= 6:
            score += 6.0 - age_h * 0.4
        elif age_h <= 24:
            score += 3.5 - (age_h - 6) * 0.1
        elif age_h <= 72:
            score += 1.5
        else:
            score -= min(4.0, (age_h - 72) / 24.0)  # stale penalty
    else:
        score -= 1.0
    return score


def _is_stale(item: dict[str, Any], max_age_hours: float = 72.0) -> bool:
    ts = item.get("published_ts") or 0
    if not ts:
        return False  # keep undated; still ranked lower
    return (time.time() - ts) > max_age_hours * 3600


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
