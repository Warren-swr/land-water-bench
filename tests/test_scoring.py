import copy

import pytest

from land_water_bench.core import metrics
from land_water_bench.scoring import exclude_unresolved


def test_excludes_unknown_weight_but_keeps_invalid_and_wrong_answers():
    points = [{"id": i, "weight": w, "truth": 1} for i, w in enumerate([1, .5, .25, 2])]
    records = {0: {"status": "ok", "label": 1}, 1: {"status": "ok", "label": 0},
               2: {"status": "invalid"}, 3: {"status": "infra_error"}}
    original = metrics(points, records)
    frozen = copy.deepcopy(original)
    score = exclude_unresolved(original)
    assert score["weighted_accuracy"] == pytest.approx(1 / 1.75)
    assert score["valid_weighted_accuracy"] == pytest.approx(1 / 1.5)
    assert score["point_accuracy"] == pytest.approx(1 / 3)
    assert score["excluded_unresolved"] == 1 and score["scored_points"] == 3
    assert score["coverage"] == .75 and score["complete"] and not score["full_grid_complete"]
    assert original == frozen


def test_all_unknown_remains_without_a_score():
    original = metrics([{"id": 0, "weight": 1, "truth": 1}], {0: {"status": "infra_error"}})
    score = exclude_unresolved(original)
    assert score["weighted_accuracy"] is None and score["point_accuracy"] is None
    assert score["scored_points"] == 0 and not score["complete"]


def test_pending_requests_do_not_become_final_scores():
    points = [{"id": i, "weight": 1, "truth": 1} for i in range(3)]
    score = exclude_unresolved(metrics(points, {0: {"status": "ok", "label": 1}, 1: {"status": "infra_error"}}))
    assert score["pending"] == 1 and not score["complete"]
    assert score["weighted_accuracy"] is None


def test_fully_observed_score_is_preserved():
    points = [{"id": i, "weight": 1, "truth": 1} for i in range(2)]
    original = metrics(points, {0: {"status": "ok", "label": 1}, 1: {"status": "invalid"}})
    score = exclude_unresolved(original)
    assert score["weighted_accuracy"] == original["weighted_accuracy"] == .5
    assert score["excluded_unresolved"] == 0 and score["coverage"] == 1
