"""Run configuration: one JSON-serialisable object that fully describes an analysis run.

The same config + the same input tables always produce the same result, so the config is
stored with every run and can be cloned for the next period.
"""
from __future__ import annotations

import io
import re
import tokenize
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from .errors import stop
from .spec import DEFAULT_RULES, RULE_VARIABLES, SPEC

CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


@dataclass
class RunConfig:
    client_name: str = "Client"
    snapshot_date: str | None = None          # YYYY-MM-DD; None = day after the latest transaction
    lookback_months: int = 24
    reporting_currency: str = "SEK"
    # table -> {client_column: spec_column}; applied after lower-casing column names
    column_map: dict[str, dict[str, str]] = field(default_factory=dict)
    frequency_bins: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 6])
    outlier_quantile: float = 0.95
    outlier_action: str = "exclude"           # exclude | keep
    reconciliation_tolerance_pct: float = 1.0
    segment_rules: list[list[str]] = field(default_factory=lambda: [list(r) for r in DEFAULT_RULES])
    # Frozen thresholds from an earlier run (dict as saved in rfm_thresholds.json), or None
    frozen_thresholds: dict[str, Any] | None = None
    baseline_label: str | None = None         # shown in the report when thresholds are frozen
    notes: str = ""

    # ---------- (de)serialisation ----------
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunConfig":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # ---------- validation ----------
    def validate(self) -> list[str]:
        """Raise RFMStop on blocking problems; return a list of non-blocking warnings."""
        warnings: list[str] = []
        self.client_name = (self.client_name or "").strip() or "Client"
        self.reporting_currency = (self.reporting_currency or "").strip().upper()
        if not CURRENCY_RE.match(self.reporting_currency):
            stop("Reporting currency not valid", f"“{self.reporting_currency}” is not a 3-letter ISO code.",
                 "Use a code like SEK, EUR or USD.")
        if self.snapshot_date:
            try:
                pd.Timestamp(self.snapshot_date)
            except (ValueError, TypeError):
                stop("Snapshot date not valid", f"“{self.snapshot_date}” is not a date.", "Use YYYY-MM-DD.")
        try:
            self.lookback_months = int(self.lookback_months)
        except (TypeError, ValueError):
            stop("Lookback window not valid", "It must be a whole number of months.", "Use 12, 24 or 36.")
        if not 3 <= self.lookback_months <= 120:
            stop("Lookback window not valid", f"{self.lookback_months} months is out of range.",
                 "Use a value between 3 and 120 months (24 is the default).")

        bins = self.frequency_bins
        if isinstance(bins, str):
            try:
                bins = [int(x) for x in re.split(r"[,\s;]+", bins.strip()) if x]
            except ValueError:
                bins = []
        bins = [int(b) for b in bins]
        if len(bins) != 5 or bins[0] != 1 or bins != sorted(set(bins)):
            stop("Frequency bins not valid", f"Got {bins}.",
                 "Enter five ascending whole numbers starting at 1, e.g. 1, 2, 3, 4, 6.")
        self.frequency_bins = bins

        self.outlier_quantile = float(self.outlier_quantile)
        if not 0.9 <= self.outlier_quantile < 1:
            stop("Outlier quantile not valid", f"Got {self.outlier_quantile}.", "Use a value between 0.90 and 0.9999.")
        if self.outlier_action not in ("exclude", "keep"):
            stop("Outlier handling not valid", f"Got “{self.outlier_action}”.", "Choose exclude or keep.")
        self.reconciliation_tolerance_pct = float(self.reconciliation_tolerance_pct)
        if not 0 <= self.reconciliation_tolerance_pct <= 50:
            stop("Items tolerance not valid", "It must be between 0 and 50 %.", "1 % is a good default.")

        # column map: normalise and warn on unknown targets
        clean_map: dict[str, dict[str, str]] = {}
        for table, mapping in (self.column_map or {}).items():
            if table not in SPEC:
                continue
            m = {}
            for src, dst in (mapping or {}).items():
                src, dst = str(src).strip().lower(), str(dst).strip().lower()
                if not src or not dst or src == dst:
                    continue
                if dst not in SPEC[table]:
                    warnings.append(f"{table}: “{src}” is mapped to “{dst}”, which is not a spec column — ignored.")
                    continue
                m[src] = dst
            clean_map[table] = m
        self.column_map = clean_map

        rules = []
        for item in self.segment_rules or []:
            name, rule = (str(item[0]).strip(), str(item[1]).strip()) if len(item) == 2 else ("", "")
            if not name and not rule:
                continue
            if not name or not rule:
                stop("Segment rule incomplete", f"“{name or rule}” needs both a name and a condition.",
                     "Example: Champions | R >= 4 and F >= 4")
            check_rule(name, rule)
            rules.append([name, rule])
        if not rules:
            stop("No segment rules", "All segment rules are empty.", "Add at least one rule, ideally ending with a catch-all.")
        if len({n for n, _ in rules}) != len(rules):
            stop("Duplicate segment names", "Two rules have the same segment name.", "Give every rule a unique name.")
        self.segment_rules = rules

        th = self.frozen_thresholds
        if th:
            need = ("recency_days_edges", "monetary_edges", "frequency_bin_edges")
            if not all(k in th and len(th[k]) == (5 if k == "frequency_bin_edges" else 4) for k in need):
                stop("Frozen thresholds not valid", "The baseline thresholds are incomplete.",
                     "Pick another baseline run, or calculate fresh cut-offs.")
            if th.get("reporting_currency") and th["reporting_currency"] != self.reporting_currency:
                warnings.append(f"Frozen thresholds are in {th['reporting_currency']}, this run uses "
                                f"{self.reporting_currency} — monetary cut-offs are not comparable.")
        return warnings


