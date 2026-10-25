#!/usr/bin/env python3
"""Storm Impact -> Commercial Property List.

    python run.py --days 90 --min-hail 1.25
    python run.py --offline            # rebuild from data/raw cache only
    python run.py --check-sources      # verify parcel source URLs / field names
    python run.py --mark-pushed        # after importing output/ghl_import.csv into GHL
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from pipeline import build_footprints, export, fetch_parcels, fetch_storms, intersect
from pipeline.common import load_config, make_window, setup_logging


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, help="look-back window (default: config window_days)")
    ap.add_argument("--min-hail", type=float, help="min hail size in inches (default: config min_hail_in)")
    ap.add_argument("--end", type=date.fromisoformat, help="window end date YYYY-MM-DD (default: today)")
    ap.add_argument("--offline", action="store_true", help="use cached downloads only")
    ap.add_argument("--check-sources", action="store_true")
    ap.add_argument("--mark-pushed", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    setup_logging(a.verbose)

    cfg = load_config(window_days=a.days, min_hail_in=a.min_hail)
    if a.min_hail is not None:  # keep the MRMS threshold in step with the inch threshold
        cfg["mrms_mesh_threshold_mm"] = round(a.min_hail * 25.4, 1)
    if a.check_sources:
        return 1 if fetch_parcels.check_sources(cfg) else 0
    if a.mark_pushed:
        print(f"Marked {export.mark_pushed()} ledger rows as pushed to GHL")
        return 0

    window = make_window(cfg["window_days"], a.end)
    print(f"Window {window.start} .. {window.end}  min hail {cfg['min_hail_in']}\" "
          f"(MESH >= {cfg['mrms_mesh_threshold_mm']} mm)")
    raw = fetch_storms.run(window, cfg, offline=a.offline)
    fp = build_footprints.run(raw, window, cfg, offline=a.offline)
    parcels = fetch_parcels.run(fp, cfg, offline=a.offline)
    events, hits = intersect.run(parcels, fp)
    export.run(hits, events, fp, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
