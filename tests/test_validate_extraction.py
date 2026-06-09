"""Tests for the extraction-validation helper (steelreuse.validate_extraction)."""

from steelreuse.validate_extraction import main

_MODEL = (
    '{"kind": "donor", "members": ['
    '{"id": "b1", "role": "beam", "raw_section": "IPE300", "length_mm": 6000},'
    '{"id": "c1", "role": "column", "raw_section": "HEB200", "length_mm": 3000}]}'
)


def _write(tmp_path):
    p = tmp_path / "model.json"
    p.write_text(_MODEL, encoding="utf-8")
    return str(p)


def test_expect_match_exits_zero(tmp_path, capsys):
    assert main([_write(tmp_path), "--expect", "2"]) == 0
    assert "OK" in capsys.readouterr().out


def test_expect_mismatch_exits_one(tmp_path, capsys):
    assert main([_write(tmp_path), "--expect", "5"]) == 1
    assert "MISMATCH" in capsys.readouterr().err


def test_schedule_row_count_match(tmp_path, capsys):
    sched = tmp_path / "schedule.csv"
    sched.write_text("Type,Length\nIPE300,6000\nHEB200,3000\n", encoding="utf-8")
    assert main([_write(tmp_path), "--schedule", str(sched)]) == 0


def test_missing_file_exits_one(tmp_path, capsys):
    assert main([str(tmp_path / "nope.json")]) == 1
    assert "not found" in capsys.readouterr().err.lower()
