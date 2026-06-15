# Contributing

Guidance for anyone — human or automated — working on this repository. The overriding rule is simple:
**the repository contains only what belongs in the public software product.** Personal, machine‑specific,
generated, or process artifacts stay out of version control.

## Development setup

```powershell
uv venv && uv pip install -e ".[analysis,fea,ml,opt,report,llm,ui,bim,dev]"
uv run pytest          # full test suite (Phase 1 tests need only the standard library)
uv run ruff check .    # lint
```

If `uv` is blocked on a locked‑down Windows machine, see [docs/UNBLOCK_UV.md](docs/UNBLOCK_UV.md); plain
`pip` works too.

## Repository scope — what to commit

Commit only material that is part of the shipped tool and useful to an external user or reviewer:

- **Source** under `src/steelreuse/` and the pyRevit add‑on under `pyrevit_extension/` and `extractor/`.
- **Tests** under `tests/`.
- **Documentation** under `docs/` and the root `README.md`, `CHANGELOG.md`, `LICENSE`.
- **Reference data** that ships with the tool: section catalogues, carbon factors, the small bundled
  sample models, and the canonical case‑study models in `data/case_study/`.
- **Project configuration**: `pyproject.toml`, `uv.lock`, `.gitignore`, CI workflows under `.github/`.

## What must NOT be committed

These are enforced by `.gitignore`; do not force‑add them:

- **Secrets** — `.env`, API keys, `*.key`.
- **Generated output** — `reports/`, `models/`, `data/generated/`, per‑run benchmark output
  directories (`docs/benchmark/<run>/`), rendered HTML/PDF docs, logs, `build/`, `dist/`.
- **Local academic / working copies** — `THESIS_PRO.*`. The public, de‑academic version of that
  document is [`docs/OVERVIEW.md`](docs/OVERVIEW.md); the thesis working copy stays local.
- **Machine‑specific configuration** — `steelreuse_runner_config.json`, signed‑venv paths, anything
  naming a specific user profile (`C:\Users\<name>\…`). Use generic placeholders (`<repo>`, `python`)
  in documentation.
- **Local tooling / agent state** — `.claude/`, editor folders, dev‑automation workers, scratch files.

If a document or comment would only make sense on one person's machine, generalize it before committing.

## Conventions

- **Style**: ruff, line length 110. The `extractor/` and `pyrevit_extension/` code must stay
  **IronPython‑safe** (standard library only, no f‑strings, no `%`‑formatting), because it runs inside
  Revit; heavy libraries (numpy/sklearn/ortools/SAP COM) must never be imported on the Revit side.
- **Units**: normalize to **N, mm** at the `core/sections.py` boundary (so MPa = N/mm²). See the
  [units convention](docs/DESIGN_PRINCIPLES.md#units-convention).
- **Engineering rules**: the five non‑negotiable [design principles](docs/DESIGN_PRINCIPLES.md) — the
  deterministic EN 1993‑1‑1 check is the source of truth, the language model never does arithmetic,
  conservative‑by‑default, never silently guess section identity. Preserve them in any change.

## Commits and pull requests

- Keep commits **atomic and well‑scoped**; do not bundle an unrelated feature into a cleanup commit.
- Write plain, professional commit messages. **Do not add AI‑attribution trailers or tool‑generated
  signatures** (e.g. `Co‑Authored‑By:` / "Generated with …" footers) — the history reads as
  engineering work, not tooling output.
- Run `uv run pytest` and `uv run ruff check .` before opening a pull request, and state the result.
- The default branch is `main`; develop on a feature branch and open a PR.
