"""Normalising raw client tables and suggesting a column mapping.

Everything is read as text first; types are parsed and validated in the L0 check.
"""
from __future__ import annotations

import re

import pandas as pd

from .spec import SPEC, SYNONYMS


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def normalise_table(df: pd.DataFrame, table: str, column_map: dict[str, str] | None = None) -> pd.DataFrame:
    """Lower-case column names, apply the client→spec renames, and make every value text or None."""
    df = normalise_columns(df)
    if column_map:
        df = df.rename(columns={k.lower(): v for k, v in column_map.items()})
    if table == "fx_rates" and "rate" not in df.columns:   # accept rate_to_sek etc.
        rc = [c for c in df.columns if c.startswith("rate")]
        if rc:
            df = df.rename(columns={rc[0]: "rate"})
    df = df.loc[:, ~df.columns.duplicated()]
    out = df.astype("string").astype(object)
    out = out.where(df.notna(), None)
    return out.replace({"": None})


def suggest_mapping(columns: list[str], table: str) -> dict[str, str | None]:
    """Spec column -> client column, by exact name, then normalised name, then known synonyms."""
    cols = [str(c).strip() for c in columns]
    by_key = {}
    for c in cols:
        by_key.setdefault(_key(c), c)
    out: dict[str, str | None] = {}
    used: set[str] = set()
    for spec_col in SPEC.get(table, {}):
        hit = None
        for cand in [spec_col, *SYNONYMS.get(spec_col, [])]:
            c = by_key.get(_key(cand))
            if c and c not in used:
                hit = c
                break
        if spec_col == "rate" and not hit:
            hit = next((c for c in cols if c.lower().startswith("rate") and c not in used), None)
        out[spec_col] = hit
        if hit:
            used.add(hit)
    return out


def mapping_to_renames(mapping: dict[str, str | None]) -> dict[str, str]:
    """{spec: client} from the UI -> {client: spec} renames for normalise_table."""
    return {str(client).strip().lower(): spec for spec, client in (mapping or {}).items()
            if client and str(client).strip().lower() != spec}
