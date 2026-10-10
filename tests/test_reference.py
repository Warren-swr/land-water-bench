import numpy as np
import shapely
from shapely.geometry import Polygon, MultiPolygon

from land_water_bench.reference_builder import even_odd_contains, level_label, south_polar
from land_water_bench.results import rescore


def test_independent_ray_with_holes_parts_and_different_latitudes():
    outer = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], dtype=float)
    hole = np.array([[2, 2], [2, 8], [8, 8], [8, 2], [2, 2]], dtype=float)
    other = np.array([[20, 0], [25, 0], [25, 5], [20, 5], [20, 0]], dtype=float)
    xy = np.array([[1, 1], [5, 5], [9, 5], [-1, 5], [23, 3], [30, 3]], dtype=float)
    expected = [True, False, True, False, True, False]
    assert even_odd_contains([outer, hole, other], xy).tolist() == expected
    geom = MultiPolygon([Polygon(outer, [hole]), Polygon(other)])
    assert shapely.covers(geom, shapely.points(xy)).tolist() == expected


def test_hierarchical_land_lake_island_pond_and_antarctica_semantics():
    assert [level_label(i) for i in range(7)] == [0, 1, 0, 1, 0, 1, 1]


def test_dateline_halves_do_not_join_across_the_middle_of_the_world():
    east = np.array([[170, -90], [180, -90], [180, -80], [170, -80], [170, -90]], dtype=float)
    west = np.array([[-180, -90], [-170, -90], [-170, -80], [-180, -80], [-180, -90]], dtype=float)
    xy = np.array([[179, -89], [-179, -89], [0, -89], [179, 89]], dtype=float)
    assert even_odd_contains([east, west], xy).tolist() == [True, True, False, False]


def test_polar_coast_closes_around_all_longitudes_without_a_dateline_wedge():
    coastline = np.column_stack([np.linspace(-180, 180, 361), np.full(361, -75)])
    ring = south_polar(coastline)
    xy = np.array([[-179, -89], [179, -89], [1, -89], [91, -89], [0, -70], [179, 89]], dtype=float)
    expected = [True, True, True, True, False, False]
    assert even_odd_contains([ring], south_polar(xy)).tolist() == expected
    assert shapely.covers(Polygon(ring), shapely.points(south_polar(xy))).tolist() == expected


def test_reference_rescore_keeps_invalid_in_denominator_and_excludes_only_unresolved():
    points = [{"id": i, "lat": 0, "weight": 1, "truth": truth} for i, truth in enumerate([0, 1, 1, 0])]
    records = {0: {"status": "ok", "label": 0}, 1: {"status": "ok", "label": 0},
               2: {"status": "invalid", "label": None}, 3: {"status": "infra_error", "label": None}}
    score, _ = rescore(points, records)
    assert score["weighted_accuracy"] == 1 / 3
    assert score["scored_points"] == 3 and score["excluded_unresolved"] == 1
    points[1]["truth"] = 0
    updated, _ = rescore(points, records)
    assert updated["weighted_accuracy"] == 2 / 3
    assert updated["scored_points"] == 3 and updated["invalid"] == 1
