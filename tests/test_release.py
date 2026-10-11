from pathlib import Path

from land_water_bench.release import DEFAULT_RESULTS, terrain_dataset, verify_release

ROOT = Path(__file__).resolve().parents[1]


def test_latest_release_verifies_both_tasks_without_external_run_directories():
    proof = verify_release(ROOT / DEFAULT_RESULTS)
    assert proof['models'] == 12 and proof['records'] == 388800
    assert proof['new_api_requests'] == 0
    assert sum(m['invalid'] for m in proof['terrain']['models']) == 471
    assert sum(m['unresolved'] for m in proof['terrain']['models']) == 723
    assert sum(m['invalid'] for m in proof['classification']['models']) == 40
    assert sum(m['excluded_unresolved'] for m in proof['classification']['models']) == 2


def test_bundled_terrain_input_preserves_all_prompts_and_reference_masks():
    dataset, checksum = terrain_dataset()
    assert checksum == 'fed2b3ed63dbf80d149cf9ad0208ff9a941e3d12171b5d2150d37e8c20dddace'
    assert len(dataset['points']) == 16200
    assert sum(p['height_reference_status'] == 'ok' for p in dataset['points']) == 15928
    assert all('Latitude:' in p['prompt'] and 'EGM2008' in p['prompt'] for p in dataset['points'])
