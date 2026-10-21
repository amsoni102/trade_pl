from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from trade_pl.config import ensure_dirs, load_config
from trade_pl.enrichment import enrich_many
from trade_pl.ingest import ingest_many, list_cached_symbols
from trade_pl.metrics import compute_many
from trade_pl.plots import plot_many
from trade_pl.rank import (
    score_summary,
    write_horizon_leaders,
    write_leaderboard,
    write_sector_leaders,
    write_suggested_pick,
)
from trade_pl.universe import (
    default_universe,
    load_universe_file,
    market_map_for,
    save_universe,
    sector_map_for,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("trade_pl")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="trade_pl screening pipeline")
    p.add_argument("--config", type=Path, default=None, help="Path to YAML config")
    p.add_argument("--universe-size", type=int, default=None)
    p.add_argument("--universe-file", type=Path, default=None)
    p.add_argument("--lookback-days", type=int, default=None)
    p.add_argument("--from-cache", action="store_true", help="Skip download; use data/raw only")
    p.add_argument("--force-ingest", action="store_true", help="Re-download even if cache is fresh")
    p.add_argument("--plot-top-k", type=int, default=None)
    p.add_argument("--enrich-top-k", type=int, default=None)
    p.add_argument("--skip-plots", action="store_true")
    p.add_argument("--skip-enrich", action="store_true", help="Skip news/financials enrichment")
    p.add_argument("--out", type=Path, default=None, help="Report directory (default reports/YYYY-MM-DD)")
    return p


