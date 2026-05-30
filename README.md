
# Circular Structural Reuse Matcher

Match **reclaimed steel members** (from a building to be deconstructed) to the member slots of a
**new design**, keeping only assignments that pass **Eurocode EN 1993-1-1**, and report the
embodied-CO₂ saved. A pyRevit + Python + AI project focused on circular-economy steel reuse.

> **Scope honesty:** this is a *member-level pre-feasibility* tool. It does **not** design connections,
> and its results are **decision-support, not code-certified**. Reused steel still requires physical
> verification (coupon testing, corrosion/fatigue survey) and connection design by an engineer.

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

- **Forces** come from a pluggable backend: `PyNiteFEA` (default, license-free, CI-friendly),
  SAP2000 OAPI (optional, higher fidelity), or SAP2000 table-export scrape (fallback).
- **The LLM does no arithmetic** — every number is computed in Python and injected into the report;
  the model only writes the surrounding prose.

## Status

| Phase | What | State |
|-------|------|-------|
| 0 | Repo scaffold, section catalog | ✅ |
| 1 | pyRevit extractor + JSON schema + section mapping | ✅ (extractor pending real-Revit test) |
| 2 | EN 1993-1-1 checks + force backend (PyNite) | ✅ |
| 3 | Mass + embodied-carbon material passport | ✅ |
| 4 | Synthetic dataset + ML (surrogate / reuse score / clustering) | ✅ |
| 5 | MILP matching (the flagship) | ✅ |
| 6 | Report (Jinja2 HTML) + provider-agnostic LLM narrative | ✅ (Gemini verified live; Ollama optional) |
| 7 | Real LTB (χ_LT), IFC extractor, Streamlit dashboard, trained-model artifacts | ✅ |
| 7+ | Cutting-stock (1 member → many cuts), SAP2000 backend, multi-objective | ⬜ optional |

Entry points:

```powershell
# full matching pipeline -> HTML report (uses Gemini narrative if GEMINI_API_KEY in .env)
uv run steelreuse --donor data/samples/donor.json --demand data/samples/demand.json --out reports/report.html

uv run streamlit run app.py            # interactive dashboard
uv run python -m steelreuse.ml.train   # regenerate synthetic dataset + train the surrogate
```

Revit-free ingestion: `steelreuse.ifc_extract.extract_ifc(path)` reads an IFC model (IfcOpenShell)
into the same JSON schema as the pyRevit extractor — so the whole pipeline runs without Revit.

## Layout

```
data/sections/eu_sections.csv   # standard steel section catalog (IPE/HE...)
data/samples/*.json             # sample extracted models for offline testing
extractor/pyrevit_extract.py    # runs INSIDE Revit (pyRevit CPython3 engine)
src/steelreuse/
  schema.py                     # JSON schema for extracted members
  core/sections.py              # catalog loader + robust section-name mapping
tests/                          # pytest (section mapping, later EC3 checks)
```

## Setup

> 👉 New here? Follow **[TODO.md](TODO.md)** — the step-by-step checklist of what *you* need to do
> (install Revit/pyRevit, build sample models, run the extractor, get API keys).

Requires Python ≥ 3.11. Using [uv](https://docs.astral.sh/uv/):

```powershell
uv venv
uv pip install -e ".[analysis,fea,ml,opt,llm,ui,dev]"
uv run pytest
```

The pyRevit extractor (`extractor/pyrevit_extract.py`) runs inside Revit and needs no system Python.
The rest of the pipeline runs in this CPython environment against the exported JSON.
