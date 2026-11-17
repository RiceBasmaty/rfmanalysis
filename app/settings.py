"""App settings, all from environment variables (set them on the Cloud Run service)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Where runs, reports and share links are kept: gs://bucket[/prefix] in production, a folder locally.
    storage: str = field(default_factory=lambda: _env("RFM_STORAGE", "./data"))
    # full = the internal app; share = only serves shared report links (deploy a second service with this)
    app_mode: str = field(default_factory=lambda: _env("APP_MODE", "full"))
    # Default BigQuery billing project shown on the Connect step
    default_project: str = field(default_factory=lambda: _env("RFM_BQ_PROJECT") or _env("GOOGLE_CLOUD_PROJECT"))
    # Public base URL of the share service, e.g. https://rfm-share-xxxx.a.run.app (empty = this app's own URL)
    share_base_url: str = field(default_factory=lambda: _env("RFM_SHARE_BASE_URL").rstrip("/"))
    # Optional folder with sub-folders of CSVs offered as a "Sample data" source (local development / demos)
    local_data_dir: str = field(default_factory=lambda: _env("RFM_LOCAL_DATA_DIR"))
    workers: int = field(default_factory=lambda: int(_env("RFM_WORKERS", "2")))
    share_days: int = field(default_factory=lambda: int(_env("RFM_SHARE_DAYS", "90")))
    # Runs whose job has not reported progress for this long are shown as interrupted
    stale_minutes: int = field(default_factory=lambda: int(_env("RFM_STALE_MINUTES", "20")))
    # Refuse to load more rows than this from one BigQuery table (protects memory)
    max_rows: int = field(default_factory=lambda: int(_env("RFM_MAX_ROWS", "20000000")))
    sync_jobs: bool = field(default_factory=lambda: _env("RFM_SYNC_JOBS") == "1")   # tests


def get_settings() -> Settings:
    return Settings()
