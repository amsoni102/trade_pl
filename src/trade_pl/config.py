from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "default.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or DEFAULT_CONFIG
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    paths = cfg.setdefault("paths", {})
    for key, default in [
        ("data_raw", "data/raw"),
        ("data_metrics", "data/metrics"),
        ("data_enrichment", "data/enrichment"),
        ("plots", "plots"),
        ("reports", "reports"),
        ("universes", "universes"),
    ]:
        p = Path(paths.get(key, default))
        if not p.is_absolute():
            p = ROOT / p
        paths[key] = p
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    for key in ("data_raw", "data_metrics", "data_enrichment", "plots", "reports", "universes"):
        Path(cfg["paths"][key]).mkdir(parents=True, exist_ok=True)
