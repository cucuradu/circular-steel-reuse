"""The extension-side IronPython view renders the review dict (loaded by path, like results_view)."""

import importlib.util
import os

_LIBDIR = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                       "SteelReuse.extension", "lib")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_LIBDIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ironpython_view_renders(monkeypatch):
    import sys
    sys.path.insert(0, _LIBDIR)            # so it can import steelreuse_results_view
    view = _load("steelreuse_review_view")
    review = {"coverage": {"total": 1, "unknown": 1, "fuzzy": 0, "audited": 0, "admitted": 1,
                           "quarantined": 0, "avg_knockdown": 1.0},
              "members": [{"id": "D1", "role": "beam", "raw_section": "X<y>", "section": None,
                           "mapping_method": "unknown", "condition": "", "verification": "",
                           "knockdown": 1.0, "defects": "", "admitted": True,
                           "issues": [["UNKNOWN_SECTION", "error"]]}]}
    html = view.render_problem_report(review)
    assert "D1" in html and "X&lt;y&gt;" in html
    assert "SteelReuse" in view.render_pda_report(review)
