"""Phase 3 tests: mass + embodied-carbon material passport."""

from pathlib import Path

import pytest

from steelreuse.core.carbon import (
    build_passport,
    load_factors,
    member_mass_kg,
    member_volume_m3,
)
from steelreuse.core.sections import load_catalog, resolve_members
from steelreuse.schema import ExtractedModel

DATA = Path(__file__).resolve().parents[1] / "src" / "steelreuse" / "data"


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_mass_and_volume_ipe300(cat):
    ipe300 = cat["IPE300"]
    assert member_mass_kg(ipe300, 6200) == pytest.approx(42.2 * 6.2)        # 261.64 kg
    assert member_volume_m3(ipe300, 6200) == pytest.approx(5380 / 1e6 * 6.2)  # 0.03336 m^3


def test_carbon_saved_ipe300(cat):
    f = load_factors()["steel"]
    mass = member_mass_kg(cat["IPE300"], 6200)
    assert mass * f.a1a3 == pytest.approx(261.64 * 1.55, rel=1e-3)
    assert mass * f.saved_per_kg == pytest.approx(261.64 * 1.45, rel=1e-3)


def test_end_of_life_credits_load_and_are_ordered():
    # A1(i): the factor table carries the end-of-life counterfactual credits as data. The shipped
    # values must respect the fate ordering the methodology rests on: full reuse saves more than
    # direct re-rolling (pilot-scale, avoids the melt) which saves more than EAF recycling.
    f = load_factors()["steel"]
    assert f.recycle_credit == pytest.approx(0.55)   # mid of the 0.4-0.7 literature range
    assert f.reroll_credit == pytest.approx(1.00)    # conservative, pilot-scale (Allwood-line)
    assert 0.0 < f.recycle_credit < f.reroll_credit < f.saved_per_kg


def test_old_factor_csv_without_credit_columns_still_loads(tmp_path):
    # Backward compatibility: a pre-A1 factors.csv (no credit columns) must load with credits 0.0,
    # and an empty cell must behave like a missing column.
    old = tmp_path / "factors_old.csv"
    old.write_text(
        "material,a1a3_kgco2e_per_kg,reuse_process_kgco2e_per_kg,source\n"
        'steel,1.55,0.10,"legacy file"\n',
        encoding="utf-8",
    )
    f = load_factors(old)["steel"]
    assert f.recycle_credit == 0.0 and f.reroll_credit == 0.0
    assert f.saved_per_kg == pytest.approx(1.45)

    empty = tmp_path / "factors_empty_cells.csv"
    empty.write_text(
        "material,a1a3_kgco2e_per_kg,reuse_process_kgco2e_per_kg,"
        "recycle_credit_kgco2e_per_kg,reroll_credit_kgco2e_per_kg,source\n"
        'steel,1.55,0.10,,,"empty cells"\n',
        encoding="utf-8",
    )
    f = load_factors(empty)["steel"]
    assert f.recycle_credit == 0.0 and f.reroll_credit == 0.0


def test_passport_skips_unknown_sections(cat):
    model = ExtractedModel.load(DATA / "samples" / "donor.json")
    resolve_members(model.members, cat)
    passport = build_passport(model.members, cat)
    # D8 (W12x40) is unknown -> excluded; the other 7 are present.
    assert len(passport.entries) == 7
    assert all(e.section != "W12x40" for e in passport.entries)
    assert passport.total_saved_kgco2e > 0
    assert passport.total_new_kgco2e > passport.total_saved_kgco2e  # reuse process > 0
