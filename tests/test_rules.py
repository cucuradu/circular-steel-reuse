"""Externalised, versioned rule tables (Roadmap §1.2).

Guards that moving the citable rule data (grades, grade defaults, condition/verification knockdowns,
carbon factors) into version-stamped files reproduces the previously hardcoded values byte-for-byte
(no number drift) and that the rule versions/sources are inspectable for the evidence package.
"""

from steelreuse.core import audit, rules, sections
from steelreuse.core.carbon import load_factors


def test_material_grades_reproduce_hardcoded_values():
    fy, bands = rules.load_material_grades()
    # Single (headline / ASTM) values.
    assert fy["S235"] == 235.0 and fy["S275"] == 275.0 and fy["S355"] == 355.0
    assert fy["S420"] == 420.0 and fy["S460"] == 460.0
    assert fy["A36"] == 248.0 and fy["A992"] == 345.0 and fy["A53"] == 240.0
    assert fy["A500"] == 345.0 and fy["A1085"] == 345.0
    # EN thickness banding, exactly as before.
    assert bands["S275"] == [(16, 275.0), (40, 265.0), (63, 255.0),
                             (80, 245.0), (100, 235.0), (150, 225.0)]
    assert bands["S355"] == [(16, 355.0), (40, 345.0), (63, 335.0),
                             (80, 325.0), (100, 315.0), (150, 295.0)]
    # ASTM grades carry no banding.
    assert "A992" not in bands and "A36" not in bands


def test_nominal_fy_unchanged_across_bands():
    # The thickness banding still governs (no drift in the lookup).
    assert sections.nominal_fy("S275", 10) == 275.0
    assert sections.nominal_fy("S275", 50) == 255.0
    assert sections.nominal_fy("S355", 90) == 315.0
    assert sections.nominal_fy("A992", 50) == 345.0   # ASTM single value, no banding


def test_grade_defaults_priority_preserved():
    gd = rules.load_grade_defaults()
    assert gd[0] == ("HSS", "A500")          # multi-letter prefixes lead
    assert ("W", "A992") in gd and ("L", "A36") in gd
    # WT/MT/ST must be tried before the single-letter W/M/S, or a tee would map as a W.
    assert gd.index(("WT", "A992")) < gd.index(("W", "A992"))
    assert sections.default_grade_for_section("W18X55") == "A992"
    assert sections.default_grade_for_section("HSS6X6X1/2") == "A500"
    assert sections.default_grade_for_section("IPE300") is None  # EU left untouched


def test_condition_and_verification_knockdowns_reproduce_values():
    cond, reject = rules.load_condition_knockdown()
    assert cond == {"A": 1.0, "B": 0.95, "C": 0.85, "D": 0.0}
    assert reject == {"D"}
    ver, accepted = rules.load_verification_knockdown()
    assert ver == {"mill_cert": 1.0, "coupon_tested": 1.0, "documented": 0.95,
                   "visual_only": 0.9, "unverified": 0.0}
    assert accepted == {"mill_cert", "coupon_tested", "documented", "visual_only"}


def test_module_constants_are_loaded_from_files():
    # The live module-level tables the rest of the code imports come from the rule files.
    assert sections.FY_BY_GRADE == rules.load_material_grades()[0]
    assert sections._FY_BANDS == rules.load_material_grades()[1]
    assert sections._US_DEFAULT_GRADE == rules.load_grade_defaults()
    assert audit.CONDITION_KNOCKDOWN == rules.load_condition_knockdown()[0]
    assert audit.REJECT_CONDITION == rules.load_condition_knockdown()[1]
    assert audit.VERIFICATION_KNOCKDOWN == rules.load_verification_knockdown()[0]
    assert audit.ACCEPTED_VERIFICATION == rules.load_verification_knockdown()[1]


def test_load_factors_ignores_comment_header():
    """The version/source header on factors.csv must not leak into the parsed factors."""
    f = load_factors()["steel"]
    assert f.a1a3 == 1.55 and f.reuse_process == 0.10
    assert f.recycle_credit == 0.55 and f.reroll_credit == 1.00
    assert "steel" in {m for m in load_factors()}  # only real material rows, no '# ...' key


def test_rules_manifest_has_versions_sources_and_hashes():
    man = rules.rules_manifest()
    assert man["ruleset_version"] == rules.RULESET_VERSION
    names = {t["name"] for t in man["tables"]}
    assert {"material_grades", "grade_defaults",
            "condition_knockdown", "verification_knockdown"} <= names
    for t in man["tables"]:
        assert t["version"] and t["source"] and t["sha256"]
        assert t["n_rows"] > 0
        assert len(t["sha256"]) == 64  # sha256 hex digest
    assert man["carbon_factors"]["version"] and man["carbon_factors"]["sha256"]
    assert man["carbon_factors"]["source"]
    assert man["section_catalog"]["files"]  # at least the EU catalogue ships
    assert all(f["sha256"] and f["source"] for f in man["section_catalog"]["files"])


def test_rule_files_carry_version_headers():
    for path in (rules.MATERIAL_GRADES, rules.GRADE_DEFAULTS,
                 rules.CONDITION_KNOCKDOWN, rules.VERIFICATION_KNOCKDOWN):
        _rows, provenance, version = rules.read_rule_csv(path)
        assert version, f"{path.name} is missing a # version: header"
        assert provenance, f"{path.name} is missing # source: provenance"
