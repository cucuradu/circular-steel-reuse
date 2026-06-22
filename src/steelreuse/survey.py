"""Survey authoring: export a context-rich PDA template, and import filled surveys from many tools
and formats (CSV / Excel / JSON) onto donor members.

Pure (stdlib only, except the optional openpyxl path for .xlsx), so it is fully unit-tested and runs
in the headless engine the pyRevit Import-Survey button shells out to. Reuses the audit field set and
value coercion; the importer is deliberately format- and tool-flexible (header aliases + value
normalisation + multi-key matching) rather than one adapter per tool.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

# Context columns (prefilled from the model) then audit columns (blank, for the surveyor to fill).
_CONTEXT_COLUMNS = ["unique_id", "mark", "id", "level", "role", "raw_section",
                    "length_mm", "recoverable_length_mm", "start_xyz", "end_xyz"]
_AUDIT_COLUMNS = ["condition_grade", "verification_status", "knockdown", "defects",
                  "connection_type", "connection_condition", "deconstructability"]
SURVEY_COLUMNS = _CONTEXT_COLUMNS + _AUDIT_COLUMNS


def survey_template_rows(model) -> list[dict]:
    """One row per donor member: context prefilled, audit columns blank."""
    rows = []
    for m in model.members:
        rl = getattr(m, "recoverable_length_mm", None)
        rows.append({
            "unique_id": m.unique_id or "",
            "mark": m.mark or "",
            "id": m.id,
            "level": m.level or "",
            "role": m.role,
            "raw_section": m.raw_section,
            "length_mm": m.length_mm,
            "recoverable_length_mm": rl if rl is not None else m.length_mm,
            "start_xyz": ";".join(str(x) for x in (m.start_xyz or [])),
            "end_xyz": ";".join(str(x) for x in (m.end_xyz or [])),
            **{c: "" for c in _AUDIT_COLUMNS},
        })
    return rows


def survey_template_csv(model) -> str:
    """Serialise the template rows to CSV with the fixed SURVEY_COLUMNS order."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=SURVEY_COLUMNS)
    w.writeheader()
    for row in survey_template_rows(model):
        w.writerow(row)
    return buf.getvalue()


_NUMBER_FIELDS = ("knockdown", "recoverable_length_mm")

_VERIFICATION_SYNONYMS = {
    "mill_cert": ("mill cert", "mill certificate", "cert", "certificate", "mtc"),
    "coupon_tested": ("coupon", "coupon test", "coupon tested", "tested", "tensile test"),
    "documented": ("documented", "drawings", "records", "as-built", "design records"),
    "visual_only": ("visual", "visual only", "assumed", "estimated", "era"),
    "unverified": ("unverified", "none", "unknown", "n/a", ""),
}
_CONDITION_SYNONYMS = {
    "A": ("a", "good", "as-new", "new", "excellent", "sound"),
    "B": ("b", "minor", "light", "light corrosion", "cosmetic"),
    "C": ("c", "significant", "section loss", "moderate", "deformed"),
    "D": ("d", "poor", "unsuitable", "bad", "severe", "heavy corrosion"),
}
_CONNECTION_SYNONYMS = {
    "bolted": ("bolted", "pinned", "simple", "shear", "bolt"),
    "welded": ("welded", "weld", "moment", "fixed"),
    "riveted": ("riveted", "rivet"),
}


def _match_synonym(text, table, default=None):
    low = text.strip().lower()
    for canonical, words in table.items():
        if low == canonical.lower() or low in words:
            return canonical
    return default


def normalize_survey_value(field, raw):
    """Coerce a raw survey cell to the stored form, mapping common synonyms; None when unset.

    Numbers -> float (None if unparseable). condition/verification/connection map through synonym
    tables, falling back to the canonicalised text (UPPER for condition, lower otherwise) so an
    unrecognised but plausible value is preserved for the audit layer rather than dropped.
    """
    text = (raw or "").strip()
    if field in _NUMBER_FIELDS:
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if field == "verification_status":
        return _match_synonym(text, _VERIFICATION_SYNONYMS, default=(text.lower() or "unverified"))
    if field == "condition_grade":
        if not text:
            return None
        return _match_synonym(text, _CONDITION_SYNONYMS, default=text.upper())
    if field == "connection_type":
        if not text:
            return None
        return _match_synonym(text, _CONNECTION_SYNONYMS, default="unknown")
    return text or None
