"""Material passport export (passport_rows): row columns + the EN-verdict join onto matched donors."""

from steelreuse.core.carbon import Passport, PassportEntry, passport_rows


def _entry(mid: str, saved: float) -> PassportEntry:
    return PassportEntry(id=mid, section="IPE300", grade="S275", length_mm=6000.0,
                         mass_kg=253.2, volume_m3=0.03, ec_new_kgco2e=392.5,
                         ec_reuse_kgco2e=34.5, ec_saved_kgco2e=saved,
                         verification_status="mill_cert", condition_grade="B")


class _A:  # duck-typed assignment (supply_id/slot_id/status/utilization)
    def __init__(self, supply_id, slot_id, status, util):
        self.supply_id, self.slot_id, self.status, self.utilization = supply_id, slot_id, status, util


def test_passport_rows_columns_and_verdict_join():
    pp = Passport(entries=[_entry("D1", 358.0), _entry("D2", 358.0)])
    rows = passport_rows(pp, [_A("D1", "B1#0", "OK", 0.563)])
    assert len(rows) == 2
    assert {"id", "section", "grade", "mass_kg", "condition_grade", "verification_status",
            "ec_saved_kgco2e", "reuse_verdict", "reuse_slot", "reuse_utilisation"} <= set(rows[0])
    # matched donor carries the EN verdict joined from the assignment
    r1 = next(r for r in rows if r["id"] == "D1")
    assert r1["reuse_verdict"] == "OK"
    assert r1["reuse_slot"] == "B1#0"
    assert r1["reuse_utilisation"] == 0.563
    # unmatched donor says so, with blank slot/utilisation
    r2 = next(r for r in rows if r["id"] == "D2")
    assert r2["reuse_verdict"] == "not reused"
    assert r2["reuse_slot"] == "" and r2["reuse_utilisation"] == ""


def test_passport_rows_without_assignments_all_not_reused():
    rows = passport_rows(Passport(entries=[_entry("D1", 358.0)]))
    assert rows[0]["reuse_verdict"] == "not reused"
