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
