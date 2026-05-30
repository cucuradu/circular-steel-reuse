# CLAUDE.md — Circular Structural Reuse Matcher

Guidance for working in this repo. Read alongside the full plan at
`C:\Users\Radu\.claude\plans\i-am-civil-eng-quirky-giraffe.md`.

## What this is
A pyRevit + Python + AI tool that matches **reclaimed steel members** to a **new design's** member
slots, keeping only Eurocode EN 1993-1-1-passing assignments, and reports embodied-CO₂ saved.
Member-level pre-feasibility screening — **not** connection design, **not** code-certified.

## Hard rules (do not violate)
1. **The LLM never does arithmetic.** All numbers (utilization, CO₂, off-cuts, assignments) are
   computed in Python and injected into reports via Jinja2. The LLM only writes prose around fixed
   numeric tokens; a post-check confirms it altered no number. Never expose calculator tools to it.
2. **Heavy compute runs OUTSIDE Revit.** The pyRevit extractor (`extractor/`) only reads the model and
   writes JSON. numpy/sklearn/ortools/SAP-COM must not be imported in the Revit-side script.
3. **The deterministic EN 1993 check is the source of truth.** Any ML "capacity surrogate" is a
   speed-only pre-screen, never authoritative. ML's real job = reuse-score, clustering, anomaly flags.
4. **Conservative by default** for unknown structural assumptions (e.g. LTB: assume unrestrained,
   Lcr factor 1.0, and flag it) — never silently assume favourable restraint.
5. **Never silently guess section identity.** Unmapped Revit names go to an `unknown` bucket and are
   reported; honor the user override CSV.

## Units convention
Catalog CSV is in catalogue units (mm, cm², cm³, cm⁴, kg/m). Internally, normalize to **N, mm**
(so MPa = N/mm²) at the boundary in `core/sections.py`. Forces from backends are kN·m / kN → convert.

## Force backend
Pluggable in `core/forces.py`: `PyNiteFEA` (default, used in CI/tests via mocks), SAP2000 OAPI
(optional), SAP2000 table scrape (fallback). Tests must not require Revit or SAP2000.

## Dev workflow
- `uv venv && uv pip install -e ".[analysis,fea,ml,opt,llm,ui,dev]"`
- `uv run pytest` — Phase 1 tests use the standard library only (no heavy deps needed).
- `uv run ruff check .`
- Keep each phase shippable on its own (see README status table).
