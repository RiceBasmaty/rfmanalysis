"""Runs: one analysis of one client for one snapshot. Everything about a run lives under runs/<id>/.

    runs/<id>/meta.json        client, source, timestamps, what was built with which settings
    runs/<id>/config.json      RunConfig fields + baseline_run_id
    runs/<id>/status.json      the current / last job: state, progress, error
    runs/<id>/input/*.parquet  cached copy of the source tables (+ fingerprint.json)
    runs/<id>/check.json       data-check result
    runs/<id>/report.json      report data
    runs/<id>/report.html      self-contained report page
    runs/<id>/files/*          downloads (CSV / JSON)
    shares/<token>.json        share link: run id, expiry
    shares/<token>.html        the report exactly as it was shared
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from rfm_engine import RFMStop, RunConfig, run_check, run_report
from . import sources
from .report_html import render_report_html
from .settings import Settings
from .storage import Storage, read_json, write_json

RUN_ID_RE = re.compile(r"^[0-9]{8}-[a-f0-9]{8}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,64}$")
APP_FIELDS = ("baseline_run_id",)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def config_hash(cfg: dict[str, Any], source: dict[str, Any] | None) -> str:
    keep = {k: v for k, v in cfg.items() if k not in ("client_name", "notes")}
    return hashlib.sha256(json.dumps([keep, source], sort_keys=True, default=str).encode()).hexdigest()[:12]


class NotFound(Exception):
    pass


class Busy(Exception):
    pass


class RunStore:
    def __init__(self, storage: Storage, settings: Settings):
        self.st, self.settings = storage, settings
        self._locks: dict[str, threading.Lock] = {}
        self._pool = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="rfm-job")
        self._active: set[str] = set()
        self._active_lock = threading.Lock()

    # ---------- basics ----------
    def _lock(self, run_id: str) -> threading.Lock:
        return self._locks.setdefault(run_id, threading.Lock())

    @staticmethod
    def check_id(run_id: str) -> str:
        if not RUN_ID_RE.match(run_id or ""):
            raise NotFound(run_id)
        return run_id

    def create(self, client: str, clone_from: str | None = None) -> dict[str, Any]:
        run_id = f"{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(4)}"
        cfg = RunConfig(client_name=(client or "").strip()[:120] or "Client").to_dict()
        cfg["baseline_run_id"] = None
        source = None
        if clone_from:
            old_cfg, old_meta = self.config(clone_from), self.meta(clone_from)
            cfg = {**old_cfg, "client_name": cfg["client_name"] if client else old_cfg.get("client_name"),
                   "snapshot_date": None, "trims": {}}
            source = old_meta.get("source")
        meta = {"id": run_id, "client": cfg["client_name"], "created_at": now(), "updated_at": now(),
                "source": source, "cloned_from": clone_from, "check": None, "report": None}
        write_json(self.st, f"runs/{run_id}/config.json", cfg)
        write_json(self.st, f"runs/{run_id}/meta.json", meta)
        return meta

    def meta(self, run_id: str) -> dict[str, Any]:
        m = read_json(self.st, f"runs/{self.check_id(run_id)}/meta.json")
        if m is None:
            raise NotFound(run_id)
        return m

    def config(self, run_id: str) -> dict[str, Any]:
        c = read_json(self.st, f"runs/{self.check_id(run_id)}/config.json")
        if c is None:
            raise NotFound(run_id)
        return c

    def update_meta(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock(run_id):
            m = self.meta(run_id)
            m.update(changes, updated_at=now())
            write_json(self.st, f"runs/{run_id}/meta.json", m)
            return m

    def save_config(self, run_id: str, cfg: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Validates (raises RFMStop) and stores. Returns the normalised config and warnings."""
        self.meta(run_id)
        app = {k: cfg.get(k) for k in APP_FIELDS}
        rc = RunConfig.from_dict(cfg)
        warnings = rc.validate()
        if app.get("baseline_run_id"):
            self.check_id(app["baseline_run_id"])
        out = {**rc.to_dict(), **app}
        out["frozen_thresholds"] = None          # resolved from baseline_run_id when a job starts
        write_json(self.st, f"runs/{run_id}/config.json", out)
        self.update_meta(run_id, client=rc.client_name)
        return out, warnings

    def status(self, run_id: str) -> dict[str, Any]:
        s = read_json(self.st, f"runs/{self.check_id(run_id)}/status.json") or {"state": "idle"}
        if s.get("state") == "running":
            age = time.time() - s.get("heartbeat", 0)
            if age > self.settings.stale_minutes * 60 and run_id not in self._active:
                s = {**s, "state": "error", "error": {"title": "The job was interrupted",
                     "what": "The server restarted while the job was running.", "fix": "Run it again."}}
        return s

    def _set_status(self, run_id: str, **s: Any) -> None:
        write_json(self.st, f"runs/{run_id}/status.json", {**s, "heartbeat": time.time(), "updated_at": now()})

    def list(self) -> list[dict[str, Any]]:
        out = []
        for key in self.st.list("runs", "/meta.json"):
            parts = key.split("/")
            if len(parts) == 3:
                m = read_json(self.st, key)
                if m:
                    out.append(m)
        return sorted(out, key=lambda m: m.get("updated_at", ""), reverse=True)

    def delete(self, run_id: str) -> None:
        self.meta(run_id)
        if run_id in self._active:
            raise Busy(run_id)
        self.st.delete_prefix(f"runs/{run_id}")

    def read(self, run_id: str, name: str) -> Any:
        return read_json(self.st, f"runs/{self.check_id(run_id)}/{name}")

    def file(self, run_id: str, name: str) -> bytes | None:
        return self.st.read_bytes(f"runs/{self.check_id(run_id)}/files/{name}")

    def files(self, run_id: str) -> list[str]:
        return [k.rsplit("/", 1)[1] for k in self.st.list(f"runs/{self.check_id(run_id)}/files")]

    # ---------- jobs ----------
    def start(self, run_id: str, kind: str) -> dict[str, Any]:
        assert kind in ("check", "report")
        meta = self.meta(run_id)
        if not (meta.get("source") or {}).get("tables", {}).get("transactions"):
            raise RFMStop("Connect the data first", "No transactions table is assigned.", "Go to step 01 · Connect.")
        with self._active_lock:
            if run_id in self._active:
                raise Busy(run_id)
            self._active.add(run_id)
        self._set_status(run_id, state="running", job=kind, progress=0.0, label="Starting", started_at=now())
        if self.settings.sync_jobs:
            self._job(run_id, kind)
        else:
            self._pool.submit(self._job, run_id, kind)
        return self.status(run_id)

    def _job(self, run_id: str, kind: str) -> None:
        last = [0.0]

        def progress(label: str, pct: float) -> None:
            if time.time() - last[0] > 0.4 or pct >= 1:
                last[0] = time.time()
                self._set_status(run_id, state="running", job=kind, progress=round(pct, 3), label=label)
        try:
            meta, cfg_d = self.meta(run_id), self.config(run_id)
            cfg = self._resolve_config(cfg_d)
            tables = self._tables(run_id, meta["source"], cfg, lambda l, p: progress(l, p * 0.35))
            scaled = lambda l, p: progress(l, 0.35 + p * 0.6)   # noqa: E731
            h = config_hash(cfg_d, meta["source"])
            if kind == "check":
                res = run_check(tables, cfg, scaled)
                write_json(self.st, f"runs/{run_id}/check.json", res)
                self.update_meta(run_id, check={"at": now(), "hash": h, "verdict": res["verdict"]["level"],
                                                "customers": res["stats"]["customers"], "snapshot": res["snapshot_date"]})
            else:
                res = run_report(tables, cfg, scaled)
                progress("Writing the report page", 0.96)
                write_json(self.st, f"runs/{run_id}/report.json", res.report)
                for name, data in res.exports.items():
                    self.st.write_bytes(f"runs/{run_id}/files/{name}", data,
                                        "text/csv" if name.endswith(".csv") else "application/json")
                self.st.write_bytes(f"runs/{run_id}/report.html", render_report_html(res.report).encode(), "text/html")
                head = res.report["views"]["all|all|all"]["overview"]["headline"]
                self.update_meta(run_id, report={"at": now(), "hash": h, "customers": head["customers"],
                                                 "net_revenue": head["net_revenue"], "currency": cfg.reporting_currency,
                                                 "snapshot": res.report["meta"]["snapshot_date"], "seconds": res.seconds})
            self._set_status(run_id, state="done", job=kind, progress=1.0, label="Done", finished_at=now())
        except RFMStop as e:
            self._set_status(run_id, state="error", job=kind, error=e.to_dict(), finished_at=now())
        except Exception as ex:  # noqa: BLE001 — shown to the user, logged for us
            traceback.print_exc()
            self._set_status(run_id, state="error", job=kind, finished_at=now(), error={
                "title": "Something went wrong", "what": f"{type(ex).__name__}: {str(ex)[:300]}",
                "fix": "Try again. If it keeps happening, send this message and the run ID to the team."})
        finally:
            with self._active_lock:
                self._active.discard(run_id)

    def _resolve_config(self, cfg_d: dict[str, Any]) -> RunConfig:
        cfg = RunConfig.from_dict(cfg_d)
        base = cfg_d.get("baseline_run_id")
        if base:
            th = self.read(base, "report.json")
            if not th:
                raise RFMStop("Baseline run has no report", f"Run {base} has no thresholds to freeze.",
                              "Pick another baseline in Settings, or calculate fresh cut-offs.")
            cfg.frozen_thresholds = {**th["thresholds"], "reporting_currency": th["meta"]["currency"]}
            cfg.baseline_label = f"{th['meta']['client']} · {th['meta']['snapshot_date']}"
        return cfg

    def _tables(self, run_id: str, source: dict[str, Any], cfg: RunConfig, progress: Callable) -> dict[str, pd.DataFrame]:
        """Use the cached copy when the source has not changed; otherwise read it and cache it."""
        progress("Checking the source tables", 0.02)
        fp = sources.fingerprint(source, self.settings)
        cached = read_json(self.st, f"runs/{run_id}/input/fingerprint.json")
        cmap_key = json.dumps(cfg.column_map, sort_keys=True)
        if cached and cached.get("fp") == fp and cached.get("column_map") == cmap_key:
            progress("Using the cached copy of the tables", 0.5)
            out = {}
            for role in cached["tables"]:
                data = self.st.read_bytes(f"runs/{run_id}/input/{role}.parquet")
                if data is None:
                    break
                out[role] = pd.read_parquet(io.BytesIO(data))
            else:
                return out
        tables = sources.load(source, cfg.column_map, self.settings, progress)
        for role, df in tables.items():
            buf = io.BytesIO()
            df.astype("string").to_parquet(buf, index=False)
            self.st.write_bytes(f"runs/{run_id}/input/{role}.parquet", buf.getvalue())
        write_json(self.st, f"runs/{run_id}/input/fingerprint.json", {"fp": fp, "column_map": cmap_key, "tables": list(tables)})
        return tables

    # ---------- sharing ----------
    def share(self, run_id: str, days: int) -> dict[str, Any]:
        html = self.st.read_bytes(f"runs/{self.check_id(run_id)}/report.html")
        if html is None:
            raise RFMStop("No report yet", "Build the report before sharing it.", "Go to step 04 · Report.")
        token = secrets.token_urlsafe(24)
        days = max(1, min(int(days), 365))
        share = {"token": token, "run_id": run_id, "created_at": now(),
                 "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")}
        self.st.write_bytes(f"shares/{token}.html", html, "text/html")
        write_json(self.st, f"shares/{token}.json", share)
        meta = self.meta(run_id)
        self.update_meta(run_id, shares=[*(meta.get("shares") or []), share])
        return share

    def revoke(self, run_id: str, token: str) -> None:
        if not TOKEN_RE.match(token):
            raise NotFound(token)
        self.st.delete(f"shares/{token}.html")
        self.st.delete(f"shares/{token}.json")
        meta = self.meta(run_id)
        self.update_meta(run_id, shares=[s for s in meta.get("shares") or [] if s["token"] != token])

    def shared_html(self, token: str) -> bytes | None:
        if not TOKEN_RE.match(token or ""):
            return None
        share = read_json(self.st, f"shares/{token}.json")
        if not share or share["expires_at"] < now():
            return None
        return self.st.read_bytes(f"shares/{token}.html")
