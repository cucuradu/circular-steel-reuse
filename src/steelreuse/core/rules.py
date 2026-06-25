"""Externalised, versioned rule tables (Roadmap §1.2) — the values a reviewer must trust or cite.

The judgement / standards-derived numbers a third party would want to *open and challenge* live in
versioned CSV files under ``steelreuse/data/rules/`` (plus the carbon factors under
``steelreuse/data/carbon/``), not buried in code:

  * **material grades** — nominal f_y by grade and EN element-thickness band (``material_grades.csv``);
  * **grade defaults** — the ASTM grade assumed for an ungraded AISC designation (``grade_defaults.csv``);
  * **condition knockdown** — surveyed A-D condition grade -> f_y knockdown (``condition_knockdown.csv``);
  * **verification knockdown** — grade-verification basis -> f_y knockdown (``verification_knockdown.csv``);
  * **carbon factors** — A1-A3 + reuse-process + end-of-life credits (``data/carbon/factors.csv``).

Each file carries a ``# version:`` header and ``# source:`` provenance comment lines, which this module
parses out so :func:`rules_manifest` can stamp *what rule data a run used* into the evidence package.
Internal solver tuning (off-cut weight, cut tolerance, over-spec ratio, the knockdown floor, fuzzy
cutoff, …) is deliberately NOT here — that is implementation, already logged via ``MatchResult.weights``,
not a citable rule.

Standard library only (imported on both sides of the Revit/IronPython boundary, like
:mod:`steelreuse.core.sections`).
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

# Coordinated version of the whole externalised rule set. Bump when ANY rule file changes; the
# per-file ``# version:`` headers should track it. Recorded in the evidence package so results name
# the rule data they used.
RULESET_VERSION = "1.0.0"

_DATA = Path(__file__).resolve().parent.parent / "data"
_RULES = _DATA / "rules"
_FACTORS = _DATA / "carbon" / "factors.csv"
_SECTIONS = _DATA / "sections"

MATERIAL_GRADES = _RULES / "material_grades.csv"
GRADE_DEFAULTS = _RULES / "grade_defaults.csv"
CONDITION_KNOCKDOWN = _RULES / "condition_knockdown.csv"
VERIFICATION_KNOCKDOWN = _RULES / "verification_knockdown.csv"

# Provenance for the bundled section catalogues (their property values are stored verbatim from these
# sources; see steelreuse.core.sections). Hashed into the manifest so a reviewer can confirm the table.
_CATALOG_PROVENANCE = {
    "eu_sections.csv": "European IPE/HE (EN 10365 / ArcelorMittal tables)",
    "uk_sections.csv": "UK UB/UC (BS EN 10365)",
    "us_sections.csv": "US AISC W-shapes (AISC Shapes Database v15.0)",
    "us_hss.csv": "US AISC rectangular/square HSS (AISC Shapes Database v15.0)",
    "us_round.csv": "US AISC round HSS + Pipe (AISC Shapes Database v15.0)",
    "eu_chs.csv": "EN 10210 hot-finished CHS",
    "channels.csv": "EU UPN channels",
    "angles.csv": "EN 10056 equal-leg angles",
}


def _sha256(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def read_rule_csv(path: str | Path) -> tuple[list[dict], str, str]:
    """Read a rule CSV, returning ``(rows, provenance, version)``.

    Lines whose first non-space character is ``#`` are provenance/version comments, not data: they are
    stripped before parsing and collected as the ``provenance`` text, with the ``# version:`` line
    surfaced separately. This lets every rule file cite itself in-place.
    """
    text = Path(path).read_text(encoding="utf-8")
    comment_lines: list[str] = []
    data_lines: list[str] = []
    version = ""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            body = line.lstrip().lstrip("#").strip()
            low = body.lower()
            if low.startswith("version:"):
                version = body.split(":", 1)[1].strip()
            elif low.startswith("source:"):
                comment_lines.append(body.split(":", 1)[1].strip())
            elif body:
                comment_lines.append(body)
        else:
            data_lines.append(line)
    rows = list(csv.DictReader(data_lines))
    return rows, " ".join(comment_lines), version


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "y")


# --------------------------------------------------------------------------------------------------
# Loaders (return the same Python structures the code used to hard-code, byte-for-byte)
# --------------------------------------------------------------------------------------------------

def load_material_grades() -> tuple[dict[str, float], dict[str, list[tuple[int, float]]]]:
    """``(FY_BY_GRADE, FY_BANDS)`` from ``material_grades.csv``.

    ``FY_BY_GRADE`` maps each grade to its base (first-row) f_y — the <=16 mm value for EU grades, the
    single specified minimum for ASTM. ``FY_BANDS`` keeps the ascending (thickness_upper, f_y) bands for
    EU grades only (ASTM rows carry a blank thickness and never enter the banding).
    """
    rows, _prov, _ver = read_rule_csv(MATERIAL_GRADES)
    fy_by_grade: dict[str, float] = {}
    bands: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        g = (r["grade"] or "").strip().upper()
        if not g:
            continue
        fy = float(r["fy_Nmm2"])
        fy_by_grade.setdefault(g, fy)  # first row for the grade is its headline value
        tu = (r["thickness_upper_mm"] or "").strip()
        if tu:
            bands.setdefault(g, []).append((int(float(tu)), fy))
    return fy_by_grade, bands


def load_grade_defaults() -> tuple[tuple[str, str], ...]:
    """The ``(prefix, grade)`` priority table from ``grade_defaults.csv`` (CSV row order = priority)."""
    rows, _prov, _ver = read_rule_csv(GRADE_DEFAULTS)
    return tuple((r["prefix"].strip().upper(), r["grade"].strip()) for r in rows if r.get("prefix"))


def load_condition_knockdown() -> tuple[dict[str, float], set[str]]:
    """``(CONDITION_KNOCKDOWN, REJECT_CONDITION)`` from ``condition_knockdown.csv``."""
    rows, _prov, _ver = read_rule_csv(CONDITION_KNOCKDOWN)
    factors: dict[str, float] = {}
    reject: set[str] = set()
    for r in rows:
        g = (r["condition_grade"] or "").strip().upper()
        if not g:
            continue
        factors[g] = float(r["knockdown"])
        if _truthy(r.get("reject")):
            reject.add(g)
    return factors, reject


def load_verification_knockdown() -> tuple[dict[str, float], set[str]]:
    """``(VERIFICATION_KNOCKDOWN, ACCEPTED_VERIFICATION)`` from ``verification_knockdown.csv``."""
    rows, _prov, _ver = read_rule_csv(VERIFICATION_KNOCKDOWN)
    factors: dict[str, float] = {}
    accepted: set[str] = set()
    for r in rows:
        v = (r["verification_status"] or "").strip().lower()
        if not v:
            continue
        factors[v] = float(r["knockdown"])
        if _truthy(r.get("accepted")):
            accepted.add(v)
    return factors, accepted


def _carbon_factor_provenance(path: str | Path | None = None) -> tuple[str, str]:
    """``(provenance, version)`` for the carbon-factor table (header comments + steel-row source)."""
    rows, prov, ver = read_rule_csv(path or _FACTORS)
    steel = next((r for r in rows if (r.get("material") or "").strip().lower() == "steel"), None)
    if steel and steel.get("source"):
        prov = (prov + " " + steel["source"]).strip()
    return prov, ver


# --------------------------------------------------------------------------------------------------
# Manifest (stamped into the evidence package)
# --------------------------------------------------------------------------------------------------

def _table_entry(name: str, path: Path) -> dict:
    rows, prov, ver = read_rule_csv(path)
    return {
        "name": name,
        "file": path.name,
        "version": ver or RULESET_VERSION,
        "source": prov,
        "sha256": _sha256(path),
        "n_rows": len(rows),
    }


def rules_manifest(carbon_factors_path: str | Path | None = None) -> dict:
    """Versions, sources and content hashes of every externalised rule table, for the evidence package.

    A reviewer reads this block to know *which* rule data produced the run — the ruleset version, each
    table's citation, and a SHA-256 they can recompute against the shipped file. ``carbon_factors_path``
    names the carbon dataset the run actually used (defaults to the bundled ICE v3 set).
    """
    cf_path = Path(carbon_factors_path) if carbon_factors_path else _FACTORS
    cf_prov, cf_ver = _carbon_factor_provenance(cf_path)
    return {
        "ruleset_version": RULESET_VERSION,
        "tables": [
            _table_entry("material_grades", MATERIAL_GRADES),
            _table_entry("grade_defaults", GRADE_DEFAULTS),
            _table_entry("condition_knockdown", CONDITION_KNOCKDOWN),
            _table_entry("verification_knockdown", VERIFICATION_KNOCKDOWN),
        ],
        "carbon_factors": {
            "file": cf_path.name,
            "version": cf_ver or RULESET_VERSION,
            "source": cf_prov,
            "sha256": _sha256(cf_path),
        },
        "section_catalog": {
            "version": RULESET_VERSION,
            "files": [
                {"name": name, "source": src, "sha256": _sha256(_SECTIONS / name)}
                for name, src in _CATALOG_PROVENANCE.items()
                if (_SECTIONS / name).exists()
            ],
        },
    }
