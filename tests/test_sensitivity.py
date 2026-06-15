"""Sensitivity study — invariant (property-style) tests on the CO2-saved sweep.

These assert *directional* truths that must hold for any model rather than pinning exact numbers, so
they stay valid as the bundled samples or carbon factors evolve:
  * a lower reclaimed knockdown never *increases* CO2 saved (it can only shrink the feasible reuse set);
  * booking net of an end-of-life counterfactual never *increases* CO2 saved, and re-rolling (the
    larger foregone credit) reduces it at least as much as recycling;
  * the tornado is sorted by swing and every swing is non-negative;
  * the Monte Carlo band is ordered (P5 <= P50 <= P95) and reproducible for a fixed seed.
"""

import pytest

from steelreuse.core.sections import load_default_catalog
from steelreuse.resources import sample_path
from steelreuse.sensitivity import (
    RunParams,
    evaluate,
    run_monte_carlo,
    run_tornado,
)


@pytest.fixture(scope="module")
def models():
    return str(sample_path("donor.json")), str(sample_path("demand.json"))


@pytest.fixture(scope="module")
def catalog():
    return load_default_catalog()


def test_lower_knockdown_does_not_increase_savings(models, catalog):
    donor, demand = models
    full = evaluate(RunParams(knockdown=1.0), donor, demand, catalog)
    knocked = evaluate(RunParams(knockdown=0.7), donor, demand, catalog)
    assert knocked <= full + 1e-6


def test_counterfactual_does_not_increase_savings(models, catalog):
    donor, demand = models
    none = evaluate(RunParams(counterfactual="none"), donor, demand, catalog)
    recycling = evaluate(RunParams(counterfactual="recycling"), donor, demand, catalog)
    rerolling = evaluate(RunParams(counterfactual="rerolling"), donor, demand, catalog)
    # Saving is booked net of the foregone fate, and reroll_credit >= recycle_credit >= 0.
    assert recycling <= none + 1e-6
    assert rerolling <= recycling + 1e-6


def test_tornado_is_sorted_and_swings_non_negative(models, catalog):
    donor, demand = models
    baseline_co2, entries = run_tornado(donor, demand, catalog=catalog)
    assert baseline_co2 > 0  # the demo model saves carbon
    swings = [e.swing for e in entries]
    assert all(s >= 0 for s in swings)
    assert swings == sorted(swings, reverse=True)
    # every driver was evaluated at >= 2 points
    assert all(len(e.variants) >= 2 for e in entries)


def test_monte_carlo_band_is_ordered_and_reproducible(models, catalog):
    donor, demand = models
    a = run_monte_carlo(donor, demand, n=8, seed=42, catalog=catalog)
    b = run_monte_carlo(donor, demand, n=8, seed=42, catalog=catalog)
    assert a.n == len(a.samples) == 8
    assert a.p5 <= a.p50 <= a.p95
    assert a.samples == b.samples  # deterministic for a fixed seed
