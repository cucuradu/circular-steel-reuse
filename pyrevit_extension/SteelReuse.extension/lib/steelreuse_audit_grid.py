# -*- coding: utf-8 -*-
"""Pure model for the in-Revit audit grid: build editable rows from a review dict, track edits, and
produce the {element_id: {field: value}} payload for steelreuse_apply.write_pda.

Stdlib only, no f-strings (IronPython 3), unit-tested in CPython by importlib path. Value coercion
reuses steelreuse.survey-style synonyms via a local copy kept tiny (the engine's survey.py is not
importable under IronPython). Editable fields are the audit + connection set.
"""

EDITABLE_FIELDS = ("condition_grade", "verification_status", "knockdown",
                   "recoverable_length_mm", "defects",
                   "connection_type", "connection_condition", "deconstructability")

# Minimal synonym normalisation mirroring steelreuse.survey (kept local: survey.py is CPython-only).
_COND = {"A": ("a", "good", "as-new", "new", "sound"), "B": ("b", "minor", "light"),
         "C": ("c", "significant", "section loss"), "D": ("d", "poor", "unsuitable", "bad")}
_VERIF = {"mill_cert": ("mill cert", "mill certificate", "cert"), "coupon_tested": ("coupon", "tested"),
          "documented": ("documented", "drawings", "records"), "visual_only": ("visual", "assumed"),
          "unverified": ("unverified", "none", "unknown")}
_CONN = {"bolted": ("bolted", "pinned", "simple"), "welded": ("welded", "moment"),
         "riveted": ("riveted",)}


def _syn(text, table, default):
    low = (text or "").strip().lower()
    for canon, words in table.items():
        if low == canon.lower() or low in words:
            return canon
    return default


def _coerce(field, raw):
    text = (raw or "")
    text = text.strip() if isinstance(text, str) else text
    if field in ("knockdown", "recoverable_length_mm"):
        if text in ("", None):
            return None
        try:
            return float(text)
        except (ValueError, TypeError):
            return None
    if field == "condition_grade":
        return _syn(text, _COND, (text or "").upper() or None)
    if field == "verification_status":
        return _syn(text, _VERIF, (text or "").lower() or None)
    if field == "connection_type":
        return _syn(text, _CONN, "unknown") if text else None
    return text or None


def build_rows(review):
    """One editable row dict per member, seeded from the review dict's current values."""
    rows = []
    for m in review.get("members", []):
        row = {"id": m.get("id"), "mark": m.get("mark", ""), "section": m.get("section") or "",
               "role": m.get("role", ""), "_dirty": False, "_changed": set()}
        row["condition_grade"] = m.get("condition", "") or ""
        row["verification_status"] = m.get("verification", "") or ""
        row["knockdown"] = m.get("knockdown")
        row["recoverable_length_mm"] = m.get("recoverable_length_mm")
        row["defects"] = m.get("defects", "") or ""
        row["connection_type"] = m.get("connection_type", "") or ""
        row["connection_condition"] = m.get("connection_condition", "") or ""
        row["deconstructability"] = m.get("deconstructability", "") or ""
        rows.append(row)
    return rows


def set_value(row, field, raw):
    """Set one field (coerced), marking the row + field dirty."""
    if field not in EDITABLE_FIELDS:
        return
    row[field] = _coerce(field, raw)
    row["_dirty"] = True
    row["_changed"].add(field)


def bulk_set(rows, field, raw):
    """Apply one value to many rows (a selection)."""
    for row in rows:
        set_value(row, field, raw)


def write_payload(rows):
    """{element_id: {field: value}} for dirty rows, only their changed non-None fields."""
    out = {}
    for row in rows:
        if not row.get("_dirty"):
            continue
        vals = {}
        for field in row["_changed"]:
            val = row.get(field)
            if val is not None and val != "":
                vals[field] = val
        if vals:
            out[str(row["id"])] = vals
    return out
