import copy

import pytest

from land_water_bench.reextract import independent_terrain, project
from land_water_bench.terrain_global import metrics


def test_offline_revision_scores_recovered_answers_and_excludes_only_filtering():
    texts = [
        '$z_{bed}$ {"type":"Water","elevation_m":-500}',
        'The request was rejected because it was considered high risk',
        '{"type":"Water","elevation_m":-500}',  # Truncated: a visible candidate is insufficient.
        '{"type":"Water","elevation_m":"-500 m"}',
        '{"type":"Water","elevation_m":-500}\nCorrection: {"type":"Solid","elevation_m":500}',
    ]
    original = [{'sample_id': i, 'status': 'invalid', 'label': None, 'prediction': None, 'text': text}
                for i, text in enumerate(texts)]
    frozen = copy.deepcopy(original)
    revised = {i: project(row, 'terrain', complete=i not in {1, 2}, filtered=i == 1)
               for i, row in enumerate(original)}
    points = [{'id': i, 'lat': 0, 'weight': 1, 'truth': 0, 'elevation_m': -500,
               'height_reference_status': 'ok', 'stratum': 'test'} for i in range(5)]
    score = metrics(points, revised)
    independent_terrain(points, revised, score)
    assert score['invalid'] == 2 and score['infra_errors'] == 1
    assert score['scored'] == score['joint_scored'] == 4
    assert score['joint_accuracy']['500'] == pytest.approx(.25)
    assert score['classification_accuracy'] == pytest.approx(.25)
    assert original == frozen


def test_unresolved_and_incomplete_classification_cannot_be_rescued_from_text():
    text = 'Water\n\nThere is land nearby.'
    row = {'sample_id': 0, 'status': 'invalid', 'label': None, 'text': text}
    assert project(row, 'classification', complete=True)['label'] == 0
    assert project(row, 'classification', complete=False)['status'] == 'invalid'
    row['status'] = 'infra_error'
    assert project(row, 'classification', complete=True) == row
