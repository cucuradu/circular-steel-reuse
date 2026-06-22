# tests/test_pda_params.py
"""Tests for the PDA shared-parameter map + value coercion (loaded by path, IronPython-safe)."""

import importlib.util
import os

_LIB = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                    "SteelReuse.extension", "lib", "steelreuse_pda_params.py")
_spec = importlib.util.spec_from_file_location("steelreuse_pda_params", _LIB)
pda = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pda)


def test_param_names_map_to_member_fields():
    names = [p[0] for p in pda.PDA_SHARED_PARAMS]
    assert "Reuse Condition Grade" in names
    # All params must map to the exact _AUDIT_FIELDS names so values round-trip into the engine.
    assert pda.FIELD_BY_PARAM == {
        "Reuse Condition Grade": "condition_grade",
        "Reuse Verification": "verification_status",
        "Reuse Knockdown": "knockdown",
        "Reuse Recoverable Length (mm)": "recoverable_length_mm",
        "Reuse Defects": "defects",
        "Reuse Connection Type": "connection_type",
        "Reuse Connection Condition": "connection_condition",
        "Reuse Deconstructability": "deconstructability",
    }


def test_coerce_number_fields():
    assert pda.coerce_field("knockdown", "0.9") == 0.9
    assert pda.coerce_field("recoverable_length_mm", "6000") == 6000.0
    assert pda.coerce_field("recoverable_length_mm", "") is None      # blank number -> unset
    assert pda.coerce_field("condition_grade", " b ") == "B"          # text upper, stripped
    assert pda.coerce_field("verification_status", "Mill_Cert") == "mill_cert"  # lower
    assert pda.coerce_field("defects", "  rust  ") == "rust"          # plain text stripped, as-is
    assert pda.coerce_field("knockdown", "") is None                  # blank -> unset
    assert pda.coerce_field("knockdown", "bad") is None               # unparseable -> unset


def test_connection_params_present_and_mapped():
    names = [p[0] for p in pda.PDA_SHARED_PARAMS]
    assert "Reuse Connection Type" in names
    assert pda.FIELD_BY_PARAM["Reuse Connection Type"] == "connection_type"
    assert pda.FIELD_BY_PARAM["Reuse Connection Condition"] == "connection_condition"
    assert pda.FIELD_BY_PARAM["Reuse Deconstructability"] == "deconstructability"


def test_coerce_connection_text():
    assert pda.coerce_field("connection_type", "Welded") == "welded"   # lowercased like verification
