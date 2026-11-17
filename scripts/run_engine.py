"""Run the engine on a folder of CSVs and write the report data + exports.

    python scripts/run_engine.py sample_data --snapshot 2026-11-01 --client "Dummy Nordic Retail" --out out/

Handy for checking logic changes without the web app.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rfm_engine import RFMStop, RunConfig, run_report  # noqa: E402

TABLES = ("transactions", "customers", "items", "fx_rates")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--snapshot", default=None)
    ap.add_argument("--client", default="Client")
    ap.add_argument("--currency", default="SEK")
    ap.add_argument("--config", type=Path, help="JSON file with RunConfig fields (overrides the flags)")
    ap.add_argument("--out", type=Path, default=Path("out"))
    a = ap.parse_args()
    tables = {t: pd.read_csv(a.folder / f"{t}.csv", dtype=str) for t in TABLES if (a.folder / f"{t}.csv").exists()}
    cfg = RunConfig(client_name=a.client, snapshot_date=a.snapshot, reporting_currency=a.currency)
    if a.config:
        cfg = RunConfig.from_dict({**cfg.to_dict(), **json.loads(a.config.read_text())})
    try:
        res = run_report(tables, cfg, progress=lambda label, pct: print(f"{pct:>4.0%}  {label}", file=sys.stderr))
    except RFMStop as e:
        print(f"STOPPED: {e.title}\n{e.what}\nHow to fix: {e.fix}", file=sys.stderr)
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report.json").write_text(json.dumps(res.report))
    for name, data in res.exports.items():
        (a.out / name).write_bytes(data)
    head = res.report["views"]["all|all|all"]["overview"]["headline"]
    print(f"Done in {res.seconds}s · {head['customers']:,} customers · {len(res.report['views'])} filter views → {a.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
