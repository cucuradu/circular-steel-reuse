"""Phase 3 tests: mass + embodied-carbon material passport."""

from pathlib import Path

import pytest

from steelreuse.core.carbon import (
    CARBON_DATASETS,
    DEFAULT_CARBON_DATASET,
    build_passport,
    factors_path,
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


def test_selectable_carbon_datasets():
    # Sweep §4 carbon-factor dataset axis: each named set loads, the default stays ICE v3 (byte-
    # identical to the historical factors.csv), and the alternatives differ ONLY in the A1-A3
    # production figure (the number EPD databases disagree on) — the credits/process are held common.
    assert DEFAULT_CARBON_DATASET == "ice_v3"
    assert load_factors(dataset="ice_v3")["steel"] == load_factors()["steel"]

    a1a3 = {name: load_factors(dataset=name)["steel"].a1a3 for name in CARBON_DATASETS}
    assert a1a3 == {"ice_v3": pytest.approx(1.55), "ice_v4": pytest.approx(1.61),
                    "oekobaudat": pytest.approx(1.74)}
    for name in CARBON_DATASETS:
        f = load_factors(dataset=name)["steel"]
        assert (f.reuse_process, f.recycle_credit, f.reroll_credit) == (0.10, 0.55, 1.00)


def test_unknown_carbon_dataset_raises():
    with pytest.raises(ValueError, match="unknown carbon dataset"):
        load_factors(dataset="not_a_dataset")
    with pytest.raises(ValueError):
        factors_path("nope")


def test_welded_member_has_higher_reuse_process_carbon():
    from steelreuse.core.sections import load_default_catalog
    from steelreuse.schema import ExtractedMember
    cat = load_default_catalog()
    clean = ExtractedMember(id="1", section="IPE300", raw_section="IPE300", length_mm=6000.0,
                            connection_type="bolted")
    welded = ExtractedMember(id="2", section="IPE300", raw_section="IPE300", length_mm=6000.0,
                             connection_type="welded")
    p = build_passport([clean, welded], cat)
    e_clean = next(e for e in p.entries if e.id == "1")
    e_welded = next(e for e in p.entries if e.id == "2")
    assert e_welded.ec_reuse_kgco2e > e_clean.ec_reuse_kgco2e
    # net saving correspondingly lower for the welded member
    assert e_welded.ec_saved_kgco2e < e_clean.ec_saved_kgco2e


def test_passport_skips_unknown_sections(cat):
    model = ExtractedModel.load(DATA / "samples" / "donor.json")
    resolve_members(model.members, cat)
    passport = build_passport(model.members, cat)
    # D8 (W12x40) is unknown -> excluded; the other 7 are present.
    assert len(passport.entries) == 7
    assert all(e.section != "W12x40" for e in passport.entries)
    assert passport.total_saved_kgco2e > 0
    assert passport.total_new_kgco2e > passport.total_saved_kgco2e  # reuse process > 0
