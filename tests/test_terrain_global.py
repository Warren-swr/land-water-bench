import json
import math

import httpx
import pytest

from land_water_bench.core import make_grid
from land_water_bench.terrain_global import GlobalRunner, PROMPT, PROTOCOL, metrics, validate_dataset
from land_water_bench.terrain_global_reference import row_values
from test_runner import integration_args


def test_area_weights_reference_mask_and_unresolved_have_distinct_denominators():
    points = [
        {'id':0,'weight':1,'truth':1,'elevation_m':100,'height_reference_status':'ok','stratum':'solid_low'},
        {'id':1,'weight':.5,'truth':0,'elevation_m':-200,'height_reference_status':'ok','stratum':'water_shelf'},
        {'id':2,'weight':.25,'truth':0,'elevation_m':None,'height_reference_status':'unscorable','stratum':'reference_unscorable'},
        {'id':3,'weight':.75,'truth':1,'elevation_m':300,'height_reference_status':'ok','stratum':'solid_mid'},
    ]
    records={0:{'status':'ok','label':1,'prediction':{'elevation_m':600}},
             1:{'status':'invalid','label':None},
             2:{'status':'ok','label':0,'prediction':{'elevation_m':-25}},
             3:{'status':'infra_error','label':None}}
    result=metrics(points,records)
    assert result['classification_accuracy']==pytest.approx(1.25/1.75)
    assert result['joint_accuracy']['500']==pytest.approx(1/1.5)
    assert result['point_joint_accuracy']['500']==.5
    assert result['joint_accuracy']['100']==0
    assert result['joint_scored']==2 and result['height_reference_excluded']==1
    assert result['height_reference_area_coverage']==.9
    assert result['elevation_mae_m']==500
    assert result['invalid']==1 and result['infra_errors']==1
    assert result['by_stratum']['reference_unscorable']['joint_accuracy']['500'] is None
    del records[3]
    partial=metrics(points,records)
    assert partial['pending']==1 and partial['joint_accuracy']['500'] is None
    assert partial['observed_joint_accuracy']['500']==pytest.approx(1/1.5)


def dataset():
    points=make_grid()
    for p in points:
        p.update(truth=0,elevation_m=-4000,height_reference_status='ok',height_reference_reason=None,stratum='water_deep',prompt=PROMPT.format(**p))
    return {'protocol':PROTOCOL,'points':points}


def test_full_grid_cannot_silently_drop_points_or_move_coordinates():
    data=dataset()
    validate_dataset(data)
    point=data['points'][0]
    point['elevation_m']=None
    point['height_reference_status']='unscorable'
    point['height_reference_reason']='unsupported_lake'
    validate_dataset(data)
    point['elevation_m']=-50
    with pytest.raises(ValueError,match='null'):
        validate_dataset(data)
    point['elevation_m']=None
    data['points'].pop()
    with pytest.raises(ValueError,match='16,200'):
        validate_dataset(data)


def test_row_registration_checks_both_axes_and_all_native_cells():
    lines=['latitude,longitude,z','degrees_north,degrees_east,meters']
    for lat in (1-1/480,1+1/480):
        for lon in range(1,360,2): lines.append(f'{lat},{lon-1/480},-4000')
    raw=('\n'.join(lines)+'\n').encode()
    assert len(row_values(raw,1,0))==360
    with pytest.raises(ValueError,match='registration'):
        row_values(raw,1,1)


@pytest.mark.asyncio
async def test_global_direct_runner_keeps_complete_grid_but_honors_smoke_limit(tmp_path,monkeypatch):
    from land_water_bench import runner as module
    args=integration_args(tmp_path,monkeypatch,limit=4,concurrency=2)
    args.dataset=tmp_path/'global.json'
    args.dataset.write_text(json.dumps(dataset()))
    args.max_timeout_retries=2
    actual_client=httpx.AsyncClient
    requests=[]
    async def handle(request):
        payload=json.loads(request.content)
        requests.append(payload)
        assert 'tools' not in payload and len(payload['messages'])==1
        assert 'grounded ice' in payload['messages'][0]['content']
        return httpx.Response(200,json={'model':'test-model','stop_reason':'end_turn','content':[{'type':'text','text':'{"type":"Water","elevation_m":-4200}'}]})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw:actual_client(transport=httpx.MockTransport(handle),**kw))
    task=GlobalRunner(args)
    await task.execute();task.lock.close()
    assert len(requests)==4 and task.state=='complete'
    summary=json.loads((args.run_dir/'summary.json').read_text())
    assert summary['models'][0]['metrics']['joint_accuracy']['500']==pytest.approx(1)
    assert len(json.loads((args.run_dir/'dataset.json').read_text())['points'])==16200
