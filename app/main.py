"""HTTP layer: the internal app (pages + JSON API) and the public share endpoint.

Run locally:   uvicorn app.main:app --reload
Cloud Run:     see Dockerfile and docs/DEPLOY.md
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from rfm_engine import ENGINE_VERSION, RFMStop, RunConfig, suggest_mapping
from rfm_engine.spec import DEFAULT_RULES, RULE_VARIABLES, SEGMENT_INFO, SPEC, TABLE_INFO
from . import sources
from .runs import Busy, NotFound, RunStore, config_hash
from .settings import Settings, get_settings
from .storage import open_storage

WEB = Path(__file__).parent / "web"
FILE_RE = re.compile(r"^[a-z0-9_]+\.(csv|json)$")

APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
           "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
           "base-uri 'none'; form-action 'self'")
# The shared report is one self-contained page with inline scripts; it may not load or send anything else.
SHARE_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
             "font-src https://fonts.gstatic.com; img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = RunStore(open_storage(settings.storage), settings)
    full = settings.app_mode != "share"
    app = FastAPI(title="RFM Studio", docs_url="/api/docs" if full else None, redoc_url=None, openapi_url="/api/openapi.json" if full else None)
    app.state.store, app.state.settings = store, settings

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy", SHARE_CSP if request.url.path.startswith("/r/") else APP_CSP)
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(RFMStop)
    async def _stop(_req: Request, e: RFMStop):
        return JSONResponse({"error": e.to_dict()}, status_code=422)

    @app.exception_handler(NotFound)
    async def _nf(_req: Request, _e: NotFound):
        return JSONResponse({"error": {"title": "Not found", "what": "This run does not exist (any more).", "fix": ""}}, status_code=404)

    @app.exception_handler(Busy)
    async def _busy(_req: Request, _e: Busy):
        return JSONResponse({"error": {"title": "A job is already running", "what": "Wait until it finishes.", "fix": ""}}, status_code=409)

    @app.get("/healthz")
    def health():
        return {"ok": True, "mode": settings.app_mode, "engine": ENGINE_VERSION}

    # ---------------- public share link ----------------
    @app.get("/r/{token}", response_class=HTMLResponse)
    def shared(token: str):
        html = store.shared_html(token)
        if html is None:
            return HTMLResponse(_expired_page(), status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"})
        return HTMLResponse(html, headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "private, max-age=300"})

    if not full:
        return app

    # ---------------- reference data ----------------
    @app.get("/api/spec")
    def spec():
        return {"spec": {t: [{"column": c, "type": v[0], "requirement": v[1], "description": v[2]} for c, v in cols.items()]
                         for t, cols in SPEC.items()},
                "tables": {t: {"label": v[0], "requirement": v[1], "description": v[2]} for t, v in TABLE_INFO.items()},
                "default_rules": DEFAULT_RULES, "rule_variables": RULE_VARIABLES,
                "segment_info": SEGMENT_INFO, "defaults": RunConfig().to_dict(),
                "default_project": settings.default_project,
                "local_datasets": sources.local_datasets(settings), "engine": ENGINE_VERSION}

    # ---------------- runs ----------------
    @app.get("/api/runs")
    def list_runs():
        return store.list()

    @app.post("/api/runs")
    def create_run(body: dict[str, Any] = Body(default={})):
        return store.create(str(body.get("client") or ""), body.get("clone_from"))

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        meta, cfg = store.meta(run_id), store.config(run_id)
        h = config_hash(cfg, meta.get("source"))
        stale = {k: bool(meta.get(k)) and meta[k].get("hash") != h for k in ("check", "report")}
        return {"meta": meta, "config": cfg, "status": store.status(run_id), "files": store.files(run_id), "stale": stale}

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str):
        store.delete(run_id)
        return {"ok": True}

    @app.put("/api/runs/{run_id}/config")
    def put_config(run_id: str, body: dict[str, Any] = Body(...)):
        cfg, warnings = store.save_config(run_id, body)
        return {"config": cfg, "warnings": warnings}

    # ---------------- connect ----------------
    @app.post("/api/source/tables")
    def source_tables(body: dict[str, Any] = Body(...)):
        return sources.list_tables(body, settings)

    @app.put("/api/runs/{run_id}/source")
    def put_source(run_id: str, body: dict[str, Any] = Body(...)):
        source = {k: body.get(k) for k in ("type", "project", "dataset", "tables")}
        if source["type"] not in ("bigquery", "local"):
            raise RFMStop("Source not supported", f"“{source['type']}”", "Use BigQuery.")
        info = sources.inspect(source, settings)
        suggestions = {role: suggest_mapping(v["columns"], role) for role, v in info.items()}
        store.update_meta(run_id, source=source)
        return {"source": source, "tables": info, "suggestions": suggestions}

    # ---------------- jobs ----------------
    @app.post("/api/runs/{run_id}/check")
    def start_check(run_id: str):
        return store.start(run_id, "check")

    @app.post("/api/runs/{run_id}/report")
    def start_report(run_id: str):
        return store.start(run_id, "report")

    @app.get("/api/runs/{run_id}/status")
    def status(run_id: str):
        return store.status(run_id)

    @app.get("/api/runs/{run_id}/check")
    def get_check(run_id: str):
        data = store.read(run_id, "check.json")
        if data is None:
            raise HTTPException(404, "No data check yet")
        return data

    @app.get("/api/runs/{run_id}/report")
    def get_report(run_id: str):
        data = store.st.read_bytes(f"runs/{store.check_id(run_id)}/report.json")
        if data is None:
            raise HTTPException(404, "No report yet")
        return Response(data, media_type="application/json")

    @app.get("/api/runs/{run_id}/report.html", response_class=HTMLResponse)
    def report_page(run_id: str):
        data = store.st.read_bytes(f"runs/{store.check_id(run_id)}/report.html")
        if data is None:
            raise HTTPException(404, "No report yet")
        return HTMLResponse(data, headers={"Content-Security-Policy": SHARE_CSP})

    @app.get("/api/baselines")
    def baselines():
        return [{"id": m["id"], "client": m["client"], "snapshot": m["report"]["snapshot"], "at": m["report"]["at"]}
                for m in store.list() if m.get("report")]

    # ---------------- downloads ----------------
    @app.get("/api/runs/{run_id}/files/{name}")
    def download(run_id: str, name: str):
        if not FILE_RE.match(name):
            raise HTTPException(404)
        data = store.file(run_id, name)
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type="text/csv" if name.endswith(".csv") else "application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/runs/{run_id}/download.zip")
    def download_zip(run_id: str):
        meta = store.meta(run_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in store.files(run_id):
                z.writestr(name, store.file(run_id, name) or b"")
            html = store.st.read_bytes(f"runs/{run_id}/report.html")
            if html:
                z.writestr("report.html", html)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", meta.get("client", "client")).strip("_") or "client"
        snap = (meta.get("report") or {}).get("snapshot", "")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="rfm_{slug}_{snap}.zip"'})

    # ---------------- sharing ----------------
    @app.post("/api/runs/{run_id}/share")
    def share(run_id: str, request: Request, body: dict[str, Any] = Body(default={})):
        s = store.share(run_id, int(body.get("days") or settings.share_days))
        base = settings.share_base_url or str(request.base_url).rstrip("/")
        return {**s, "url": f"{base}/r/{s['token']}"}

    @app.delete("/api/runs/{run_id}/share/{token}")
    def revoke(run_id: str, token: str):
        store.revoke(run_id, token)
        return {"ok": True}

    @app.get("/api/share-base")
    def share_base(request: Request):
        return {"base": settings.share_base_url or str(request.base_url).rstrip("/")}

    # ---------------- pages ----------------
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _expired_page() -> str:
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Link expired</title><style>body{font-family:Inter,system-ui,sans-serif;background:#F6F5F1;color:#143255;"
            "display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}main{background:#fff;border-radius:16px;"
            "padding:32px;max-width:440px}h1{font-family:'Space Grotesk',system-ui,sans-serif;margin:0 0 8px;color:#000}</style>"
            "<main><h1>This link has expired</h1><p>The report is no longer shared at this address. "
            "Ask the person who sent it for a new link.</p></main>")


app = create_app()
