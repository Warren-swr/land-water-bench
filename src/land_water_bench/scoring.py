"""Explicit scoring policy for excluding unresolved API failures, not invalid answers."""

from __future__ import annotations

import copy

VERSION = "land-water-score/2.0-exclude-unresolved"


def exclude_unresolved(score: dict) -> dict:
    result = copy.deepcopy(score)
    scored = score["valid"] + score["invalid"]
    result.update(
        score_policy=VERSION,
        excluded_unresolved=score["infra_errors"],
        scored_points=scored,
        full_grid_complete=score["complete"],
        full_grid_weighted_accuracy=score["weighted_accuracy"],
        full_grid_accuracy_lower_bound=score["accuracy_lower_bound"],
        full_grid_accuracy_upper_bound=score["accuracy_upper_bound"],
        complete=score["pending"] == 0 and scored > 0,
    )
    # observed_weighted_accuracy includes invalid responses as wrong; valid_weighted_accuracy does not.
    result["weighted_accuracy"] = score["observed_weighted_accuracy"] if result["complete"] else None
    result["point_accuracy"] = score["correct"] / scored if result["complete"] else None
    if result["complete"]:
        result["accuracy_lower_bound"] = result["accuracy_upper_bound"] = result["weighted_accuracy"]
    return result
