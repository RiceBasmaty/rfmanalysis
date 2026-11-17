"""Data sources: BigQuery (production) and an optional local CSV folder (development / demos).

A source looks like:
    {"type": "bigquery", "project": "billing-project", "dataset": "data-project.dataset",
     "tables": {"transactions": "transactions", "customers": "customers", ...}}
    {"type": "local", "dataset": "sample_data", "tables": {"transactions": "transactions.csv", ...}}
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from rfm_engine.spec import SPEC, TABLES
from rfm_engine.errors import RFMStop
from .settings import Settings

DATASET_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)?$")
NAME_RE = re.compile(r"^[A-Za-z0-9_\-.$]+$")


class SourceError(RFMStop):
    pass


# ---------------- BigQuery ----------------
def _bq(project: str):
    from google.cloud import bigquery
    try:
        return bigquery.Client(project=project or None)
    except Exception as ex:  # noqa: BLE001
        raise SourceError("Could not connect to BigQuery", str(ex).splitlines()[0][:300],
                          "Check the billing project and that the app's service account can use BigQuery.") from ex


def _dataset_id(dataset: str, project: str) -> str:
    dataset = (dataset or "").strip().replace(":", ".")
    if not DATASET_RE.match(dataset):
        raise SourceError("Dataset not valid", f"“{dataset}” is not project.dataset.", "Example: my-project.client_rfm")
    return dataset if "." in dataset else f"{project}.{dataset}"


def _friendly(ex: Exception, what: str) -> SourceError:
    msg = str(ex).splitlines()[0][:300]
    if "403" in msg or "Permission" in msg or "denied" in msg.lower():
        fix = "Give the app's service account BigQuery Data Viewer on the dataset and BigQuery Read Session User on the billing project."
    elif "404" in msg or "Not found" in msg:
        fix = "Check the spelling of the project and dataset."
    else:
        fix = "Try again; if it keeps failing, check the BigQuery console for the same table."
    return SourceError(what, msg, fix)


def list_tables(source: dict[str, Any], settings: Settings) -> list[dict[str, Any]]:
    if source.get("type") == "local":
        return _local_list(source, settings)
    client = _bq(source.get("project", ""))
    ds = _dataset_id(source.get("dataset", ""), client.project)
    try:
        out = []
        for item in client.list_tables(ds):
            t = client.get_table(item.reference)
            out.append({"name": t.table_id, "rows": int(t.num_rows or 0), "size_mb": round((t.num_bytes or 0) / 1e6, 1),
                        "columns": [f.name for f in t.schema], "type": t.table_type,
                        "modified": t.modified.isoformat() if t.modified else None})
        return sorted(out, key=lambda x: x["name"])
    except SourceError:
        raise
    except Exception as ex:  # noqa: BLE001
        raise _friendly(ex, "Could not list the tables") from ex


def inspect(source: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Columns and five sample rows of each assigned table (free: no query is run)."""
    tables = _assigned(source)
    if source.get("type") == "local":
        out = {}
        for role, name in tables.items():
            df = pd.read_csv(_local_path(source, name, settings), dtype=str, nrows=5)
            out[role] = {"columns": list(df.columns), "sample": df.fillna("").values.tolist()}
        return out
    client = _bq(source.get("project", ""))
    ds = _dataset_id(source.get("dataset", ""), client.project)
    out = {}
    for role, name in tables.items():
        try:
            t = client.get_table(f"{ds}.{name}")
            rows = list(client.list_rows(t, max_results=5))
            cols = [f.name for f in t.schema]
            out[role] = {"columns": cols, "sample": [["" if r.get(c) is None else str(r.get(c)) for c in cols] for r in rows],
                         "rows": int(t.num_rows or 0)}
        except Exception as ex:  # noqa: BLE001
            raise _friendly(ex, f"Could not read the {role} table “{name}”") from ex
    return out


