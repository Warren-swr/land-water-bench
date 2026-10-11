import asyncio
import json
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest

from land_water_bench.terrain import PROMPT, PROTOCOL, TerrainRunner, metrics, parse_prediction, project_answer
from test_runner import integration_args, sample_runner


@pytest.mark.parametrize('text,expected', [
    ('{"type":"Water","elevation_m":-4700}', {'type':'Water','elevation_m':-4700}),
    ('The point is in the Atlantic.\n```json\n{"type":"water","elevation_m":-4321.5}\n```', {'type':'Water','elevation_m':-4321.5}),
    ('{"type":"Solid","elevation_m":-150}', {'type':'Solid','elevation_m':-150}),
    ('{"type":"Water","elevation_m":"-4700 m"}', None),
    ('{"type":"Water","elevation_m":true}', None),
    ('{"type":"Water","elevation_m":NaN}', None),
    ('{"type":"Water","elevation_m":-4,"elevation_m":-4000}', None),
    ('{"type":"Solid","elevation_m":100} {"type":"Water","elevation_m":-100}', None),
    ('{"type":"Solid","elevation_m":[10,100]}', None),
    ('{"type":"Solid","elevation_m":100', None),
])
def test_terrain_parser(text, expected):
    assert parse_prediction(text)[0] == expected


@pytest.mark.parametrize('text,expected', [
    (r'$z_{bed} = 1/3$ {user_query}\n{"type":"Water","elevation_m":-700}', -700),
    ('{"type":"Water","elevation_m":-700}\nExplanation.\n{"type":"water","elevation_m":-700.0}', -700),
    ('{"type":"Water","elevation_m":-50 00}\nCorrected, valid JSON:\n{"type":"Water","elevation_m":-5000}', -5000),
    ('{"type":"Solid","evaluation_m":80} — corrected: {"type":"Solid","elevation_m":80}', 80),
    ('{"type":"Solid","elevation_m":170}\nThat is wrong. Corrected answer:\n{"type":"Solid","elevation_m":1070}', 1070),
    ('{"type":"Solid","elevation_m":170}\nLet me refine:\n{"type":"Solid","elevation_m":1070}', 1070),
    ('{"type":"Solid","elevation_m":170}\nFinal answer:\n{"type":"Solid","elevation_m":1070}', 1070),
])
def test_explicit_corrections_repetition_and_math_in_body(text, expected):
    assert parse_prediction(text)[0]['elevation_m'] == expected


@pytest.mark.parametrize('text', [
    '{"broken": {"type":"Water","elevation_m":-700}',
    '{"type":"Water","elevation_m":-700}\n{"type":"Solid","elevation_m":700}',
    '{"type":"Water","elevation_m":-700}\nCorrection: {"type":"Water","elevation_m":"-700 m"}',
    '{"type":"Water","elevation_m":-700,"elevation_m":-800}',
    '{"type":"Water","elevation_m":-700}\n{"type":"Water","elevation_m":- seventy}',
    '{"example":{"type":"Water","elevation_m":-700}}',
])
def test_corrections_do_not_salvage_broken_or_ambiguous_answers(text):
    assert parse_prediction(text)[0] is None


def test_terrain_parser_keeps_old_snapshot_reproducible():
    from land_water_bench.terrain import LEGACY_PARSER_VERSION
    text = r'$z_{bed}$ {"type":"Water","elevation_m":-700}'
    assert parse_prediction(text, version=LEGACY_PARSER_VERSION)[0] is None
    assert parse_prediction(text)[0]['elevation_m'] == -700


@pytest.mark.parametrize('wire,body', [
    ('messages', {'stop_reason':'content_filter','content':[{'type':'text','text':'blocked'}]}),
    ('responses', {'status':'incomplete','incomplete_details':{'reason':'content_filter'},'output':[]}),
    ('responses', {'status':'failed','error':{'code':'content_filter'},'output':[]}),
])
def test_filtered_responses_are_unresolved_for_both_tasks(wire, body):
    from land_water_bench.answers import reextract_record
    from land_water_bench.core import extract_answer
    row = {'status':'invalid','label':None,'response':body,'text':extract_answer(wire,body)[0]}
    for result in [project_answer(row,wire), reextract_record(row,wire)]:
        assert result['status'] == 'infra_error' and result['label'] is None
        assert result['error']['type'] == 'content_filter'


def test_truncation_and_thinking_are_not_usable_answers():
    body = {'content':[{'type':'thinking','thinking':'{"type":"Water","elevation_m":-4000}'},
                       {'type':'text','text':'{"type":"Solid","elevation_m":200}'}], 'stop_reason':'end_turn'}
    assert project_answer({'response':body},'messages')['prediction']['type'] == 'Solid'
    body['stop_reason'] = 'max_tokens'
    assert project_answer({'response':body},'messages')['status'] == 'invalid'


