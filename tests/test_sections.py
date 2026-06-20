"""Phase 1 tests: section catalog loading, name normalization, mapping, and schema round-trip.

Standard library + the package only (no heavy deps), so these run on a bare Python install.
"""

import math
from pathlib import Path

import pytest

from steelreuse.core.sections import (
    SectionProps,
    default_grade_for_section,
    load_catalog,
    load_catalog_imperial,
    load_catalog_round,
    load_default_catalog,
    map_section,
    normalize_name,
    resolve_members,
)
from steelreuse.schema import ExtractedMember, ExtractedModel

DATA = Path(__file__).resolve().parents[1] / "src" / "steelreuse" / "data"


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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("UC254x254x73", "UC254X254X73"),         # already canonical
        ("UKC 305x305x97", "UC305X305X97"),       # UKC prefix -> UC
        ("305x305x137 UC", "UC305X305X137"),      # trailing designation
        ("UB 457x191x74", "UB457X191X74"),        # spaced prefix
        ("UKB 457x152x52", "UB457X152X52"),       # UKB prefix -> UB
        ("UC 356x406x1299", "UC356X406X1299"),    # leading-zero-free, large mass
    ],
)
def test_normalize_name_uk(raw, expected):
    assert normalize_name(raw) == expected


def test_uk_does_not_hijack_hss_or_eu():
    # "TUBE" contains "UB" but not at a word boundary; HSS triples have no U[BC] token.
    assert normalize_name("HSS6x6x5/8") == "HSS6X6X5/8"
    assert normalize_name("HEB300") == "HEB300"


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


def test_geometry_confirms_fuzzy_match(catalog):
    # "IPE305" is fuzzy by name, but the measured dimensions pin it to exactly one catalog row.
    m = ExtractedMember(id="x", raw_section="IPE305", h_mm=300.0, b_mm=150.0)
    report = resolve_members([m], catalog)
    assert m.section == "IPE300"
    assert len(report.fuzzy) == 0
    assert report.mapped[0].method == "geometry" and report.mapped[0].confidence == 1.0


def test_geometry_beats_the_fuzzy_name_candidate(catalog):
    # The name suggests IPE330, but the physical dimensions are IPE360's -> dimensions win.
    assert map_section("IPE335", catalog).canonical == "IPE330"
    m = ExtractedMember(id="x", raw_section="IPE335",
                        h_mm=360.0, b_mm=170.0, tf_mm=12.7, tw_mm=8.0)
    report = resolve_members([m], catalog)
    assert m.section == "IPE360" and report.mapped[0].method == "geometry"


def test_ambiguous_dimensions_do_not_confirm(catalog):
    # Two catalog rows share h/b within tolerance -> no unique identification -> stays quarantined.
    from dataclasses import replace

    doctored = dict(catalog)
    doctored["IPE300X"] = replace(catalog["IPE300"], name="IPE300X")
    m = ExtractedMember(id="x", raw_section="IPE305", h_mm=300.0, b_mm=150.0)
    report = resolve_members([m], doctored)
    assert m.section is None and len(report.fuzzy) == 1


def test_geometry_rescues_unknown_name_only_with_all_dims(catalog):
    # No name signal at all: all four dimensions are required before geometry may identify it.
    full = ExtractedMember(id="a", raw_section="Mystery Steel Thing",
                           h_mm=300.0, b_mm=150.0, tf_mm=10.7, tw_mm=7.1)
    partial = ExtractedMember(id="b", raw_section="Mystery Steel Thing",
                              h_mm=300.0, b_mm=150.0)
    report = resolve_members([full, partial], catalog)
    assert full.section == "IPE300" and report.mapped[0].method == "geometry"
    assert partial.section is None and len(report.unknown) == 1


def test_member_dims_roundtrip_through_schema(tmp_path):
    m = ExtractedMember(id="x", raw_section="IPE305", h_mm=300.0, b_mm=150.0,
                        tf_mm=10.7, tw_mm=7.1)
    model = ExtractedModel(kind="donor", members=[m])
    p = tmp_path / "donor.json"
    model.save(p)
    loaded = ExtractedModel.load(p)
    assert loaded.members[0].h_mm == 300.0 and loaded.members[0].tw_mm == 7.1


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


