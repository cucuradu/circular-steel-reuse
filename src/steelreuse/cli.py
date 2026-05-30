"""Command-line entry point: donor.json + demand.json -> matching report (HTML + console summary).

    uv run steelreuse --donor data/samples/donor.json --demand data/samples/demand.json \
        --out reports/report.html
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .llm.providers import select_provider
from .llm.report import build_report_context, generate_narrative, render_html
from .pipeline import LoadModel, run_pipeline


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (stdlib): sets vars like GEMINI_API_KEY without overriding the shell."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Circular structural steel-reuse matcher")
    ap.add_argument("--donor", required=True, help="donor (supply) extraction JSON")
    ap.add_argument("--demand", required=True, help="new-design (demand) extraction JSON")
    ap.add_argument("--out", default="reports/report.html", help="output HTML path")
    ap.add_argument("--knockdown", type=float, default=1.0, help="reclaimed f_y knockdown (<=1.0)")
    ap.add_argument("--beam-udl", type=float, default=15.0, help="assumed beam UDL (kN/m == N/mm)")
    ap.add_argument("--column-axial", type=float, default=400.0, help="assumed column axial (kN)")
    args = ap.parse_args(argv)

    load_dotenv()  # pick up GEMINI_API_KEY etc. from a .env in the working directory

    loads = LoadModel(beam_udl_Npmm=args.beam_udl, column_axial_N=args.column_axial * 1e3)
    res = run_pipeline(args.donor, args.demand, loads=loads, knockdown=args.knockdown)

    ctx = build_report_context(res)
    narrative, source = generate_narrative(ctx, select_provider())
    html = render_html(ctx, narrative, source)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    print(f"Mapping: {res.validation.summary()}")
    print(f"Supply {res.supply_count} | demand slots {res.slot_count} | reused {res.match.n_reused}")
    print(f"CO2e saved by matches: {res.match.total_co2_saved_kg:.1f} kg "
          f"(full donor stock potential: {res.passport.total_saved_kgco2e:.1f} kg)")
    if res.match.unmatched_slots:
        print(f"Slots needing new steel: {', '.join(res.match.unmatched_slots)}")
    print(f"Narrative source: {source}")
    print(f"Report written -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
