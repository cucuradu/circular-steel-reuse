"""The shared file picker in the extension's buttons lib (lib/steelreuse_buttons.py).

Regression cover for the Run Match crash: selecting the demand model fired a multi-select WinForms
dialog with a filter pyRevit built as "|*.json|csv|xlsx" -- a MALFORMED spec the Vista COM dialog
rejected with an unhandled COMException, which took Revit down with a fatal error. The fix builds a
well-formed ``files_filter`` and guards the call so a picker error can never escape the click handler.

``steelreuse_buttons`` imports ``pyrevit.forms`` at module top, so we stub a fake ``pyrevit`` package
into ``sys.modules`` before loading it (the module itself is otherwise stdlib-only / IronPython-safe).
"""

import importlib.util
import os
import sys
import types


def _load_buttons(fake_forms):
    """Load steelreuse_buttons.py with ``pyrevit.forms`` stubbed by ``fake_forms``."""
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.forms = fake_forms
    forms_mod = types.ModuleType("pyrevit.forms")
    forms_mod.pick_file = fake_forms.pick_file
    forms_mod.alert = fake_forms.alert
    # steelreuse_buttons also imports steelreuse_runner (stdlib-only) -- let the real one load.
    sys.modules["pyrevit"] = pyrevit
    sys.modules["pyrevit.forms"] = forms_mod
    lib = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                       "SteelReuse.extension", "lib")
    sys.path.insert(0, lib)  # so its `import steelreuse_runner` resolves
    try:
        spec = importlib.util.spec_from_file_location(
            "steelreuse_buttons", os.path.join(lib, "steelreuse_buttons.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(lib)


class _FakeForms:
    """Records the kwargs pick_file is called with; pick_file/alert configurable per test."""

    def __init__(self, pick_result=None, pick_raises=None):
        self.pick_kwargs = None
        self.alerts = []
        self._pick_result = pick_result
        self._pick_raises = pick_raises

    def pick_file(self, **kwargs):
        self.pick_kwargs = kwargs
        if self._pick_raises is not None:
            raise self._pick_raises
        return self._pick_result

    def alert(self, *args, **kwargs):
        self.alerts.append((args, kwargs))


def test_ext_filter_is_a_well_formed_even_paired_filter():
    buttons = _load_buttons(_FakeForms())
    flt = buttons._ext_filter(("json", "csv", "xlsx"))
    # WinForms requires Description|Pattern pairs -> an even number of '|'-separated parts.
    parts = flt.split("|")
    assert len(parts) % 2 == 0
    # Every chosen extension must appear in the (single) pattern that selects them all.
    assert "*.json;*.csv;*.xlsx" in flt
    # The malformed pyRevit-built form must NOT be what we produce.
    assert flt != "|*.json|csv|xlsx"


def test_pick_model_file_passes_files_filter_not_file_ext():
    fake = _FakeForms(pick_result="C:/m.json")
    buttons = _load_buttons(fake)
    out = buttons.pick_model_file("Donor", multi_file=True)
    assert out == "C:/m.json"
    # The crux of the fix: we must hand pyRevit a ready-made files_filter, never file_ext (which it
    # would re-wrap into the malformed "|*.json|csv|xlsx" that crashed the multi-select dialog).
    assert "file_ext" not in fake.pick_kwargs
    assert fake.pick_kwargs["multi_file"] is True
    assert "*.json;*.csv;*.xlsx" in fake.pick_kwargs["files_filter"]


def test_pick_model_file_swallows_picker_errors_instead_of_crashing_revit():
    # A raising pick_file simulates the COMException that, unhandled, was fatal to Revit.
    fake = _FakeForms(pick_raises=Exception("0xe0434352"))
    buttons = _load_buttons(fake)
    out = buttons.pick_model_file("Demand", multi_file=True)
    assert out is None              # handler returns cleanly...
    assert len(fake.alerts) == 1    # ...after telling the user, so Revit survives.