# --- catalog data integrity ------------------------------------------------

def test_catalog_property_consistency():
    """Every catalog row (EU IPE/HE + 283-shape US + UK UB/UC) must obey the physical relations between its
    properties, so a transcription slip (cm vs mm, an Iy/Wpl swap, a strong/weak-axis mix-up) fails
    loudly instead of silently corrupting a capacity check. This also guards any future catalog
    expansion: add rows and this recomputes the derived quantities from the primaries.

    Relations (catalogue units): mass ≈ 0.785·A_cm2 (steel at 7850 kg/m³, +fillets);
    Wel_y ≈ Iy/(h/2); iy ≈ √(Iy/A); Wel_z ≈ Iz/(b/2); iz ≈ √(Iz/A); and Wpl ≥ Wel on both axes.
    Tolerances have generous headroom over the worst real deviation (~1.5%).

    AISC HSS have a different mass basis by design: the tabulated *nominal weight* is computed from
    the nominal wall, while the area (and all section properties) use the design wall
    ``tdes = 0.93·tnom`` (A500 ERW), so for tubes the expected mass is ``0.785·A/0.93``.
    """
    cat = load_default_catalog()
    assert len(cat) > 250                                   # EU + US + UK merged
    for s in cat.values():
        shp = s.shape.upper()
        A, Iy, Iz = s.A / 1e2, s.Iy / 1e4, s.Iz / 1e4       # -> cm^2, cm^4
        Wely, Welz = s.Wel_y / 1e3, s.Wel_z / 1e3           # -> cm^3
        Wply, Wplz = s.Wpl_y / 1e3, s.Wpl_z / 1e3
        h, b, iy, iz = s.h / 10, s.b / 10, s.iy / 10, s.iz / 10  # -> cm
        # ERW nominal-wall weight on design-wall area applies to US *rectangular* HSS only; round
        # hollow (CHS/Pipe) and every EN/GB section tabulate mass on the same wall -> 0.785*A.
        erw = s.is_hollow and not s.is_round and s.standard == "US"
        mass_expected = 0.785 * A / (0.93 if erw else 1.0)
        assert s.mass_kgm == pytest.approx(mass_expected, rel=0.05), f"{s.name}: mass vs area"
        # The Wel = I/(half-depth) relation assumes the centroid sits at mid-depth. True about both
        # axes for doubly-symmetric sections; about the major axis only for channels (symmetric there);
        # never for angles (centroid offset on both legs). Radius of gyration holds for all.
        if shp != "L":
            assert Wely == pytest.approx(Iy / (h / 2), rel=0.03), f"{s.name}: Wel_y vs Iy/(h/2)"
        if shp not in ("C", "L"):
            assert Welz == pytest.approx(Iz / (b / 2), rel=0.03), f"{s.name}: Wel_z vs Iz/(b/2)"
        assert iy == pytest.approx(math.sqrt(Iy / A), rel=0.03), f"{s.name}: iy vs sqrt(Iy/A)"
        assert iz == pytest.approx(math.sqrt(Iz / A), rel=0.03), f"{s.name}: iz vs sqrt(Iz/A)"
        assert Wply >= Wely and Wplz >= Welz, f"{s.name}: Wpl < Wel"


# --- round hollow (CHS) + i_min --------------------------------------------

def _chs(name="CHS200X5", D=200.0, t=5.0):
    """A round-section SectionProps built like the round loader does (D->h=b, t->tw=tf)."""
    import math as _m
    r_out, r_in = D / 2.0, D / 2.0 - t
    A = _m.pi * (r_out**2 - r_in**2)
    I = _m.pi / 4.0 * (r_out**4 - r_in**4)
    Wel = I / r_out
    Wpl = 4.0 / 3.0 * (r_out**3 - r_in**3)
    i = _m.sqrt(I / A)
    return SectionProps(
        name=name, shape="CHS", h=D, b=D, tw=t, tf=t, r=0.0, A=A,
        mass_kgm=A * 7.85e-3, Iy=I, Wel_y=Wel, Wpl_y=Wpl, iy=i,
        Iz=I, Wel_z=Wel, Wpl_z=Wpl, iz=i, standard="EU",
    )


