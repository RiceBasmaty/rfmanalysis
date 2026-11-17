"""RFM engine: data check (L0), segmentation (L1), markets (L2), cohorts (L5).

Pure Python + pandas, no web or cloud dependencies, so it can be tested and reused on its own.
"""
from .config import RunConfig
from .errors import RFMStop
from .load import suggest_mapping
from .pipeline import ENGINE_VERSION, ReportResult, run_check, run_report

__all__ = ["RunConfig", "RFMStop", "ReportResult", "run_check", "run_report", "suggest_mapping", "ENGINE_VERSION"]
