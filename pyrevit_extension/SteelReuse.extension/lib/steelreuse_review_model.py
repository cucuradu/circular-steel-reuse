# -*- coding: utf-8 -*-
"""Headless view-model for the SteelReuse donor-Review window: flatten review.json into grid rows.

No Revit, no WPF -> unit-testable under CPython exactly as it runs under IronPython in Revit (the same
split as steelreuse_panel_model). The window (steelreuse_review_window.py) binds these plain objects
to its two DataGrids -- a Problems tab (members needing attention) and a PDA QA tab (the audit) -- and
the row's ``id`` drives Zoom-to-element. IronPython-safe: stdlib only, no f-strings.
"""


def _txt(value, dash="-"):
    """A display string for a possibly-missing field (em-dash-free; '-' so it copies cleanly)."""
    if value is None or value == "":
        return dash
    return str(value)


class ProblemRow:
    """One donor member that needs attention (unknown/fuzzy section, missing grade, no coords...)."""

    __slots__ = ("id", "role", "raw_section", "mapped", "method", "severity", "issues")

    def __init__(self, m):
        self.id = _txt(m.get("id"), "")
        self.role = _txt(m.get("role"), "")
        self.raw_section = _txt(m.get("raw_section"), "")
        self.mapped = _txt(m.get("section"))
        self.method = _txt(m.get("mapping_method"), "")
        self.severity = _txt(m.get("worst_severity"), "")
        # "UNKNOWN_SECTION, MISSING_GRADE" -- the issue codes, severity dropped (shown in its column).
        self.issues = ", ".join(str(code) for code, _sev in m.get("issues", []))


class PdaRow:
    """One donor member's pre-demolition audit line (condition / verification / knockdown / admitted)."""

    __slots__ = ("id", "role", "condition", "verification", "knockdown", "admitted", "defects")

    def __init__(self, m):
        self.id = _txt(m.get("id"), "")
        self.role = _txt(m.get("role"), "")
        self.condition = _txt(m.get("condition"))
        self.verification = _txt(m.get("verification"))
        kd = m.get("knockdown")
        self.knockdown = "%.3f" % kd if isinstance(kd, (int, float)) else "-"
        self.admitted = "yes" if m.get("admitted") else "no"
        self.defects = _txt(m.get("defects"), "")


def problem_rows(review):
    """The members with at least one issue, as :class:`ProblemRow`s (the Problems tab)."""
    return [ProblemRow(m) for m in review.get("members", []) if m.get("issues")]


def pda_rows(review):
    """Every member as a :class:`PdaRow` (the PDA QA tab -- audit covers all, not just problems)."""
    return [PdaRow(m) for m in review.get("members", [])]


def problem_summary(review):
    """A one-line coverage headline for the Problems tab."""
    cov = review.get("coverage", {})
    n = len(problem_rows(review))
    return ("%s / %s members need attention   |   %s unknown   |   %s fuzzy   |   %s mapped"
            % (n, cov.get("total", "?"), cov.get("unknown", "?"),
               cov.get("fuzzy", "?"), cov.get("mapped", "?")))


def pda_summary(review):
    """A one-line coverage headline for the PDA QA tab."""
    cov = review.get("coverage", {})
    avg = cov.get("avg_knockdown", 1.0)
    avg_str = "%.3f" % avg if isinstance(avg, (int, float)) else "?"
    return ("%s / %s audited   |   %s admitted   |   %s quarantined   |   avg knockdown %s"
            % (cov.get("audited", "?"), cov.get("total", "?"), cov.get("admitted", "?"),
               cov.get("quarantined", "?"), avg_str))
