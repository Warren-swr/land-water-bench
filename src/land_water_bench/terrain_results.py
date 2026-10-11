"""Audit terrain pilot evidence and build its independent static comparison page."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

from .core import atomic_json, digest, make_dataset, read_jsonl
from .results import require, scores_agree, seal
from .terrain import TOLERANCES, metrics, project_answer, validate_dataset


def verify(run):
    dataset = validate_dataset(json.loads((run / 'dataset.json').read_text()))
    manifest = json.loads((run / 'manifest.json').read_text())
    config = json.loads((run / 'config.json').read_text())
    summary = json.loads((run / 'summary.json').read_text())
    require(summary['state'] == 'complete', 'Run is not complete')
    require(not config['limit'], 'Build from the complete frozen pilot dataset')
    require(digest((run / 'dataset.json').read_bytes()) == manifest['dataset_sha256'], 'Dataset hash mismatch')
    for name, checksum in manifest['execution_source_sha256'].items():
        require(digest((run / 'source' / name).read_bytes()) == checksum, 'Execution snapshot mismatch')
    points = dataset['points']
    output = []
    for model in summary['models']:
        raw = read_jsonl(run / (model['name'] + '.jsonl'))
        records = {r['sample_id']: r for r in raw}
        require(len(raw) == len(records) == len(points) and set(records) == {p['id'] for p in points}, 'Incomplete or duplicate records')
        for point in points:
            row = records[point['id']]
            request = row['request']
            require(not any(k in request for k in ('tools', 'system', 'instructions', 'previous_response_id')), 'Agent context supplied')
            messages = request.get('input', request.get('messages'))
            require(messages == [{'role':'user','content':point['prompt']}], 'Frozen prompt differs from actual request')
            require(request['model'] == model['requested_model'], 'Requested model mismatch')
            if row['status'] != 'infra_error':
                parsed = project_answer(row, model['wire'], version=config['answer_parser'])
                require(all(row[k] == v for k,v in parsed.items()), 'Answer extraction mismatch')
        score = metrics(points, records)
        require(scores_agree(score, model['metrics']), 'Saved scores differ from recomputed scores')
        included = [p for p in points if records[p['id']]['status'] != 'infra_error']
        numeric = [p for p in included if records[p['id']]['status'] == 'ok']
        require(bool(included), 'No scoreable responses')
        for tolerance in TOLERANCES:
            correct = sum(records[p['id']]['label'] == p['truth'] and
                          abs(records[p['id']]['prediction']['elevation_m'] - p['elevation_m']) <= tolerance for p in numeric)
            require(math.isclose(score['joint_accuracy'][str(tolerance)], correct / len(included), abs_tol=1e-12), 'Independent joint score disagrees')
        if numeric:
            mae = math.fsum(abs(records[p['id']]['prediction']['elevation_m']-p['elevation_m']) for p in numeric)/len(numeric)
            require(math.isclose(score['elevation_mae_m'],mae,abs_tol=1e-9), 'Independent height error disagrees')
        safe = {str(i): {k:r[k] for k in ('sample_id','status','label','prediction','text','complete_response','extraction','attempts','latency_seconds','error') if k in r}
                for i,r in records.items()}
        output.append({'name':model['name'], 'metrics':score, 'records':safe,
                       'settings': {k:model[k] for k in ('wire','reasoning','max_output_tokens','temperature')},
                       'attempts':model['attempts']})
    return dataset, summary, sorted(output,key=lambda m:(-m['metrics']['joint_accuracy']['500'],m['name']))


def report(data, summary, models):
    pct = lambda value: f'{value*100:.2f}%' if value is not None else '—'
    metre = lambda value: f'{value:,.1f}' if value is not None else '—'
    lines = ['# Terrain / Elevation Pilot', '',
        f"Run: `{summary['run_id']}`。输入为经纬度，输出 `type: Solid | Water` 和 `elevation_m`。",
        '', '**本轮为 512 题分层试验，不能与原全球面积加权榜单直接比较。**', '',
        '| 模型 | 联合 ±500 m | ±100 m | ±1,000 m | 分类准确率 | 高程 MAE (m) | 陆地 MAE (m) | 海洋 MAE (m) | 无效 | 未决 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for m in models:
        s=m['metrics']
        lines.append(f"| {m['name']} | {pct(s['joint_accuracy']['500'])} | {pct(s['joint_accuracy']['100'])} | {pct(s['joint_accuracy']['1000'])} | {pct(s['classification_accuracy'])} | {metre(s['elevation_mae_m'])} | {metre(s['by_type']['Solid']['elevation_mae_m'])} | {metre(s['by_type']['Water']['elevation_mae_m'])} | {s['invalid']} | {s['infra_errors']} |")
    lines += ['', '## 题目与参考', '',
        '- 同一批坐标、每模型每坐标一次独立请求。仅有 user prompt，无 Agent、工具、图像、地图、系统提示词或对话历史。',
        '- 输入来自原 2° 中心网格，纬度限制为 −59° 至 +59°。海陆分类取 GSHHG 2.3.7；已知分类参考冲突点、湖泊、极区和近零高程点在运行前排除。',
        '- 陆地四档：<200、200–1,000、1,000–2,500、≥2,500 m。海洋按水深四档：<500、500–2,500、2,500–4,500、≥4,500 m。每档 64 题，题目等权，不做全球面积加权。',
        '- 高程取 [NOAA ETOPO 2022](https://www.ncei.noaa.gov/products/etopo-global-relief-model) 15 角秒栅格，EGM2008 基准。整数经纬度对应四个相邻像元的双线性插值；逐点保留源 URL、原始 CSV 和 SHA-256。',
        '- 陆地输出裸地高程；海洋输出海床高程，水下为负值。参考是固定数据产品，测量误差与栅格分辨率仍有限制；100 m 容差表示相对该栅格的误差，不代表物理世界中 100 m 精度已获证明。',
        f"- 冻结数据集 SHA-256：`{summary['dataset_sha256']}`。", '',
        '## 评分与抽取', '',
        '- 联合准确率 = 类型正确且绝对高程误差不超过容差的题数 / 可评分题数。主排序容差在运行前固定为 500 m，同时报告 100 和 1,000 m。',
        '- 允许正文解释或代码围栏中出现一个完整答案 JSON。多个冲突答案、重复键、单位字符串、范围、空值、非有限数值、缺字段和输出截断均无效；不读取 thinking / reasoning 块。',
        '- 无效回答计错并保留在准确率分母。未决 API 请求按既定规则从分子和分母剔除，并报告数量与覆盖率；尚未返回的 pending 请求不生成最终分数。',
        '- MAE / 中位数 / P90 仅统计有效数值，包含类型错误的回答，必须与数值覆盖率一起看。类型错误但高度接近的题不会通过联合评分。', '',
        '## 运行与验证', '',
        f"- 每模型并发上限 {summary['config']['concurrency']}；限流等可重试错误最多 {summary['config']['max_retries']} 次；读取超时最多重试 {summary['config']['max_timeout_retries']} 次。",
        f"- 首次请求超时 {summary['config']['timeout_seconds']} 秒，读取超时后为两倍；输出上限 65,536 tokens。",
        '- Responses 模型 reasoning effort=low；Messages 模型 thinking budget=1,024。沿用已有接入设置，不属于相同推理预算的比较。模型名为接入网关中的实验别名，未独立核验底层提供方身份。',
        '- 独立核对所有实际请求的题目、工具缺失、完整性、答案抽取、联合准确率和 MAE；原始运行记录与代码快照保留在 run 目录。',
        '- 本轮为单次采样的小规模试验，细小分差不能证明稳定的模型优劣。三维点云只展示实际采样点，高程放大约 160 倍；没有对未测区域生成地形。', '',
        '## 各高程档联合准确率（±500 m）', '',
        '| 档位 | '+' | '.join(m['name'] for m in models)+' |',
        '|---|'+'---:|'*len(models)]
    for name in models[0]['metrics']['by_stratum']:
        lines.append('| '+name+' | '+' | '.join(pct(m['metrics']['by_stratum'][name]['joint_accuracy']['500']) for m in models)+' |')
    return '\n'.join(lines)+'\n'


def build(run, output, assets, freeze=False):
    require(not output.exists(), 'Use a new output directory')
    dataset, summary, models = verify(run)
    verification={'passed':True,'run_id':summary['run_id'],'models':[m['name'] for m in models],
                  'records':sum(m['metrics']['expected'] for m in models),'independent_joint_score_and_mae':True,
                  'actual_prompts_and_no_tools_verified':True,'new_model_requests':0}
    atomic_json(run/'terrain-verification.json',verification)
    output.mkdir(parents=True)
    for name in ('index.html','style.css','app.js'):
        shutil.copy2(assets/name,output/name)
    for name in ('display.ttf','FONT-LICENSE.txt'):
        shutil.copy2(assets.parent/name,output/name)
    root=assets.resolve().parent.parent
    for source,target in [('THIRD_PARTY_NOTICES.md','THIRD_PARTY_NOTICES.md'),('licenses/LGPL-3.0.txt','GSHHG-LICENSE.txt'),('licenses/GPL-3.0.txt','GSHHG-GPL.txt')]:
        shutil.copy2(root/source,output/target)
    points=[{k:p[k] for k in ('id','source_grid_id','lat','lon','truth','stratum','elevation_m','native_cell_spread_m','prompt')} for p in dataset['points']]
    payload={'run_id':run.name,'dataset_sha256':summary['dataset_sha256'],'points':points,'models':models,
             'reference':dataset['height_reference'],'land_background':[[p['lat'],p['lon']] for p in make_dataset()['points'] if p['truth']],
             'verification':verification}
    atomic_json(output/'data.json',payload)
    (output/'RESULTS.md').write_text(report(dataset,summary,models))
    seal(output)
    if freeze:
        seal(run)
    return verification


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--assets',type=Path,default=Path('web/terrain'))
    p.add_argument('--seal',action='store_true')
    args=p.parse_args()
    print(json.dumps(build(args.run_dir,args.output,args.assets,args.seal),indent=2))


if __name__=='__main__':
    main()