def test_i_min_defaults_to_min_geometric_axis():
    # I/H section: principal == geometric, so i_min falls back to min(iy, iz) when not supplied.
    ipe = load_catalog()["IPE300"]
    assert ipe.i_min == pytest.approx(min(ipe.iy, ipe.iz))


def test_i_min_uses_supplied_value_for_angles():
    # Angles carry a real principal-axis i_min (i_v) below their geometric iz.
    s = SectionProps(
        name="L100X100X10", shape="L", h=100.0, b=100.0, tw=10.0, tf=10.0, r=12.0,
        A=1920.0, mass_kgm=15.0, Iy=1.77e6, Wel_y=24700.0, Wpl_y=44000.0, iy=30.4,
        Iz=1.77e6, Wel_z=24700.0, Wpl_z=44000.0, iz=30.4, standard="EU", i_min=19.4,
    )
    assert s.i_min == pytest.approx(19.4)
    assert s.i_min < s.iz


def test_chs_shear_area_is_2A_over_pi():
    # EN 1993-1-1 6.2.6(3): round hollow A_v = 2A/pi.
    s = _chs()
    assert s.is_hollow is True
    assert s.Av_z == pytest.approx(2.0 * s.A / math.pi)


def test_round_loader_converts_and_sets_symmetric_axes(tmp_path):
    # Imperial round CSV (OD/wall in inches) -> internal mm, with Iy==Iz and i_min==r_gyr.
    csv_text = (
        "name,shape,OD_in,tdes_in,A_in2,mass_lbft,I_in4,S_in3,Z_in3,r_in\n"
        "HSS6.000X0.250,CHS,6.0,0.233,4.22,14.35,17.6,5.87,7.79,2.04\n"
    )
    p = tmp_path / "round.csv"
    p.write_text(csv_text, encoding="utf-8")
    cat = load_catalog_round(p, metric=False)
    s = cat["HSS6.000X0.250"]
    assert s.shape == "CHS" and s.is_hollow
    assert s.h == pytest.approx(152.4) and s.b == pytest.approx(152.4)   # 6 in -> mm, h=b=OD
    assert s.tw == pytest.approx(s.tf) and s.r == 0.0
    assert s.Iy == s.Iz and s.Wel_y == s.Wel_z                           # axisymmetric
    assert s.A == pytest.approx(4.22 * 645.16, rel=1e-4)
    assert s.i_min == pytest.approx(2.04 * 25.4, rel=1e-4)
    assert s.Av_z == pytest.approx(2.0 * s.A / math.pi)


# --- new-family name mapping against the default (merged) catalog -----------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Round HSS HSS6.625X0.280", "HSS6.625X0.280"),   # 2-token round HSS (one X)
        ("HSS6.625x0.280", "HSS6.625X0.280"),
        ("PIPE6STD", "PIPE6STD"),                          # AISC pipe
        ("Pipe-Column PIPE4XS", "PIPE4XS"),
        ("CHS168.3X6.3", "CHS168.3X6.3"),                 # canonical EN CHS
        ("CHS 168.3x6.3", "CHS168.3X6.3"),                 # spaced + lowercase x
        ("168.3X6.3 CHS", "CHS168.3X6.3"),                 # trailing CHS token
        ("UPN200", "UPN200"),                              # channel via generic profile path
        ("UPN 200", "UPN200"),
        ("L100x100x10", "L100X100X10"),                    # angle (AISC L pattern)
    ],
)
def test_map_new_families(raw, expected):
    cat = load_default_catalog()
    r = map_section(raw, cat)
    assert r.canonical == expected, f"{raw} -> {r.canonical} ({r.method})"
