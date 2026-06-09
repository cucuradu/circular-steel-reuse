"""Streamlit dashboard for the Circular Structural Reuse Matcher.

    streamlit run app.py          # needs the [ui] extra: pip install "steelreuse[ui]"

Upload a donor + demand JSON (or use the bundled samples), set the load model and analysis options
(mirroring the CLI), and view the matching, KPIs, material passport, and download the HTML report.
All numbers come from the deterministic pipeline; the optional LLM narrative only adds prose.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from steelreuse.core.loads import AreaLoadModel
from steelreuse.llm.providers import select_provider
from steelreuse.llm.report import build_report_context, generate_narrative, render_html
from steelreuse.pipeline import run_pipeline
from steelreuse.resources import SAMPLES_DIR as SAMPLES
from steelreuse.schema import ExtractionError


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
        st.caption("Using bundled samples if no files are uploaded.")

        with st.expander("Load model (area-based, EN 1990)", expanded=True):
            dead = st.number_input("Permanent g_k (kN/m²)", 0.5, 20.0, 3.5, 0.5)
            live = st.number_input("Imposed q_k (kN/m²)", 0.5, 20.0, 3.0, 0.5)
            gamma_g = st.number_input("γ_G (permanent)", 1.0, 1.5, 1.35, 0.05)
            gamma_q = st.number_input("γ_Q (variable)", 1.0, 1.8, 1.5, 0.05)
            trib_width = st.number_input("Default beam tributary width (m)", 0.5, 12.0, 3.0, 0.5)
            knockdown = st.slider("Reclaimed f_y knockdown", 0.5, 1.0, 1.0, 0.05)

        with st.expander("Analysis options", expanded=False):
            trib_from_geometry = st.checkbox("Estimate tributary from geometry", value=False)
            frame_analysis = st.checkbox("Global frame analysis (PyNite)", value=False)
            phi = st.number_input("Sway imperfection φ (0 = off)", 0.0, 0.02, 0.0, 0.001, format="%.3f")
            wind = st.number_input("Wind pressure (kN/m², frame only)", 0.0, 5.0, 0.0, 0.1)
            seismic = st.number_input("Seismic Cs (frame only)", 0.0, 1.0, 0.0, 0.05)
            allow_cutting = st.checkbox("Cutting-stock (1 donor → many cuts)", value=False)
            all_demand = st.checkbox("Include non-steel demand", value=False)

    donor = _save_upload(donor_up, SAMPLES / "donor.json")
    demand = _save_upload(demand_up, SAMPLES / "demand.json")

    loads = AreaLoadModel(
        dead_kpa=dead, live_kpa=live, gamma_g=gamma_g, gamma_q=gamma_q,
        beam_tributary_width_m=trib_width, notional_phi=phi,
    )

    try:
        res = run_pipeline(
            donor, demand, loads=loads, knockdown=knockdown,
            steel_only_demand=not all_demand, tributary_from_geometry=trib_from_geometry,
            allow_cutting=allow_cutting, frame_analysis=frame_analysis,
            wind_kpa=wind, seismic_cs=seismic,
        )
    except ExtractionError as e:
        st.error(f"Could not read an input model: {e}")
        st.stop()
    except Exception as e:  # noqa: BLE001 — surface any pipeline failure in the UI, not a crash
        st.error(f"Pipeline failed: {type(e).__name__}: {e}")
        st.stop()

    ctx = build_report_context(res)
    narrative, source = generate_narrative(ctx, select_provider())

    if res.frame is not None:
        if res.frame.ok:
            notes = f" — {'; '.join(res.frame.warnings)}" if res.frame.warnings else ""
            st.caption(f"Forces: frame analysis (PyNite), {res.frame.node_count} nodes, "
                       f"{res.frame.member_count} members{notes}")
        else:
            why = res.frame.warnings[0] if res.frame.warnings else "unavailable"
            st.caption(f"Forces: analytic (frame analysis not applied — {why})")

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
        st.warning(f"{ctx['unknown']} donor member(s) across {ctx['unknown_kinds']} type(s) "
                   "unidentified and excluded (not in the steel catalog):")
        st.dataframe(pd.DataFrame(ctx["unknown_breakdown"]), use_container_width=True)

    st.subheader("Material passport (donor)")
    st.dataframe(pd.DataFrame([e.__dict__ for e in res.passport.entries]), use_container_width=True)

    html = render_html(ctx, narrative, source)
    st.download_button("Download HTML report", html, file_name="report.html", mime="text/html")
    with st.expander("Raw report context (JSON)"):
        st.code(json.dumps(ctx, indent=2), language="json")


if __name__ == "__main__":
    main()
