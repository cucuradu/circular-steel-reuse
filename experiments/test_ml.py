"""Phase 4 tests: synthetic dataset, capacity surrogate, clustering, reuse score."""

import pytest
from ml.clustering import cluster_similar, group_by_section
from ml.reuse_score import reuse_scores
from ml.surrogate import train_surrogate
from ml.synthetic import generate_beam_dataset

from steelreuse.core.sections import load_catalog
from steelreuse.schema import ExtractedMember


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


@pytest.fixture(scope="module")
def dataset(cat):
    # small sweep keeps the test fast
    return generate_beam_dataset(cat, grades=("S235", "S355"),
                                 spans=range(3000, 9001, 1000), udls=range(5, 36, 5))


def test_dataset_shape_and_labels(dataset):
    assert len(dataset) > 200
    assert dataset["utilization"].min() >= 0
    assert set(dataset["passes"].unique()) <= {0, 1}
    assert dataset["passes"].nunique() == 2  # both passing and failing rows generated


def test_surrogate_tracks_deterministic(dataset):
    model = train_surrogate(dataset)
    # surrogate should reproduce the deterministic utilization closely
    assert model.r2 > 0.95


def test_group_by_section_exact():
    members = [
        ExtractedMember(id="a", section="IPE300"),
        ExtractedMember(id="b", section="IPE300"),
        ExtractedMember(id="c", section="HEB300"),
        ExtractedMember(id="d", section=None),  # unmapped -> ignored
    ]
    groups = group_by_section(members)
    assert set(groups) == {"IPE300", "HEB300"}
    assert len(groups["IPE300"]) == 2


def test_cluster_similar_groups_close_sections(cat):
    members = [
        ExtractedMember(id="1", section="IPE300", length_mm=6000),
        ExtractedMember(id="2", section="IPE330", length_mm=6000),  # similar to IPE300
        ExtractedMember(id="3", section="HEB300", length_mm=4000),  # bulky -> other cluster
    ]
    labels = cluster_similar(members, cat, n_clusters=2)
    assert labels["1"] == labels["2"]
    assert labels["3"] != labels["1"]


def test_reuse_score_rewards_standardization_and_length():
    members = [
        ExtractedMember(id="r1", section="IPE300", length_mm=8000),
        ExtractedMember(id="r2", section="IPE300", length_mm=8000),
        ExtractedMember(id="r3", section="IPE300", length_mm=8000),
        ExtractedMember(id="u1", section="HEB300", length_mm=2000),  # unique + short
    ]
    scores = reuse_scores(members)
    assert scores["r1"] > scores["u1"]
    assert all(0.0 <= s <= 1.0 for s in scores.values())
