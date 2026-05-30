"""Phase 6 tests: report context, the anti-hallucination numeric guard, and HTML rendering."""

from pathlib import Path

import pytest

from steelreuse.core.sections import load_catalog
from steelreuse.llm.report import (
    build_report_context,
    deterministic_narrative,
    find_invented_numbers,
    generate_narrative,
    render_html,
)
from steelreuse.pipeline import run_pipeline

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def ctx():
    res = run_pipeline(
        str(DATA / "samples" / "donor.json"),
        str(DATA / "samples" / "demand.json"),
        catalog=load_catalog(),
    )
    return build_report_context(res)


def test_context_has_expected_keys(ctx):
    for key in ("n_reused", "match_co2_saved_kg", "unknown", "assignments", "disclaimer"):
        assert key in ctx
    assert ctx["unknown"] == 1
    assert ctx["disclaimer"].startswith("Member-level")


def test_numeric_guard_flags_invented_numbers():
    allowed = {12.0, 45.5}
    assert find_invented_numbers("we saved 45.5 kg across 12 members", allowed) == []
    # 999 is not in the allowed set -> flagged as invented
    assert find_invented_numbers("an extra 999 kg", allowed) == [999.0]


def test_deterministic_narrative_only_uses_real_numbers(ctx):
    from steelreuse.llm.report import _allowed_numbers

    text = deterministic_narrative(ctx)
    assert find_invented_numbers(text, _allowed_numbers(ctx)) == []


def test_generate_narrative_null_provider_is_deterministic(ctx):
    narrative, source = generate_narrative(ctx)  # default NullProvider
    assert source == "deterministic"
    assert "demand slot" in narrative


def test_render_html_contains_kpis(ctx):
    html = render_html(ctx, "test narrative", "deterministic")
    assert "Circular Structural Reuse" in html
    assert "kg CO2e saved" in html
    assert "test narrative" in html
    assert str(ctx["n_reused"]) in html
