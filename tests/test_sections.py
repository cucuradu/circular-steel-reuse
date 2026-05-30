"""Phase 1 tests: section catalog loading, name normalization, mapping, and schema round-trip.

Standard library + the package only (no heavy deps), so these run on a bare Python install.
"""

from pathlib import Path

import pytest

from steelreuse.core.sections import (
    load_catalog,
    map_section,
    normalize_name,
    resolve_members,
)
from steelreuse.schema import ExtractedModel

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
    r = map_section("W12x40", catalog)
    assert r.method == "unknown" and r.canonical is None


def test_override_wins(catalog):
    r = map_section("MYSTEEL", catalog, overrides={"MYSTEEL": "IPE300"})
    assert r.method == "override" and r.canonical == "IPE300"


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


# --- schema round-trip -----------------------------------------------------

def test_schema_roundtrip(tmp_path):
    model = ExtractedModel.load(DATA / "samples" / "demand.json")
    assert model.kind == "demand"
    n1 = next(m for m in model.members if m.id == "N1")
    assert n1.spans_mm == [6000, 6000]  # continuous beam split preserved
    out = tmp_path / "rt.json"
    model.save(out)
    assert ExtractedModel.load(out).to_dict() == model.to_dict()
