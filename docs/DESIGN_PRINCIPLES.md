# Design principles

The matcher is built around five non-negotiable engineering principles. They are cited throughout
the source and documentation (e.g. *design principle 3*) wherever a piece of behaviour exists to
honour one of them.

## Hard rules

1. **No arithmetic in the language model.** Every number — utilization, embodied CO₂, off-cuts,
   assignments — is computed in Python and injected into the report via Jinja2. The language model
   only writes prose around fixed numeric tokens, and a post-generation check confirms it altered no
   number. The model is never given a calculator or numeric tool.

2. **Heavy computation runs outside Revit.** The pyRevit extractor reads the model and writes JSON,
   nothing more; numpy / scikit-learn / OR-Tools / the SAP2000 COM bridge are never imported in the
   Revit-side script. All analysis and optimisation run in a normal Python environment.

3. **The deterministic EN 1993-1-1 check is the single source of truth.** Any machine-learning
   "capacity surrogate" is a speed-only pre-screen and is never authoritative. The ML layer's role is
   reuse scoring, clustering, and anomaly flagging — not verification.

4. **Conservative by default for unknown structural assumptions.** Where a restraint or action effect
   is unknown, the tool assumes the unfavourable case and flags it — for example, lateral-torsional
   buckling is evaluated as if the member were unrestrained with an effective-length factor of 1.0.
   Favourable restraint is never assumed silently.

5. **Section identity is never silently guessed.** Revit family names that cannot be mapped to a
   catalogue section go to an explicit `unknown` bucket and are reported; a user-supplied override CSV
   is always honoured.

## Units convention

Catalogue CSV data is in catalogue units (mm, cm², cm³, cm⁴, kg/m). Values are normalised to
**newtons and millimetres** (so that MPa = N/mm²) at the boundary in `core/sections.py`. Member forces
returned by the analysis backends are in kN·m / kN and converted on ingestion.

## Force backend

The internal-force backend is pluggable in `core/forces.py`:

- **PyNite** — the default finite-element solver (exercised in CI and tests via mocks).
- **SAP2000 (OAPI)** — optional, used when SAP2000 is available on the machine.
- **SAP2000 table scrape** — a fallback path.

The automated tests must not require Revit or SAP2000 to run.
