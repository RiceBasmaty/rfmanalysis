"""Small helpers: JSON-safe conversion and number formatting used in generated text."""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def jsonable(obj: Any, digits: int = 6) -> Any:
    """Recursively convert numpy / pandas values into plain JSON types (NaN -> None)."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v, digits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v, digits) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return records(obj, digits)
    if isinstance(obj, pd.Series):
        return jsonable(obj.to_dict(), digits)
    if obj is None or obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else round(f, digits)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, pd.Period):
        return str(obj)
    return obj


def records(df: pd.DataFrame, digits: int = 6) -> list[dict[str, Any]]:
    return [jsonable(r, digits) for r in df.to_dict(orient="records")]


def money(v: float, currency: str) -> str:
    a = abs(v)
    s = (f"{v / 1e9:,.2f}B" if a >= 1e9 else f"{v / 1e6:,.1f}M" if a >= 1e6
         else f"{v / 1e3:,.0f}k" if a >= 1e4 else f"{v:,.0f}")
    return f"{s} {currency}"


def pct(v: float, d: int = 0) -> str:
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{d}%}"
