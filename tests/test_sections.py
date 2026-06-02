"""Phase 1 tests: section catalog loading, name normalization, mapping, and schema round-trip.

Standard library + the package only (no heavy deps), so these run on a bare Python install.
"""

from pathlib import Path

import pytest

from steelreuse.core.sections import (
    default_grade_for_section,
    load_catalog,
    load_catalog_imperial,
    load_default_catalog,
    map_section,
    normalize_name,
    resolve_members,
)
from steelreuse.schema import ExtractedMember, ExtractedModel

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# --- catalog ---------------------------------------------------------------

def test_catalog_loads_and_converts_units(catalog):
    ipe300 = catalog["IPE300"]
    assert ipe300.A == pytest.approx(5380.0)          # 53.8 cm^2 -> mm^2
    assert ipe300.Iy == pytest.approx(8.356e7)        # 8356 cm^4 -> mm^4
    assert ipe300.Wpl_y == pytest.approx(628_000.0)   # 628 cm^3 -> mm^3
    assert ipe300.iy == pytest.approx(125.0)          # 12.5 cm -> mm
    assert ipe300.Av_z > 0


# --- normalization ---------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("IPE 300", "IPE300"),
        ("IPE300", "IPE300"),
        ("IPE_400", "IPE400"),
        ("IPE300-S275", "IPE300"),
        ("HE 300 B", "HEB300"),
        ("HE300A", "HEA300"),
        ("HEA 240", "HEA240"),
        ("HE 220 A", "HEA220"),
        ("HEM300", "HEM300"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


# --- mapping ---------------------------------------------------------------

def test_map_exact(catalog):
    r = map_section("IPE300", catalog)
    assert r.method == "exact" and r.canonical == "IPE300" and r.confidence == 1.0


def test_map_normalized_variants(catalog):
    assert map_section("IPE 300", catalog).canonical == "IPE300"
    assert map_section("HE 300 B", catalog).canonical == "HEB300"
    assert map_section("HEA 240", catalog).canonical == "HEA240"


def test_map_unknown_us_section(catalog):
    # Against the *European* catalog a W-shape is still unknown (no AISC entries present).
    r = map_section("W12x40", catalog)
    assert r.method == "unknown" and r.canonical is None


def test_override_wins(catalog):
    r = map_section("MYSTEEL", catalog, overrides={"MYSTEEL": "IPE300"})
    assert r.method == "override" and r.canonical == "IPE300"


def test_fuzzy_matches_are_quarantined_by_default(catalog):
    # "IPE305" is a near-miss (~0.83 to IPE300/IPE330) -> a fuzzy hit, not exact/normalized/unknown.
    assert map_section("IPE305", catalog).method == "fuzzy"

    m = ExtractedMember(id="x", raw_section="IPE305")
    report = resolve_members([m], catalog)              # default: include_fuzzy=False
    assert len(report.fuzzy) == 1
    assert m.section is None        # quarantined: a guessed section never enters the analysis silently

    m2 = ExtractedMember(id="y", raw_section="IPE305")
    resolve_members([m2], catalog, include_fuzzy=True)  # opt in to the guess
    assert m2.section == map_section("IPE305", catalog).canonical


# --- end-to-end on the sample donor ---------------------------------------

def test_resolve_sample_donor(catalog):
    model = ExtractedModel.load(DATA / "samples" / "donor.json")
    report = resolve_members(model.members, catalog)
    # 7 of 8 map cleanly; the US section is the only unknown and is never guessed.
    assert len(report.unknown) == 1
    assert report.unknown[0].raw == "W12x40"
    by_id = {m.id: m for m in model.members}
    assert by_id["D8"].section is None
    assert by_id["D1"].section == "IPE300"
    assert by_id["D4"].section == "HEB300"
    assert report.n_total == 8


# --- US / AISC (imperial) --------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("W Shapes W18x55", "W18X55"),            # lowercase 'x' separator
        ("W Shapes W12X26", "W12X26"),            # uppercase 'X'
        ("W Shapes-Column W14x109", "W14X109"),   # family-name junk + size
        ("W18X55", "W18X55"),
        ("C Shapes C8X11.5", "C8X11.5"),          # channel, decimal weight
        ("HSS-Hollow Structural Section-Column HSS6x6x5/8", "HSS6X6X5/8"),  # tube, fraction
    ],
)
def test_normalize_name_us(raw, expected):
    assert normalize_name(raw) == expected


def test_us_does_not_break_eu_normalization():
    # the AISC detector must not fire on European names (no 'x'-joined size token).
    assert normalize_name("IPE 300") == "IPE300"
    assert normalize_name("HE 300 B") == "HEB300"


def test_load_catalog_imperial_converts_units():
    # W18X55 from AISC v15: A=16.2 in^2, Ix=890 in^4, w=55 lb/ft, d=18.1 in. Verify the in->mm/SI
    # conversion lands on the published soft-metric values (W460x82 ~ A=10500 mm^2, Ix=370e6 mm^4).
    us = load_catalog_imperial()
    w = us["W18X55"]
    assert w.A == pytest.approx(10451.6, rel=1e-4)        # 16.2 in^2 -> mm^2
    assert w.Iy == pytest.approx(3.7045e8, rel=1e-3)      # 890 in^4 -> mm^4 (AISC x -> EN y)
    assert w.mass_kgm == pytest.approx(81.85, rel=1e-3)   # 55 lb/ft -> kg/m
    assert w.h == pytest.approx(459.74, rel=1e-4)         # 18.1 in -> mm
    assert w.r > 0 and w.Av_z > 0                          # fillet recovered, shear area positive


def test_default_catalog_merges_eu_and_us():
    cat = load_default_catalog()
    assert "IPE300" in cat and "W18X55" in cat            # both standards in one catalog
    assert cat["W18X55"].mass_kgm == pytest.approx(81.85, rel=1e-3)


def test_map_us_section_against_default_catalog():
    cat = load_default_catalog()
    r = map_section("W Shapes W18x55", cat)
    assert r.canonical == "W18X55" and r.method == "normalized"


@pytest.mark.parametrize(
    "name,grade",
    [
        ("W18X55", "A992"),       # wide-flange -> A992
        ("HSS6X6X5/8", "A500"),   # tube -> A500
        ("C8X11.5", "A36"),       # channel -> A36
        ("IPE300", None),         # European -> untouched (keeps existing EN behaviour)
        ("HEB300", None),
    ],
)
def test_default_grade_for_section(name, grade):
    assert default_grade_for_section(name) == grade


# --- schema round-trip -----------------------------------------------------

def test_schema_roundtrip(tmp_path):
    model = ExtractedModel.load(DATA / "samples" / "demand.json")
    assert model.kind == "demand"
    n1 = next(m for m in model.members if m.id == "N1")
    assert n1.spans_mm == [6000, 6000]  # continuous beam split preserved
    out = tmp_path / "rt.json"
    model.save(out)
    assert ExtractedModel.load(out).to_dict() == model.to_dict()
