"""Run the original Colab notebook headless on sample_data and save reference numbers.

The engine tests compare against these, so the app reproduces the notebook exactly.
Usage: python tests/reference/run_notebook.py notebooks/RFM_Descriptive_Cohorts.ipynb
"""
import json, sys, os, contextlib, io
import IPython.display as d
nb = json.load(open(sys.argv[1]))
cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
g = {}
here = os.path.dirname(os.path.abspath(__file__))
data = os.path.abspath(os.path.join(here, "..", "..", "sample_data"))
d.display = lambda *a, **k: None
import plotly.graph_objects as go
go.Figure.show = lambda self, *a, **k: None
for i, src in enumerate(cells):
    if "5 · Save the results" in src:
        break
    src = src.replace('SOURCE = "Google Drive folder"', 'SOURCE = "Local folder"').replace('"/content/drive/MyDrive/RFMDummyData"', repr(data))
    src = src.replace("from IPython.display import display, HTML", "from IPython.display import HTML\ndisplay = lambda *a, **k: None")
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(src, f"cell{i}", "exec"), g)
kpi, sc = g["kpi_all"], g["scored"]
ref = {
    "snapshot": str(g["SNAP"].date()), "window_start": str(g["WSTART"].date()),
    "rows_received": int(g["N0"]), "rows_clean": int(len(g["tx"])), "rows_identified": int(len(g["txi"])),
    "customers": int(len(g["cust"])), "outliers": int(g["cust"].is_outlier.sum()), "scored": int(len(sc)),
    "thresholds": {k: g["TH"][k] for k in ("recency_days_edges", "monetary_edges", "frequency_bin_edges")},
    "segment_sizes": {k: int(v) for k, v in sc.segment.value_counts().items()},
    "net_revenue": round(float(sc.monetary.sum()), 2),
    "coverage": {k: round(float(v), 6) for k, v in g["coverage"].items()},
    "dq": sorted([[r["table"], r["check"], r["severity"], r["rows"]] for r in g["dq"] if r["rows"] > 0]),
    "cleaning_log": [[r["step"], r["rows_after"]] for r in g["clog"]],
    "kpi_total": {k: round(float(v), 6) for k, v in kpi.loc["Total"].items()},
    "rfm_sum": [int(sc.R.sum()), int(sc.F.sum()), int(sc.M.sum())],
}
json.dump(ref, open(os.path.join(here, "notebook_reference.json"), "w"), indent=1)
print(json.dumps({k: ref[k] for k in ("snapshot", "customers", "scored", "segment_sizes")}, indent=1))