def test_joint_score_counts_invalid_excludes_infra_and_includes_wrong_type_height_error():
    points=[{'id':i,'truth':1 if i % 2 == 0 else 0,'elevation_m':100 if i % 2 == 0 else -4000,'stratum':'test'} for i in range(5)]
    records={0:{'status':'ok','label':1,'prediction':{'elevation_m':200}},
             1:{'status':'ok','label':1,'prediction':{'elevation_m':-4000}},
             2:{'status':'invalid'},3:{'status':'infra_error'}}
    partial=metrics(points,records)
    assert partial['pending'] == 1 and partial['joint_accuracy']['500'] is None
    records[4]={'status':'ok','label':1,'prediction':{'elevation_m':1000}}
    score=metrics(points,records)
    assert score['scored'] == 4 and score['invalid'] == 1 and score['infra_errors'] == 1
    assert score['classification_accuracy'] == .5
    assert score['joint_accuracy'] == {'100':.25,'500':.25,'1000':.5}
    assert score['elevation_mae_m'] == pytest.approx(1000/3)
    assert score['numeric_coverage'] == .75
    assert score['by_type']['Water']['scored'] == 1


@pytest.mark.asyncio
async def test_timeout_retries_have_separate_budget(tmp_path):
    runner,model=sample_runner(tmp_path)
    runner.args.max_timeout_retries=2
    runner.timeout_attempts=Counter()
    async def timeout(request):
        raise httpx.ReadTimeout('test',request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        await runner.sample(client,model,{'id':0,'prompt':'test'})
    assert runner.attempts[('m',0)] == 3
    assert runner.retries == 2 and runner.records['m'][0]['status'] == 'infra_error'
    for handle in runner.handles.values(): handle.close()


@pytest.mark.asyncio
async def test_terrain_runner_freezes_prompts_and_resumes_without_new_requests(tmp_path,monkeypatch):
    from land_water_bench import runner as module
    args=integration_args(tmp_path,monkeypatch,limit=0,concurrency=2)
    args.dataset=tmp_path/'terrain.json'
    args.max_timeout_retries=2
    points=[]
    for i in range(4):
        point={'id':i,'lat':float(i+1),'lon':1.0,'truth':0,'elevation_m':-4000,'weight':1,'stratum':'deep'}
        point['prompt']=PROMPT.format(**point)
        points.append(point)
    args.dataset.write_text(json.dumps({'protocol':PROTOCOL,'points':points}))
    actual_client=httpx.AsyncClient
    calls=[]
    async def handler(request):
        body=json.loads(request.content)
        calls.append(body)
        assert 'tools' not in body and 'system' not in body
        return httpx.Response(200,json={'model':'test-model','stop_reason':'end_turn',
            'content':[{'type':'text','text':'Reasoning in the body.\n{"type":"Water","elevation_m":-4200}'}]})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:actual_client(transport=httpx.MockTransport(handler),**kwargs))
    runner=TerrainRunner(args)
    await runner.execute()
    runner.lock.close()
    summary=json.loads((args.run_dir/'summary.json').read_text())
    assert len(calls)==4 and summary['state']=='complete'
    assert summary['models'][0]['metrics']['joint_accuracy']['500']==1
    assert summary['models'][0]['metrics']['joint_accuracy']['100']==0
    assert 'terrain.py' in json.loads((args.run_dir/'manifest.json').read_text())['execution_source_sha256']
    second=TerrainRunner(args)
    await second.execute()
    second.lock.close()
    assert len(calls)==4
    from land_water_bench.terrain_results import verify
    dataset, saved, models = verify(args.run_dir)
    assert len(dataset['points'])==4 and models[0]['metrics']['joint_accuracy']['500']==1
    evidence=args.run_dir/'m.jsonl'
    rows=[json.loads(line) for line in evidence.read_text().splitlines()]
    rows[0]['text']='Different answer body.'
    evidence.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    with pytest.raises(ValueError,match='extraction'):
        verify(args.run_dir)


def test_reference_interpolation_checks_cell_registration():
    from land_water_bench.terrain_reference import point_height
    lat,lon=29,9
    rows=['latitude,longitude,z','degrees_north,degrees_east,meters']
    values=iter([100,200,300,400])
    for a in (-1,1):
        for b in (-1,1): rows.append(f'{lat+a/480},{lon+b/480},{next(values)}')
    raw=('\n'.join(rows)+'\n').encode()
    height,spread,_=point_height(raw,lat,lon)
    assert height==250 and spread==300
    with pytest.raises(ValueError,match='registration'):
        point_height(raw,lat+1,lon)
