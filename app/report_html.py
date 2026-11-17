"""Builds the self-contained report page: one HTML file with the styles, chart library, report code and
data inlined, so a shared link keeps working without the app (and can be saved and opened offline)."""
from __future__ import annotations

import html
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

WEB = Path(__file__).parent / "web"


@lru_cache(maxsize=1)
def _assets() -> dict[str, str]:
    s = WEB / "static"
    return {
        "template": (WEB / "report.html").read_text(),
        "css": (s / "css/rfm.css").read_text(),
        "echarts": (s / "vendor/echarts-5.5.1.min.js").read_text(),
        "core": (s / "js/core.js").read_text(),
        "charts": (s / "js/charts.js").read_text(),
        "report": (s / "js/report.js").read_text(),
    }


def _script_safe(text: str) -> str:
    # never let inlined content close the <script> element early
    return text.replace("</script", "<\\/script").replace("<!--", "<\\!--")


def render_report_html(report: dict[str, Any]) -> str:
    a = _assets()
    meta = report["meta"]
    title = f"RFM report · {meta['client']} · {meta['snapshot_date']}"
    data = json.dumps(report, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    out = a["template"]
    for key, value in (("{{TITLE}}", html.escape(title)), ("{{CSS}}", a["css"]),
                       ("{{ECHARTS}}", _script_safe(a["echarts"])), ("{{CORE_JS}}", _script_safe(a["core"])),
                       ("{{CHARTS_JS}}", _script_safe(a["charts"])), ("{{REPORT_JS}}", _script_safe(a["report"])),
                       ("{{DATA}}", data)):
        out = out.replace(key, value, 1)
    return out
