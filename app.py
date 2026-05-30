"""Streamlit dashboard for the Circular Structural Reuse Matcher.

    uv run streamlit run app.py

Upload a donor + demand JSON (or use the bundled samples), set load/knockdown assumptions, and view
the matching, KPIs, material passport, and download the HTML report. All numbers come from the
deterministic pipeline; the optional LLM narrative only adds prose.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from steelreuse.llm.providers import select_provider
from steelreuse.llm.report import build_report_context, generate_narrative, render_html
from steelreuse.pipeline import LoadModel, run_pipeline

SAMPLES = Path(__file__).parent / "data" / "samples"


def _save_upload(upload, fallback: Path) -> str:
    """Persist an uploaded file to a temp path, or fall back to a bundled sample."""
    if upload is None:
        return str(fallback)
    tmp = Path(tempfile.gettempdir()) / upload.name
    tmp.write_bytes(upload.getvalue())
    return str(tmp)


def main() -> None:
    import pandas as pd
    import streamlit as st

    st.set_page_config(page_title="Circular Steel Reuse Matcher", layout="wide")
    st.title("♻️ Circular Structural Reuse Matcher")
    st.caption("Member-level pre-feasibility (EN 1993-1-1). Not connection design; not code-certified.")

    with st.sidebar:
        st.header("Inputs")
        donor_up = st.file_uploader("Donor (supply) JSON", type="json")
        demand_up = st.file_uploader("New-design (demand) JSON", type="json")
        st.divider()
        beam_udl = st.slider("Beam UDL (kN/m)", 2.0, 40.0, 15.0, 1.0)
        col_axial = st.slider("Column axial (kN)", 50.0, 2000.0, 400.0, 50.0)
        knockdown = st.slider("Reclaimed f_y knockdown", 0.5, 1.0, 1.0, 0.05)
        st.caption("Using bundled samples if no files are uploaded.")

    donor = _save_upload(donor_up, SAMPLES / "donor.json")
    demand = _save_upload(demand_up, SAMPLES / "demand.json")

    res = run_pipeline(
        donor, demand,
        loads=LoadModel(beam_udl_Npmm=beam_udl, column_axial_N=col_axial * 1e3),
        knockdown=knockdown,
    )
    ctx = build_report_context(res)
    narrative, source = generate_narrative(ctx, select_provider())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Members reused", ctx["n_reused"])
    c2.metric("CO2e saved (kg)", f"{ctx['match_co2_saved_kg']:.0f}")
    c3.metric("Slots needing new steel", ctx["n_unmatched"])
    c4.metric("Donor stock potential (kg CO2e)", f"{ctx['donor_saved_co2_kg']:.0f}")

    st.info(f"{narrative}  \n*(narrative: {source})*")

    st.subheader("Assignments")
    if ctx["assignments"]:
        st.dataframe(pd.DataFrame(ctx["assignments"]), use_container_width=True)
    else:
        st.warning("No feasible reuse matches for these inputs.")

    if ctx["unknown"]:
        st.warning(f"{ctx['unknown']} donor member(s) unidentified and excluded: "
                   f"{', '.join(ctx['unknown_names'])}")

    st.subheader("Material passport (donor)")
    st.dataframe(pd.DataFrame([e.__dict__ for e in res.passport.entries]), use_container_width=True)

    html = render_html(ctx, narrative, source)
    st.download_button("Download HTML report", html, file_name="report.html", mime="text/html")
    with st.expander("Raw report context (JSON)"):
        st.code(json.dumps(ctx, indent=2), language="json")


if __name__ == "__main__":
    main()