def _mirror_reports(ranked, report_dir: Path, latest: Path, cfg: dict) -> None:
    write_leaderboard(ranked, report_dir)
    write_suggested_pick(ranked, report_dir, cfg.get("weights", {}))
    write_horizon_leaders(ranked, report_dir, n=int(cfg.get("horizon_top_n", 25)))
    write_sector_leaders(ranked, report_dir, n=int(cfg.get("sector_top_n", 15)))
    latest.mkdir(parents=True, exist_ok=True)
    write_leaderboard(ranked, latest)
    write_suggested_pick(ranked, latest, cfg.get("weights", {}))
    write_horizon_leaders(ranked, latest, n=int(cfg.get("horizon_top_n", 25)))
    write_sector_leaders(ranked, latest, n=int(cfg.get("sector_top_n", 15)))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # yfinance logs every 404/delist as ERROR — drown the useful pipeline progress.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    cfg = load_config(args.config)
    # ensure enrichment path exists
    enrich_path = cfg["paths"].get("data_enrichment")
    if enrich_path is None:
        from trade_pl.config import ROOT

        enrich_path = ROOT / "data" / "enrichment"
        cfg["paths"]["data_enrichment"] = enrich_path
    Path(cfg["paths"]["data_enrichment"]).mkdir(parents=True, exist_ok=True)
    ensure_dirs(cfg)

    if args.universe_size is not None:
        cfg["universe_size"] = args.universe_size
    if args.lookback_days is not None:
        cfg["lookback_days"] = args.lookback_days
    if args.plot_top_k is not None:
        cfg["plot_top_k"] = args.plot_top_k
    if args.enrich_top_k is not None:
        cfg["enrich_top_k"] = args.enrich_top_k

    raw_dir = Path(cfg["paths"]["data_raw"])
    metrics_dir = Path(cfg["paths"]["data_metrics"])
    plots_dir = Path(cfg["paths"]["plots"])
    enrich_dir = Path(cfg["paths"]["data_enrichment"])
    reports_root = Path(cfg["paths"]["reports"])
    universes_dir = Path(cfg["paths"]["universes"])

    report_dir = Path(args.out or (reports_root / date.today().isoformat()))
    report_dir.mkdir(parents=True, exist_ok=True)

    if args.universe_file:
        tickers = load_universe_file(args.universe_file)
    else:
        tickers = default_universe(cfg["universe_size"])

    smap = sector_map_for(tickers)
    mmap = market_map_for(tickers)
    save_universe(
        tickers,
        universes_dir,
        criteria={
            "universe_size": cfg["universe_size"],
            "min_price": cfg["min_price"],
            "min_avg_volume": cfg["min_avg_volume"],
            "sectors": sorted(set(smap.values())),
            "markets": sorted(set(mmap.values())),
        },
    )

    ingest_meta: dict = {"skipped": True}
    if not args.from_cache:
        logger.info("Ingesting %d tickers across %d sectors…", len(tickers), len(set(smap.values())))
        ingest_meta = ingest_many(
            tickers,
            raw_dir,
            lookback_days=cfg["lookback_days"],
            workers=int(cfg.get("ingest_workers", 3)),
            force=args.force_ingest,
            min_interval=float(cfg.get("ingest_min_interval", 0.4)),
        )
        logger.info(
            "Ingest ok=%s failed=%s saved=%s cached=%s",
            ingest_meta["ok"],
            len(ingest_meta["failed"]),
            ingest_meta.get("saved"),
            ingest_meta.get("cached"),
        )
    else:
        logger.info("Using cache only (%s)", raw_dir)

    symbols = [s for s in tickers if (raw_dir / f"{s.replace('/', '-')}.parquet").exists()]
    if not symbols:
        symbols = list_cached_symbols(raw_dir)
    if not symbols:
        logger.error("No cached data. Run without --from-cache first.")
        return 1

    logger.info("Computing metrics for %d symbols…", len(symbols))
    summary = compute_many(
        symbols,
        raw_dir,
        metrics_dir,
        min_history_days=cfg["min_history_days"],
        min_price=cfg["min_price"],
        min_avg_volume=cfg["min_avg_volume"],
        sector_map=smap,
        market_map=mmap,
    )
    if summary.empty:
        logger.error("No symbols passed filters.")
        return 1

    ranked = score_summary(summary, cfg.get("weights"))
    latest = reports_root / "latest"
    _mirror_reports(ranked, report_dir, latest, cfg)

    plotted: list[str] = []
    if not args.skip_plots:
        top_k = int(cfg.get("plot_top_k", 40))
        top_syms = ranked["symbol"].head(top_k).tolist()
        logger.info("Plotting top %d…", len(top_syms))
        plotted = plot_many(top_syms, metrics_dir, plots_dir)

    enriched: list[str] = []
    if not args.skip_enrich:
        enrich_k = int(cfg.get("enrich_top_k", 300))
        # Enrich a large top set + horizon leaders + per-sector depth
        want = set(ranked["symbol"].head(enrich_k).tolist())
        for col in ["return_1w", "return_1m", "return_3m", "return_6m", "return_1y"]:
            if col in ranked.columns:
                want.update(ranked.nlargest(30, col)["symbol"].tolist())
        per_sector = max(15, enrich_k // max(1, ranked["sector"].nunique()))
        for _sector, group in ranked.groupby("sector"):
            want.update(group.head(per_sector)["symbol"].tolist())
        enrich_list = [s for s in ranked["symbol"].tolist() if s in want][:enrich_k]
        logger.info("Enriching %d names (news, financials, calendar)…", len(enrich_list))
        enriched = enrich_many(
            enrich_list,
            enrich_dir,
            workers=int(cfg.get("enrich_workers", 6)),
        )

    manifest = {
        "as_of": date.today().isoformat(),
        "report_dir": str(report_dir),
        "config": {
            "universe_size": cfg["universe_size"],
            "lookback_days": cfg["lookback_days"],
            "weights": cfg.get("weights"),
            "metrics_version": cfg.get("metrics_version"),
            "plot_top_k": cfg.get("plot_top_k"),
            "enrich_top_k": cfg.get("enrich_top_k"),
        },
        "tickers_requested": len(tickers),
        "tickers_scored": len(ranked),
        "sectors": sorted(ranked["sector"].dropna().unique().tolist()) if "sector" in ranked.columns else [],
        "ingest": {k: ingest_meta[k] for k in ingest_meta if k != "ok_symbols"},
        "plotted": plotted,
        "enriched": enriched,
        "paths": {
            "leaderboard": str(report_dir / "leaderboard.csv"),
            "suggested_pick": str(report_dir / "suggested_pick.json"),
            "leaders_by_horizon": str(report_dir / "leaders_by_horizon.json"),
            "leaders_by_sector": str(report_dir / "leaders_by_sector.json"),
            "summary": str(metrics_dir / "summary.csv"),
            "enrichment": str(enrich_dir),
        },
    }
    (report_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (latest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    pick = ranked.iloc[0]["symbol"]
    logger.info("Done. Suggested candidate: %s  |  scored=%d  |  report: %s", pick, len(ranked), report_dir)
    print(
        json.dumps(
            {
                "suggested_pick": pick,
                "report": str(report_dir),
                "n_scored": len(ranked),
                "n_enriched": len(enriched),
                "sectors": manifest["sectors"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