# ---------- safe segment rules ----------
_ALLOWED_OPS = {">=", "<=", "==", "!=", ">", "<", "(", ")"}
_ALLOWED_WORDS = set(RULE_VARIABLES) | {"and", "or", "not", "True", "False"}


def check_rule(name: str, rule: str) -> None:
    """Allow only a tiny expression language (variables, numbers, comparisons, and/or/not).

    Rules are evaluated with DataFrame.eval, so they are whitelisted token by token first —
    nothing else (attribute access, function calls, strings) can get through.
    """
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(rule).readline))
    except (tokenize.TokenError, IndentationError) as ex:
        stop("Segment rule not understood", f"{name}: “{rule}” ({ex})", "Check brackets and operators.")
    for t in toks:
        if t.type in (tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT):
            continue
        if t.type == tokenize.NUMBER:
            continue
        if t.type == tokenize.NAME and t.string in _ALLOWED_WORDS:
            continue
        if t.type == tokenize.OP and t.string in _ALLOWED_OPS:
            continue
        stop("Segment rule not understood", f"{name}: “{rule}” — “{t.string}” is not allowed.",
             "Use only R, F, M, recency_days, frequency, monetary, has_prior_history, numbers, "
             "comparisons (>=, <=, ==, >, <, !=) and and / or / not.")
    sample = pd.DataFrame(dict(R=[1, 3, 5], F=[1, 3, 5], M=[1, 3, 5], recency_days=[400, 90, 5],
                               frequency=[1, 3, 8], monetary=[100.0, 900.0, 9000.0],
                               has_prior_history=[False, True, False]))
    try:
        res = sample.eval(rule)
        if not (isinstance(res, pd.Series) and res.dtype == bool):
            raise ValueError("the condition does not give True / False")
    except Exception as ex:  # noqa: BLE001 - shown to the user
        stop("Segment rule not understood", f"{name}: “{rule}” — {ex}",
             "Combine comparisons with and / or / not, e.g. R >= 4 and F == 1.")
