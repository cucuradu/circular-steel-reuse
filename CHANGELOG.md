# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-09

First public, release-engineered version. The deterministic EN 1993-1-1 core is unchanged; this
release locks in the previously-uncommitted analysis work and turns the project into a distributable
tool (CI, license, releases).

### Added
- **Global frame analysis** (`--frame-analysis`, `core/frame.py`): the demand model is assembled into
  one connected simple-braced frame (pinned beams, continuous columns, fixed bases) and solved in
  PyNiteFEA, so column axials come from the real load path. Includes EN 1993-1-1 §5.3.2 sway
  imperfection as equivalent horizontal forces + P-Δ (`--phi` / `--pdelta`), wind storey forces
  (`--wind`, EN 1991-1-4 input), the EN 1998-1 lateral-force seismic method (`--seismic`), and
  multi-span beam splitting at interior supports. Per-member analytic fallback where geometry is missing.
- **Load-combination envelope**: members are verified against every ULS combination; the governing
  (worst-utilisation) case is reported, and reuse plus the avoided-new baseline must pass all of them.
- **Optional cutting-stock** mode (`--cut`): one donor cut into several pieces for several slots.
- **MIT `LICENSE`** file (the project already declared MIT in metadata).
- **CI** (GitHub Actions, Windows runner, Python 3.11 + 3.12: ruff + pytest) and a tag-driven
  **release** workflow that builds the wheel/sdist and the thesis PDF.
- `THESIS_PRO.md` (canonical thesis) and `build_thesis_pdf.py` (Markdown → HTML → PDF with inline SVG
  figures).

### Changed
- Avoided-new baseline is now **standard-aware** (EU vs US): the lightest-adequate new section is
  searched within the slot's own standard.
- EU section catalog expanded to the common range (HEA/HEB/HEM 200–400, IPE160–600).
- Heavy sections (`t_f > 40 mm`): correct EN 1993-1-1 Table 3.1 `f_y` bands and Table 6.2 buckling
  curves.
- χ_LT is surfaced in the default report (the "if unrestrained" value is flagged on slab-restrained
  beams).
- Catalog/carbon CSVs live inside the package (`src/steelreuse/data/`) so an installed wheel finds them.
- Project version is now sourced dynamically from `steelreuse.__version__` (single source of truth).

### Notes
- ML modules remain an **exploratory side-study**, not wired into the certified path.
- The pyRevit extractor has not yet been validated against a real Revit model (tracked for a later
  release); columns in the bundled test extractions lack plan coordinates.

## [0.1.0]

Initial internal version: pyRevit/IFC extractors, EN 1993-1-1 member checks, PyNite force backend,
carbon passport, MILP matching, Jinja2 HTML report with a provider-agnostic LLM narrative, CLI, and a
Streamlit app.

[Unreleased]: https://github.com/cucuradu/circular-steel-reuse/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/cucuradu/circular-steel-reuse/releases/tag/v0.2.0
