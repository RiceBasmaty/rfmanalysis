"""API tests: the whole flow on the sample data with local storage (no Google Cloud needed)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    s = Settings(storage=str(tmp_path_factory.mktemp("store")), local_data_dir=str(ROOT), sync_jobs=True)
    return TestClient(create_app(s))


@pytest.fixture(scope="module")
def run(client):
    r = client.post("/api/runs", json={"client": "Dummy Nordic Retail"}).json()
    src = {"type": "local", "dataset": "sample_data",
           "tables": {"transactions": "transactions.csv", "customers": "customers.csv", "items": "items.csv", "fx_rates": "fx_rates.csv"}}
    res = client.put(f"/api/runs/{r['id']}/source", json=src)
    assert res.status_code == 200, res.text
    assert res.json()["suggestions"]["transactions"]["transaction_id"] == "transaction_id"
    cfg = client.get(f"/api/runs/{r['id']}").json()["config"]
    cfg["snapshot_date"] = "2026-11-01"
    assert client.put(f"/api/runs/{r['id']}/config", json=cfg).status_code == 200
    return r["id"]


def test_spec_and_pages(client):
    spec = client.get("/api/spec").json()
    assert "transactions" in spec["spec"] and spec["local_datasets"] == ["sample_data"]
    page = client.get("/runs/20260101-abcdef12/report")
    assert page.status_code == 200 and "RFM Studio" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


def test_check_then_report(client, run):
    assert client.post(f"/api/runs/{run}/check").json()["state"] == "done"
    chk = client.get(f"/api/runs/{run}/check").json()
    assert chk["stats"]["customers"] == 24999
    assert client.post(f"/api/runs/{run}/report").json()["state"] == "done"
    rep = client.get(f"/api/runs/{run}/report").json()
    assert rep["views"]["all|all|all"]["customers"] == 24998
    got = client.get(f"/api/runs/{run}").json()
    assert got["stale"] == {"check": False, "report": False}
    assert "customer_segments.csv" in got["files"]


def test_stale_after_config_change(client, run):
    cfg = client.get(f"/api/runs/{run}").json()["config"]
    cfg["lookback_months"] = 12
    client.put(f"/api/runs/{run}/config", json=cfg)
    assert client.get(f"/api/runs/{run}").json()["stale"]["report"] is True
    cfg["lookback_months"] = 24
    client.put(f"/api/runs/{run}/config", json=cfg)
    assert client.get(f"/api/runs/{run}").json()["stale"]["report"] is False


def test_downloads_and_share(client, run):
    z = client.get(f"/api/runs/{run}/download.zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert client.get(f"/api/runs/{run}/files/../../etc/passwd").status_code == 404
    s = client.post(f"/api/runs/{run}/share", json={"days": 7}).json()
    page = client.get(f"/r/{s['token']}")
    assert page.status_code == 200 and "Dummy Nordic Retail" in page.text
    assert page.headers["x-robots-tag"].startswith("noindex")
    assert "</script" not in page.text.split('id="report-data">')[1].split("</script>")[0]
    client.delete(f"/api/runs/{run}/share/{s['token']}")
    assert client.get(f"/r/{s['token']}").status_code == 404
    assert client.get("/r/not-a-token").status_code == 404


def test_validation_errors_are_friendly(client, run):
    cfg = client.get(f"/api/runs/{run}").json()["config"]
    bad = {**cfg, "segment_rules": [["Evil", "__import__('os').system('id')"]]}
    r = client.put(f"/api/runs/{run}/config", json=bad)
    assert r.status_code == 422 and r.json()["error"]["title"] == "Segment rule not understood"
    assert client.get("/api/runs/not-an-id").status_code == 404


def test_duplicate_and_delete(client, run):
    dup = client.post("/api/runs", json={"clone_from": run}).json()
    got = client.get(f"/api/runs/{dup['id']}").json()
    assert got["meta"]["source"]["dataset"] == "sample_data" and got["config"]["snapshot_date"] is None
    assert client.delete(f"/api/runs/{dup['id']}").status_code == 200
    assert client.get(f"/api/runs/{dup['id']}").status_code == 404


def test_share_mode_serves_only_links(tmp_path):
    c = TestClient(create_app(Settings(storage=str(tmp_path), app_mode="share")))
    assert c.get("/healthz").json()["mode"] == "share"
    assert c.get("/api/runs").status_code == 404
    assert c.get("/").status_code == 404
