"""The shared file picker in the extension's buttons lib (lib/steelreuse_buttons.py).

Regression cover for the Run Match crash: selecting the demand model fired a multi-select WinForms
``OpenFileDialog`` from the modeless WPF window, which under Revit 2026 threw a managed exception
*inside its own modal pump* (0xe0434352) -- a fatal Revit crash no try/except could catch. The fix
swaps to the WPF ``Microsoft.Win32.OpenFileDialog`` and keeps a guard as belt-and-braces.

Only the pure logic is unit-tested here: the filter string, and that the picker NEVER lets an error
escape (it returns None + alerts). The actual .NET dialog is Revit-side and verified manually there.
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
    # Common-file dialogs require Description|Pattern pairs -> an even number of '|'-separated parts.
    parts = flt.split("|")
    assert len(parts) % 2 == 0
    # Every chosen extension must appear in the (single) pattern that selects them all.
    assert "*.json;*.csv;*.xlsx" in flt
    # The malformed pyRevit-built form must NOT be what we produce.
    assert flt != "|*.json|csv|xlsx"


def test_pick_model_file_never_lets_an_error_escape_to_crash_revit():
    # Under CPython the .NET dialog (clr / Microsoft.Win32) can't be imported, so the body raises --
    # standing in for any picker/backend error. The guard must swallow it: return None and alert,
    # never propagate (an escaped exception in the WPF click handler is a fatal Revit crash).
    fake = _FakeForms()
    buttons = _load_buttons(fake)
    out = buttons.pick_model_file("Demand", multi_file=True)
    assert out is None
    assert len(fake.alerts) == 1
