# Circular Structural Reuse Matcher

[![CI](https://github.com/cucuradu/circular-steel-reuse/actions/workflows/ci.yml/badge.svg)](https://github.com/cucuradu/circular-steel-reuse/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An open-source, **member-level pre-feasibility** tool that matches **reclaimed steel members** (from a
building to be deconstructed) to the member slots of a **new design** — keeping only assignments that
pass **Eurocode EN 1993-1-1** — and reports the **embodied-CO₂ saved**. Use it as a one-click Revit
add-in, a web dashboard, or a command-line tool; the whole engine runs in plain CPython, so **Revit is
optional**.

> **Scope honesty:** this is a *member-level pre-feasibility* tool. It does **not** design connections,
> and its results are **decision-support, not code-certified**. Reused steel still requires physical
> verification (coupon testing, corrosion/fatigue survey) and connection design by an engineer.

📖 **[docs/OVERVIEW.md](docs/OVERVIEW.md)** — the comprehensive technical reference (architecture,
method, results, limitations) · **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)** — the EN 1993-1-1
clause→code mapping and every assumption · **[docs/VALIDATION.md](docs/VALIDATION.md)** — hand-calc /
section-table validation · **[docs/CASE_STUDY.md](docs/CASE_STUDY.md)** — a real 1000-member building
run · **[docs/DESIGN_PRINCIPLES.md](docs/DESIGN_PRINCIPLES.md)** — the five engineering ground rules.

<!-- TODO: add docs/img/steelreuse_panel.png — the SteelReuse ribbon + a colour-coded model -->

---

## Use it in Revit (recommended)

The **SteelReuse** ribbon tab runs the whole workflow without ever touching a command line: extract a
model to JSON, match donor stock to a new design, and colour the model by reuse status.

**One-time install**

1. Clone this repo and install the engine into a Python ≥ 3.11 environment (the ribbon shells out to it):
   ```powershell
   git clone https://github.com/cucuradu/circular-steel-reuse.git
   cd circular-steel-reuse
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -e ".[analysis,fea,opt,report,llm]"
   ```
2. In Revit: **pyRevit tab → Settings → Custom Extension Directories → Add Folder**, pick the
   `pyrevit_extension` folder inside your clone, **Save Settings**, then **pyRevit → Reload**.
3. A new **SteelReuse** tab appears.

**The no-terminal workflow**

1. **Extract Steel** on the donor model → `donor.json`; again on the new design → `demand.json`.
2. **Run Match** → pick the two JSONs, choose options, **Run** → review KPIs, the assignments grid
   and the result tabs; **Apply Matches** colours the model.
3. **Reuse Schedule** for a native Revit passport schedule; **Trace Match** / **Clear Matches** as needed.

👉 Full button-by-button guide: **[pyrevit_extension/README.md](pyrevit_extension/README.md)**.

## Or run the web dashboard

No Revit needed — upload extracted JSON or IFC, set loads, see the KPIs in your browser:

```powershell
pip install "steelreuse[ui] @ git+https://github.com/cucuradu/circular-steel-reuse.git"
streamlit run app.py        # opens http://localhost:8501
```

## Or the CLI

Install it as an isolated command-line tool with [pipx](https://pipx.pypa.io/):

```powershell
pipx install "steelreuse[analysis,fea,opt,report,llm] @ git+https://github.com/cucuradu/circular-steel-reuse.git"

steelreuse --demo        # bundled sample models -> reports/demo_report.html (no input files needed)
steelreuse --donor donor.json --demand demand.json --out reports/report.html
```

> Don't have `pipx`? `python -m pip install --user pipx; python -m pipx ensurepath` (then reopen the
> terminal); plain `pip install` works too. An optional Gemini API key (in a `.env` as
> `GEMINI_API_KEY=...`) adds an AI-written narrative; without it the report uses a deterministic
> summary. **The LLM does no arithmetic** — every number is computed in Python; the model only writes
> the surrounding prose.

