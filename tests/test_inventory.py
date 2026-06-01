"""Tests for the extraction inventory (works on any model, mapped or not)."""

from pathlib import Path

import pytest

from steelreuse.core.sections import load_catalog
from steelreuse.inventory import build_inventory, render_inventory_html, render_inventory_text
from steelreuse.schema import ExtractedMember, ExtractedModel

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_inventory_on_sample_donor_with_catalog(cat):
    model = ExtractedModel.load(DATA / "samples" / "donor.json")
    inv = build_inventory(model, cat)
    assert inv.n_members == 8
    assert inv.n_beams + inv.n_columns == 8
    assert inv.n_unknown == 1            # the US W-shape
    assert inv.n_mapped == 7
    assert inv.total_mass_kg > 0         # mapped subset is costed
    assert inv.total_length_m > 0


def test_inventory_works_without_catalog():
    # an entirely foreign (US) model: no catalog -> still a valid inventory, nothing costed
    members = [
        ExtractedMember(id="1", role="beam", raw_section="W Shapes W14X30", length_mm=6000, level="L2"),
        ExtractedMember(id="2", role="beam", raw_section="W Shapes W14X30", length_mm=6000, level="L2"),
        ExtractedMember(id="3", role="column", raw_section="W10X33", length_mm=4000, level="L1"),
    ]
    model = ExtractedModel(kind="donor", members=members, model_name="US")
    inv = build_inventory(model, catalog=None)
    assert inv.n_members == 3
    assert inv.total_mass_kg == 0.0      # not costed without a catalog
    top = inv.by_section[0]
    assert top.raw_section == "W Shapes W14X30" and top.count == 2
    assert top.total_length_mm == 12000.0
    assert inv.by_level == {"L1": 1, "L2": 2}


def test_inventory_renders(cat):
    model = ExtractedModel.load(DATA / "samples" / "donor.json")
    inv = build_inventory(model, cat)
    txt = render_inventory_text(inv)
    assert "Inventory" in txt and "members" in txt
    html = render_inventory_html(inv)
    assert "Pre-demolition Steel Inventory" in html
    assert "By section" in html and "By level" in html
