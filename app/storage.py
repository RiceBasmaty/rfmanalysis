"""Tiny key/value file storage: Cloud Storage in production, a local folder in development.

Keys are slash-separated paths like runs/<id>/meta.json. Only the characters in SAFE_KEY are allowed,
so a key can never escape the storage root.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

SAFE_KEY = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-.]+)*$")


def _check(key: str) -> str:
    if not SAFE_KEY.match(key) or ".." in key:
        raise ValueError(f"unsafe storage key: {key!r}")
    return key


class Storage(Protocol):
    def read_bytes(self, key: str) -> bytes | None: ...
    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def exists(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str, suffix: str = "") -> list[str]: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        return self.root / _check(key)

    def read_bytes(self, key: str) -> bytes | None:
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)                      # atomic on the same file system

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        import shutil
        p = self._p(prefix.rstrip("/"))
        if p.is_dir():
            shutil.rmtree(p)

    def list(self, prefix: str, suffix: str = "") -> list[str]:
        base = self._p(prefix.rstrip("/"))
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob(f"*{suffix}") if p.is_file() and not p.name.endswith(".tmp"))


class GCSStorage:
    def __init__(self, url: str):
        from google.cloud import storage  # imported lazily so local development needs no credentials
        bucket, _, prefix = url[len("gs://"):].partition("/")
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket)
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""

    def _k(self, key: str) -> str:
        return self.prefix + _check(key)

    def read_bytes(self, key: str) -> bytes | None:
        from google.api_core.exceptions import NotFound
        try:
            return self.bucket.blob(self._k(key)).download_as_bytes()
        except NotFound:
            return None

    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.bucket.blob(self._k(key)).upload_from_string(data, content_type=content_type)

    def exists(self, key: str) -> bool:
        return self.bucket.blob(self._k(key)).exists()

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound
        try:
            self.bucket.blob(self._k(key)).delete()
        except NotFound:
            pass

    def delete_prefix(self, prefix: str) -> None:
        blobs = list(self.client.list_blobs(self.bucket, prefix=self._k(prefix.rstrip("/")) + "/"))
        for i in range(0, len(blobs), 100):
            with self.client.batch():
                for b in blobs[i:i + 100]:
                    b.delete()

    def list(self, prefix: str, suffix: str = "") -> list[str]:
        out = []
        base = self._k(prefix.rstrip("/")) + "/"
        glob = f"{base}**{suffix}" if suffix else None    # filtered server-side
        for b in self.client.list_blobs(self.bucket, prefix=base, match_glob=glob):
            if b.name.endswith(suffix):
                out.append(b.name[len(self.prefix):])
        return sorted(out)


def open_storage(url: str) -> Storage:
    return GCSStorage(url) if url.startswith("gs://") else LocalStorage(url)


def read_json(st: Storage, key: str) -> Any:
    data = st.read_bytes(key)
    return json.loads(data) if data is not None else None


def write_json(st: Storage, key: str, obj: Any) -> None:
    st.write_bytes(key, json.dumps(obj, separators=(",", ":"), default=str).encode(), "application/json")
