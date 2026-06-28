"""Unit tests for the IronPython extractor's ``_is_non_steel`` (extractor/pyrevit_extract.py).

The extractor runs inside Revit on IronPython and imports ``pyrevit`` at module top, so it cannot be
imported on a bare CPython test runner directly. We install a minimal ``pyrevit`` stub (just the surface
the module touches at import time + the ``DB.StructuralAssetClass`` enum the function reads), load the
module by path, then drive ``_is_non_steel`` with ``_structural_material`` / ``_type_name`` monkeypatched.

Regression target: a concrete/timber member with NO structural material assigned (mat is None) must be
dropped via the family/type NAME fallback. The old code returned False before reaching that fallback.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

EXTRACTOR = Path(__file__).resolve().parents[1] / "extractor" / "pyrevit_extract.py"


def _load_extractor():
    pyrevit = types.ModuleType("pyrevit")

    class _Bip:
        # BuiltInParameter.<ANY> -> a unique-ish sentinel; the function only passes these around.
        def __getattr__(self, name):
            return ("BIP", name)

    class _SAC:
        Metal = "Metal"
        Concrete = "Concrete"
        Wood = "Wood"

    class _Doc:
        def GetElement(self, *_a):
            return None

    pyrevit.DB = types.SimpleNamespace(BuiltInParameter=_Bip(), StructuralAssetClass=_SAC)
    pyrevit.forms = types.SimpleNamespace()
    pyrevit.revit = types.SimpleNamespace(doc=_Doc())
    pyrevit.script = types.SimpleNamespace(get_output=lambda: types.SimpleNamespace())
    sys.modules["pyrevit"] = pyrevit

    spec = importlib.util.spec_from_file_location("pyrevit_extract", EXTRACTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ex():
    return _load_extractor()


def test_unassigned_material_concrete_name_is_dropped(ex, monkeypatch):
    # mat is None + a non-steel NAME word -> the fixed fallback fires and drops it (the bug case).
    monkeypatch.setattr(ex, "_structural_material", lambda _e: None)
    monkeypatch.setattr(ex, "_type_name", lambda _e: "Concrete-Rectangular Beam CB24x24")
    assert ex._is_non_steel(object()) is True


def test_unassigned_material_steel_name_is_kept(ex, monkeypatch):
    # mat is None + a steel name -> kept; the fallback is strictly one-sided.
    monkeypatch.setattr(ex, "_structural_material", lambda _e: None)
    monkeypatch.setattr(ex, "_type_name", lambda _e: "W Shapes W18x55")
    assert ex._is_non_steel(object()) is False


def test_unassigned_material_empty_name_is_kept(ex, monkeypatch):
    monkeypatch.setattr(ex, "_structural_material", lambda _e: None)
    monkeypatch.setattr(ex, "_type_name", lambda _e: "")
    assert ex._is_non_steel(object()) is False


def test_assigned_concrete_asset_class_is_dropped(ex, monkeypatch):
    # Material present with a Concrete structural asset class -> dropped via the (unchanged) enum path,
    # before any name check. Confirms the fix did not disturb the material-based branches.
    class FakeAsset:
        StructuralAssetClass = ex.DB.StructuralAssetClass.Concrete

    class FakePropertySet:
        def GetStructuralAsset(self):
            return FakeAsset()

    class FakeMaterial:
        StructuralAssetId = ("asset-id",)
        MaterialClass = "Concrete"
        Name = "Concrete, Cast-in-Place gray"

    monkeypatch.setattr(ex, "_structural_material", lambda _e: FakeMaterial())
    monkeypatch.setattr(ex, "_type_name", lambda _e: "should-not-matter")
    monkeypatch.setattr(ex.doc, "GetElement", lambda *_a: FakePropertySet())
    assert ex._is_non_steel(object()) is True


def test_assigned_metal_asset_class_is_kept(ex, monkeypatch):
    # Material present with a Metal asset class -> kept, even though a later name check exists.
    class FakeAsset:
        StructuralAssetClass = ex.DB.StructuralAssetClass.Metal

    class FakePropertySet:
        def GetStructuralAsset(self):
            return FakeAsset()

    class FakeMaterial:
        StructuralAssetId = ("asset-id",)
        MaterialClass = "Metal"
        Name = "Steel, 45-345"

    monkeypatch.setattr(ex, "_structural_material", lambda _e: FakeMaterial())
    monkeypatch.setattr(ex, "_type_name", lambda _e: "Concrete-sounding but asset says Metal")
    monkeypatch.setattr(ex.doc, "GetElement", lambda *_a: FakePropertySet())
    assert ex._is_non_steel(object()) is False