More CLI recipes (portfolio matching, pre-demolition audit, frame analysis, sensitivity study) are in
the [CLI reference below](#cli-reference).

---

## How it works (pipeline)

```
Revit ──(pyRevit extractor)──┐
IFC   ──(ifcopenshell)───────┴──> donor.json / demand.json
                                          │
                        ┌─────────────────┴──────────────────┐
                        │  CPython pipeline (outside Revit)   │
                        │  sections → EC3 checks → forces     │
                        │  → MILP matching → carbon passport  │
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
  unsuitable stock from the supply. See [docs/PRE_DEMOLITION_AUDIT.md](docs/PRE_DEMOLITION_AUDIT.md).
- **Revit-free ingestion**: `steelreuse.ifc_extract.extract_ifc(path)` reads an IFC model (IfcOpenShell)
  into the same JSON schema as the pyRevit extractor — so the whole pipeline runs without Revit.

## Capabilities

- **EN 1993-1-1 member checks** — classification, tension, compression + buckling, biaxial bending with
  LTB (χ_LT), shear, and the full 6.3.3 beam-column interaction.
- **MILP matching** (the flagship) — coupled donor↔demand assignment maximising net CO₂ saved (or slots
  filled / reclaimed mass), with optional **cutting-stock** (one donor → many cuts).
- **Global frame analysis** — gravity load path + EN 5.3.2 sway imperfection + `--wind` + EN 1998
  `--seismic` lateral force + P-Δ, via PyNiteFEA (or an experimental SAP2000 OAPI backend).
- **Embodied-carbon passport** — mass, A1–A3 vs. reuse-process carbon, net CO₂ saved (ICE v3.0 factors).
- **Pre-demolition audit** — per-member condition/verification → `f_y` knockdown + quarantine.
- **Stewardship & portfolio** — over-spec penalty, utilisation floor, distinct-section cap, and matching
  one donor stock across several demand projects at once.
- **Sensitivity study** — `steelreuse-sensitivity`: tornado of driver swings + optional Monte-Carlo band.

**Maturity:** the core extract → check → match → report pipeline is stable and covered by 399 tests
across versions in CI. The **SAP2000** force backend and the **`--reserve` option-value** term are
experimental (the SAP2000 backend falls back to PyNite when unavailable).

## CLI reference

Once installed, the `steelreuse` command is on your PATH:

```powershell
steelreuse --demo                                  # bundled sample models -> reports/demo_report.html
steelreuse --donor donor.json --demand demand.json --out reports/report.html

# portfolio: match ONE donor stock across SEVERAL demand models at once:
steelreuse --donor donor.json --demand projectA.json projectB.json --out reports/portfolio.html

# write a per-element status JSON for the pyRevit "Apply Matches" button:
steelreuse --donor donor.json --demand demand.json --apply-matches-out status.json

# pre-demolition audit (per-member condition/verification → knockdown + quarantine):
steelreuse --donor donor.json --demand demand.json --pda audit.csv

# sanity-check an extraction (counts by role, mapped/unknown, coords) vs an expected/Revit count:
steelreuse-validate donor.json --expect 1016       # or: --schedule revit_framing.csv

# pre-demolition inventory from ANY extracted model (works even when sections don't map):
python -m steelreuse.inventory donor.json --out reports/inventory.html

# sensitivity tornado (one-at-a-time driver swings + optional Monte Carlo P5–P95 CO₂ band):
steelreuse-sensitivity --donor donor.json --demand demand.json
```

## Layout

```
src/steelreuse/data/sections/   # steel section catalogues (EU IPE/HE + UK UB/UC + US AISC W/HSS +
                                #   CHS + channels + angles) — bundled in the wheel
src/steelreuse/data/samples/*.json  # sample extracted models for offline testing — bundled in the wheel
extractor/pyrevit_extract.py    # runs INSIDE Revit (IronPython 3 engine; stdlib-only)
pyrevit_extension/              # the SteelReuse Revit add-on (register as a pyRevit extension directory)
src/steelreuse/
  schema.py                     # JSON schema for extracted members
  pipeline.py                   # end-to-end orchestration
  core/sections.py              # catalogue loader + robust section-name mapping
  core/ec3_checks.py            # EN 1993-1-1 member checks
  match/optimize.py             # the MILP matcher
tests/                          # pytest (399 tests: section mapping, EC3 checks, matcher, frame, …)
```

## Setup (development)

To work on the code, clone and install editable with all extras:

```powershell
git clone https://github.com/cucuradu/circular-steel-reuse.git
cd circular-steel-reuse
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[analysis,fea,opt,report,llm,ui,bim,dev]"
pytest          # 399 tests
ruff check .
```

`uv` works too if your machine allows it; otherwise the `pip` commands above are equivalent.

The pyRevit extractor (`extractor/pyrevit_extract.py`) runs inside Revit and needs no system Python.
The rest of the pipeline runs in this CPython environment against the exported JSON.
