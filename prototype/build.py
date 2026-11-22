"""Build the self-contained prototype page: prototype/index.html (steps 01–03 as example screens) + the app's
real styles and report code + a report JSON (gzip + base64, unpacked in the browser).

    python prototype/build.py path/to/report.json prototype/dist/index.html
"""
from __future__ import annotations

import base64
import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/web/static"


def main(report_json: Path, out: Path) -> None:
    shell = (ROOT / "prototype/index.html").read_text()
    css = (STATIC / "css/rfm.css").read_text()
    js = "\n".join(f"<script>{(STATIC / f).read_text().replace('</script', '<\\/script')}</script>"
                   for f in ("vendor/echarts-5.5.1.min.js", "js/core.js", "js/charts.js", "js/report.js"))
    data = base64.b64encode(gzip.compress(report_json.read_bytes(), 9)).decode()
    page = shell.replace("/*__APP_CSS__*/", css, 1).replace(
        "<!--__APP_JS__-->", js + f'\n<script id="report-gz" type="text/plain">{data}</script>', 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"{out} · {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
