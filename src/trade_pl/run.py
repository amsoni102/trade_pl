from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from trade_pl.config import ensure_dirs, load_config
from trade_pl.ingest import ingest_many, list_cached_symbols
from trade_pl.metrics import compute_many
from trade_pl.plots import plot_many
from trade_pl.rank import score_summary, write_leaderboard, write_suggested_pick
from trade_pl.universe import default_universe, load_universe_file, save_universe

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
    p.add_argument("--out", type=Path, default=None, help="Report directory (default reports/YYYY-MM-DD)")
    p.add_argument("--skip-plots", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    ensure_dirs(cfg)

    if args.universe_size is not None:
        cfg["universe_size"] = args.universe_size
    if args.lookback_days is not None:
        cfg["lookback_days"] = args.lookback_days
    if args.plot_top_k is not None:
        cfg["plot_top_k"] = args.plot_top_k

    raw_dir = Path(cfg["paths"]["data_raw"])
    metrics_dir = Path(cfg["paths"]["data_metrics"])
    plots_dir = Path(cfg["paths"]["plots"])
    reports_root = Path(cfg["paths"]["reports"])
    universes_dir = Path(cfg["paths"]["universes"])

    report_dir = args.out or (reports_root / date.today().isoformat())
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    if args.universe_file:
        tickers = load_universe_file(args.universe_file)
    else:
        tickers = default_universe(cfg["universe_size"])

    save_universe(
        tickers,
        universes_dir,
        criteria={
            "universe_size": cfg["universe_size"],
            "min_price": cfg["min_price"],
            "min_avg_volume": cfg["min_avg_volume"],
        },
    )

    ingest_meta = {"skipped": True}
    if not args.from_cache:
        logger.info("Ingesting %d tickers…", len(tickers))
        ingest_meta = ingest_many(
            tickers,
            raw_dir,
            lookback_days=cfg["lookback_days"],
            workers=cfg.get("ingest_workers", 8),
            force=args.force_ingest,
        )
        logger.info("Ingest ok=%s failed=%s", ingest_meta["ok"], len(ingest_meta["failed"]))
    else:
        logger.info("Using cache only (%s)", raw_dir)

    symbols = [s for s in tickers if (raw_dir / f"{s.replace('/', '-')}.parquet").exists()]
    if not symbols:
        # fall back to whatever is cached
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
    )
    if summary.empty:
        logger.error("No symbols passed filters.")
        return 1

    ranked = score_summary(summary, cfg.get("weights"))
    write_leaderboard(ranked, report_dir)
    write_suggested_pick(ranked, report_dir, cfg.get("weights", {}))
    # also mirror latest for the website
    latest = reports_root / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    write_leaderboard(ranked, latest)
    write_suggested_pick(ranked, latest, cfg.get("weights", {}))

    plotted: list[str] = []
    if not args.skip_plots:
        top_k = int(cfg.get("plot_top_k", 25))
        top_syms = ranked["symbol"].head(top_k).tolist()
        logger.info("Plotting top %d…", len(top_syms))
        plotted = plot_many(top_syms, metrics_dir, plots_dir)

    manifest = {
        "as_of": date.today().isoformat(),
        "report_dir": str(report_dir),
        "config": {
            "universe_size": cfg["universe_size"],
            "lookback_days": cfg["lookback_days"],
            "weights": cfg.get("weights"),
            "metrics_version": cfg.get("metrics_version"),
            "plot_top_k": cfg.get("plot_top_k"),
        },
        "tickers_requested": tickers,
        "tickers_scored": ranked["symbol"].tolist(),
        "ingest": ingest_meta,
        "plotted": plotted,
        "paths": {
            "leaderboard": str(report_dir / "leaderboard.csv"),
            "suggested_pick": str(report_dir / "suggested_pick.json"),
            "summary": str(metrics_dir / "summary.csv"),
        },
    }
    (report_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (latest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    pick = ranked.iloc[0]["symbol"]
    logger.info("Done. Suggested candidate: %s  |  report: %s", pick, report_dir)
    print(json.dumps({"suggested_pick": pick, "report": str(report_dir), "n_scored": len(ranked)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
