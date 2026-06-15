
# Circular Structural Reuse Matcher

[![CI](https://github.com/cucuradu/circular-steel-reuse/actions/workflows/ci.yml/badge.svg)](https://github.com/cucuradu/circular-steel-reuse/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Match **reclaimed steel members** (from a building to be deconstructed) to the member slots of a
**new design**, keeping only assignments that pass **Eurocode EN 1993-1-1**, and report the
embodied-CO₂ saved. A pyRevit + Python + AI project focused on circular-economy steel reuse.

> **Scope honesty:** this is a *member-level pre-feasibility* tool. It does **not** design connections,
> and its results are **decision-support, not code-certified**. Reused steel still requires physical
> verification (coupon testing, corrosion/fatigue survey) and connection design by an engineer.

📖 **[docs/OVERVIEW.md](docs/OVERVIEW.md)** — the comprehensive technical reference (architecture,
method, results, limitations) · **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)** — the EN 1993-1-1
clause→code mapping and every assumption · **[docs/VALIDATION.md](docs/VALIDATION.md)** — hand-calc /
section-table validation · **[docs/CASE_STUDY.md](docs/CASE_STUDY.md)** — a real 1000-member building
run · **[docs/DESIGN_PRINCIPLES.md](docs/DESIGN_PRINCIPLES.md)** — the five engineering ground rules.

## Quickstart

Requires **Python ≥ 3.11** (developed and tested on Windows). Install it as an isolated command-line
tool with [pipx](https://pipx.pypa.io/):

```powershell
pipx install "steelreuse[analysis,fea,opt,report,llm] @ git+https://github.com/cucuradu/circular-steel-reuse.git"

steelreuse --demo        # run the bundled sample models -> reports/demo_report.html
steelreuse --version
```

`--demo` needs no input files — sample donor/demand models ship inside the package. Then run it on
your own extracted models:

```powershell
steelreuse --donor donor.json --demand demand.json --out reports/report.html
```

> Don't have `pipx`? `python -m pip install --user pipx; python -m pipx ensurepath` (then reopen the
> terminal). Plain `pip install` works too — see [Setup](#setup). An optional Gemini API key (in a
> `.env` as `GEMINI_API_KEY=...`) adds an AI-written narrative; without it the report uses a
> deterministic summary.

## How it works (pipeline)

```
Revit ──(pyRevit extractor)──> donor.json / demand.json
                                      │
                    ┌─────────────────┴──────────────────┐
                    │  CPython pipeline (outside Revit)   │
                    │  sections → EC3 checks → forces     │
                    │  → ML (reuse score) → MILP matching │
                    │  → LLM narrative → report / UI      │
                    └─────────────────────────────────────┘
```

- **Forces**: by default each member is checked against closed-form per-member loads (a floor-area
  pressure × tributary width, EN 1990 factors). With `--frame-analysis` the whole demand structure is
  instead assembled and solved as one **simple-braced frame** in PyNiteFEA (license-free) — beams stay
  simply-supported but column axials then come from the **real load path** (multi-storey accumulation,
  interior vs. corner). Members without usable geometry fall back to the per-member path automatically.
- **Pre-demolition audit**: the donor inventory can carry a surveyed condition (A–D) and verification
  basis (mill cert / coupon test / documented / visual / unverified) per member — in the JSON or merged
  from a CSV with `--pda`. These drive a per-member `f_y` knockdown and **quarantine** unverified or
  unsuitable stock from the supply (honest by default: a member with no audit data is unchanged). See
  [docs/PRE_DEMOLITION_AUDIT.md](docs/PRE_DEMOLITION_AUDIT.md).
- **The LLM does no arithmetic** — every number is computed in Python and injected into the report;
  the model only writes the surrounding prose.

## Status

| Phase | What | State |
|-------|------|-------|
| 0 | Repo scaffold, section catalog | ✅ |
| 1 | pyRevit extractor + JSON schema + section mapping | ✅ (extractor pending real-Revit test) |
| 2 | EN 1993-1-1 checks + force backend (PyNite) | ✅ |
| 3 | Mass + embodied-carbon material passport | ✅ |
| 4 | Synthetic dataset + ML (surrogate / reuse score / clustering) | ✅ *(exploratory; not wired into the pipeline — see [METHODOLOGY §11](docs/METHODOLOGY.md))* |
| 5 | MILP matching (the flagship) | ✅ |
| 6 | Report (Jinja2 HTML) + provider-agnostic LLM narrative | ✅ (Gemini verified live; Ollama optional) |
| 7 | Real LTB (χ_LT), IFC extractor, Streamlit dashboard, trained-model artifacts | ✅ |
| 7+ | Cutting-stock (1 member → many cuts; **default**, `--no-cut` for whole-member-only) ✅ · **Global frame analysis** (`--frame-analysis`: gravity load path + EN 5.3.2 sway EHF + `--wind` + EN 1998 `--seismic` lateral force + P-Δ via PyNite) ✅ · **Pre-demolition audit** (`--pda`: per-member condition/verification → knockdown + quarantine) ✅ · **HSS catalog + hollow-section checks** (388 AISC rect/square HSS) ✅ · **Connection feasibility screen** (`--connections`: geometric donor-vs-design-section compatibility) ✅ · **Geometry confirmation** of fuzzy/unknown section names from measured dimensions ✅ · **Moment-shape checks** (`--moment-shape`: less-conservative LTB `C₁` via the 4-moment/`C_b` formula + 6.3.3 `C_m` from the solved moment diagram; default off) ✅ · **"Apply Matches" Revit write-back** (`--apply-matches-out`: per-element status JSON + pyRevit button colours donor/demand elements by reuse status) ✅ · **Selectable matching objective** (`--objective co2|members|mass`: net CO₂ saved / slots filled / reclaimed mass, all MILP-proven) ✅ · **Objective trade-off table** (`--pareto`: every goal solved side-by-side) ✅ · **Stock stewardship & counterfactuals** (`--counterfactual` end-of-life carbon basis · `--disposition` store/re-roll/recycle advisory · `--min-util` floor · `--w-overspec` over-spec penalty · `--max-distinct-sections` variety cap · `--demand a.json b.json …` **portfolio matching** · `--reserve` scarcity/option-value proxy, experimental — all default off; see [METHODOLOGY §7.6](docs/METHODOLOGY.md)) ✅ · **SAP2000 frame-force backend** (`--solver sap2000` via the OAPI, with a PyNite-vs-SAP2000 parity benchmark; **experimental** — falls back to PyNite when SAP2000 is unavailable; see [docs/benchmark/README.md](docs/benchmark/README.md)) ◑ · **Sensitivity & uncertainty study** (`steelreuse-sensitivity`: tornado of one-at-a-time driver swings + optional Monte Carlo P5–P95 CO₂ band; see [METHODOLOGY §10.1](docs/METHODOLOGY.md)) ✅ · ML option-value calibration ([designed, not built](docs/OPTION_VALUE_ML.md)) ⬜ · modal-spectrum seismic, true multi-objective (Pareto) ⬜ | ◑ partial |

Entry points (once installed, the `steelreuse` command is on your PATH):

```powershell
steelreuse --demo                                  # bundled sample models -> reports/demo_report.html
steelreuse --donor donor.json --demand demand.json --out reports/report.html

# portfolio: match ONE donor stock across SEVERAL demand models at once (per-project + global report):
steelreuse --donor donor.json --demand projectA.json projectB.json --out reports/portfolio.html

# also write a per-element status JSON for the pyRevit "Apply Matches" button:
steelreuse --donor donor.json --demand demand.json --apply-matches-out status.json

streamlit run app.py                               # interactive dashboard (needs the [ui] extra)
python -m steelreuse.ml.train                      # regenerate synthetic dataset + train the surrogate

# sanity-check an extraction (counts by role, mapped/unknown, coords) vs an expected/Revit count:
steelreuse-validate donor.json --expect 1016       # or: --schedule revit_framing.csv

# pre-demolition inventory from ANY extracted model (works even when sections don't map):
python -m steelreuse.inventory donor.json --out reports/inventory.html
```

Revit-free ingestion: `steelreuse.ifc_extract.extract_ifc(path)` reads an IFC model (IfcOpenShell)
into the same JSON schema as the pyRevit extractor — so the whole pipeline runs without Revit.

## Layout

```
src/steelreuse/data/sections/   # steel section catalogs (EU IPE/HE + US AISC W + HSS) — bundled in the wheel
src/steelreuse/data/samples/*.json  # sample extracted models for offline testing — bundled in the wheel
extractor/pyrevit_extract.py    # runs INSIDE Revit (IronPython 3 engine; stdlib-only)
src/steelreuse/
  schema.py                     # JSON schema for extracted members
  core/sections.py              # catalog loader + robust section-name mapping
tests/                          # pytest (section mapping, later EC3 checks)
```

## Setup (development)

To work on the code (rather than just use the CLI), clone and install editable with all extras:

```powershell
git clone https://github.com/cucuradu/circular-steel-reuse.git
cd circular-steel-reuse
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[analysis,fea,ml,opt,report,llm,ui,bim,dev]"
pytest          # 217 tests
ruff check .
```

`uv` works too if your machine allows it; if unsigned binaries are blocked (Windows Application
Control / WDAC / Smart App Control), see **[docs/UNBLOCK_UV.md](docs/UNBLOCK_UV.md)** for how to
diagnose and unblock it, or just use the `pip` commands above.

The pyRevit extractor (`extractor/pyrevit_extract.py`) runs inside Revit and needs no system Python.
The rest of the pipeline runs in this CPython environment against the exported JSON. The author's
personal setup checklist (installing Revit/pyRevit, building test models, getting API keys) lives in
[TODO.md](TODO.md).