def fingerprint(source: dict[str, Any], settings: Settings) -> str:
    """Changes when the source or any assigned table changes — used to reuse the cached copy."""
    parts: dict[str, Any] = {"source": {k: source.get(k) for k in ("type", "project", "dataset", "tables")}}
    tables = _assigned(source)
    if source.get("type") == "local":
        parts["mtime"] = {r: _local_path(source, n, settings).stat().st_mtime for r, n in tables.items()}
    else:
        client = _bq(source.get("project", ""))
        ds = _dataset_id(source.get("dataset", ""), client.project)
        try:
            parts["modified"] = {r: str(client.get_table(f"{ds}.{n}").modified) for r, n in tables.items()}
        except Exception as ex:  # noqa: BLE001
            raise _friendly(ex, "Could not read the table details") from ex
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load(source: dict[str, Any], column_map: dict[str, dict[str, str]], settings: Settings,
         progress=lambda label, pct: None) -> dict[str, pd.DataFrame]:
    """Read only the columns the analysis uses (mapped client columns + spec-named columns) as text."""
    tables = _assigned(source)
    if "transactions" not in tables:
        raise SourceError("Transactions table not set", "Assign the transactions table on the Connect step.", "")
    out: dict[str, pd.DataFrame] = {}
    client = None
    if source.get("type") != "local":
        client = _bq(source.get("project", ""))
        ds = _dataset_id(source.get("dataset", ""), client.project)
    for i, (role, name) in enumerate(tables.items()):
        progress(f"Reading {role} from {'BigQuery' if client else 'the sample folder'}", 0.05 + 0.25 * i / max(len(tables), 1))
        wanted = {c.lower() for c in SPEC[role]} | {k.lower() for k in (column_map.get(role) or {})}
        if role == "fx_rates":
            wanted |= {"rate"}
        if client is None:
            df = pd.read_csv(_local_path(source, name, settings), dtype=str)
            out[role] = df[[c for c in df.columns if c.strip().lower() in wanted or (role == "fx_rates" and c.lower().startswith("rate"))]]
            continue
        try:
            t = client.get_table(f"{ds}.{name}")
            if (t.num_rows or 0) > settings.max_rows:
                raise SourceError("Table too large", f"{name} has {t.num_rows:,} rows; the limit is {settings.max_rows:,}.",
                                  "Filter the table in BigQuery first (e.g. to the lookback window), or raise RFM_MAX_ROWS.")
            fields = [f for f in t.schema if f.name.lower() in wanted or (role == "fx_rates" and f.name.lower().startswith("rate"))]
            df = client.list_rows(t, selected_fields=fields).to_dataframe(create_bqstorage_client=True)
        except SourceError:
            raise
        except Exception as ex:  # noqa: BLE001
            raise _friendly(ex, f"Could not read the {role} table “{name}”") from ex
        out[role] = df.astype("string")
    return out


def _assigned(source: dict[str, Any]) -> dict[str, str]:
    tables = {r: str(n).strip() for r, n in (source.get("tables") or {}).items() if r in TABLES and n and str(n).strip()}
    for n in tables.values():
        if not NAME_RE.match(n):
            raise SourceError("Table name not valid", f"“{n}”", "Use the table name exactly as in BigQuery.")
    return tables


# ---------------- local sample folders ----------------
def local_datasets(settings: Settings) -> list[str]:
    if not settings.local_data_dir:
        return []
    root = Path(settings.local_data_dir)
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.glob("*.csv"))) if root.is_dir() else []


def _local_root(source: dict[str, Any], settings: Settings) -> Path:
    ds = source.get("dataset", "")
    if ds not in local_datasets(settings):
        raise SourceError("Sample folder not found", f"“{ds}”", "Pick one of the listed sample folders.")
    return Path(settings.local_data_dir) / ds


def _local_path(source: dict[str, Any], name: str, settings: Settings) -> Path:
    p = _local_root(source, settings) / name
    if p.parent != _local_root(source, settings) or not p.exists():
        raise SourceError("File not found", f"“{name}”", "Pick one of the listed files.")
    return p


def _local_list(source: dict[str, Any], settings: Settings) -> list[dict[str, Any]]:
    root = _local_root(source, settings)
    out = []
    for p in sorted(root.glob("*.csv")):
        cols = list(pd.read_csv(p, dtype=str, nrows=0).columns)
        with p.open("rb") as f:
            rows = max(sum(1 for _ in f) - 1, 0)
        out.append({"name": p.name, "rows": rows, "size_mb": round(p.stat().st_size / 1e6, 1), "columns": cols,
                    "type": "CSV", "modified": None})
    return out
