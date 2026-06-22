# .../lib/steelreuse_pda_params.py
# -*- coding: utf-8 -*-
"""PDA shared-parameter definitions + value coercion, shared by Set Audit and the Extract read-back.

Stdlib only, no f-strings (IronPython 3 under Revit), and unit-tested in CPython by importlib path.
The parameter names live under the same "SteelReuse" shared-param group as the reuse passport, so
they are schedulable alongside it. Coercion mirrors steelreuse.core.audit normalisation (condition
upper, verification lower).
"""

# (parameter display name, kind) -- kind is "text" or "number" (matches steelreuse_apply._spec).
PDA_SHARED_PARAMS = (
    ("Reuse Condition Grade", "text"),
    ("Reuse Verification", "text"),
    ("Reuse Knockdown", "number"),
    ("Reuse Recoverable Length (mm)", "number"),
    ("Reuse Defects", "text"),
    ("Reuse Connection Type", "text"),
    ("Reuse Connection Condition", "text"),
    ("Reuse Deconstructability", "text"),
)

# Revit param name -> ExtractedMember field name.
FIELD_BY_PARAM = {
    "Reuse Condition Grade": "condition_grade",
    "Reuse Verification": "verification_status",
    "Reuse Knockdown": "knockdown",
    "Reuse Recoverable Length (mm)": "recoverable_length_mm",
    "Reuse Defects": "defects",
    "Reuse Connection Type": "connection_type",
    "Reuse Connection Condition": "connection_condition",
    "Reuse Deconstructability": "deconstructability",
}

_NUMBER_FIELDS = ("knockdown", "recoverable_length_mm")


def coerce_field(field, value):
    """Coerce a raw string for ``field`` to its stored form, or None when blank/unparseable.

    Number fields -> float (None if not parseable); condition -> UPPER; verification -> lower;
    other text -> stripped as-is.
    """
    text = (value or "").strip()
    if not text:
        return None
    if field in _NUMBER_FIELDS:
        try:
            return float(text)
        except ValueError:
            return None
    if field in ("condition_grade", "connection_condition"):
        return text.upper()
    if field in ("verification_status", "connection_type", "deconstructability"):
        return text.lower()
    return text
