'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const pct = n => n == null ? '—' : (100 * n).toFixed(2) + '%';
  const meters = n => n == null ? '—' : Math.round(n).toLocaleString('en-US') + ' m';
  const colors = {solid:'#ba782b',water:'#4c68d7',error:'#d26487',muted:'#939bb2',grid:'#dce1ef',ink:'#24263c'};
  const strataNames = {solid_low:['低地','< 200 m'],solid_mid:['丘陵 / 中低地','200–1,000 m'],solid_high:['高地','1,000–2,500 m'],solid_alpine:['高山 / 高原 / 冰原','≥ 2,500 m'],water_shelf:['浅海','水深 < 500 m'],water_slope:['中等水深','500–2,500 m'],water_deep:['深海','2,500–4,500 m'],water_abyssal:['更深海域','≥ 4,500 m']};
  const displayNames = {'gpt-6-astra':'GPT-6 Astra','gpt-5.6-sol':'GPT-5.6 Sol','gpt-5.6-terra':'GPT-5.6 Terra','claude-opus-5-5':'Claude Opus 5.5','claude-sonnet-5-5':'Claude Sonnet 5.5','claude-haiku-5-5':'Claude Haiku 5.5','kimi-k3':'Kimi K3','mimo-v2.6-pro':'MiMo V2.6 Pro','qwen3.8-max':'Qwen 3.8 Max','deepseek-v4.1-flash':'DeepSeek V4.1 Flash','step-5-preview':'Step 5 Preview','seed-2.1':'Seed 2.1'};
  const statusNames = {ok:'有效回答',invalid:'无效回答',infra_error:'请求未决'};
  const nameOf = m => displayNames[m.name] || m.name;
  let metadata, reference, model = null, records = null, classificationRecords = null;
  let selected = 6596, yaw = -100, pitch = 20, hits = {}, redraw = false, loading = 0;
  let terrainError = null, classificationError = null;
  const cache = new Map();
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }
  async function json(path) {
    const response = await fetch(path, {cache:'no-store'});
    if (!response.ok) throw new Error('结果读取失败（HTTP ' + response.status + '），请刷新重试。');
    return response.json();
  }
  async function loadRecords(path) {
    if (cache.has(path)) return cache.get(path);
    const rows = await json(path);
    cache.set(path, rows);
    if (cache.size > 6) cache.delete(cache.keys().next().value);
    return rows;
  }

  function canvas(id) { const c=$(id),dpr=window.devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);const ctx=c.getContext('2d');ctx.scale(dpr,dpr);ctx.font='11px Arial';return {ctx,w,h}; }
  function project(lat,lon,r=1) { const a=(lon+yaw)*Math.PI/180,b=lat*Math.PI/180,t=pitch*Math.PI/180,x=Math.cos(b)*Math.sin(a),y=Math.sin(b),z=Math.cos(b)*Math.cos(a);return [r*x,r*(y*Math.cos(t)-z*Math.sin(t)),r*(y*Math.sin(t)+z*Math.cos(t))]; }
  function globe(id,predicted) {
    const {ctx,w,h}=canvas(id),cx=w/2,cy=h/2,R=Math.min(w*.39,h*.40);hits[id]=[];
    ctx.beginPath();ctx.arc(cx,cy,R,0,Math.PI*2);ctx.fillStyle='#eef1f8';ctx.fill();ctx.strokeStyle=colors.grid;ctx.lineWidth=.7;
    for(let lat=-60;lat<=60;lat+=30){ctx.beginPath();let on=false;for(let lon=-180;lon<=180;lon+=3){const [x,y,z]=project(lat,lon);if(z>=0){if(on)ctx.lineTo(cx+x*R,cy-y*R);else ctx.moveTo(cx+x*R,cy-y*R);on=true;}else on=false;}ctx.stroke();}
    for(let lon=-180;lon<180;lon+=30){ctx.beginPath();let on=false;for(let lat=-90;lat<=90;lat+=3){const [x,y,z]=project(lat,lon);if(z>=0){if(on)ctx.lineTo(cx+x*R,cy-y*R);else ctx.moveTo(cx+x*R,cy-y*R);on=true;}else on=false;}ctx.stroke();}
    ctx.fillStyle='#c6cddd';for(const p of reference.points){if(!p.truth)continue;const [x,y,z]=project(p.lat,p.lon);if(z>0){ctx.beginPath();ctx.arc(cx+x*R,cy-y*R,Math.max(.6,R*.008),0,Math.PI*2);ctx.fill();}}
    if(predicted&&!records){ctx.fillStyle=colors.muted;ctx.textAlign='center';ctx.fillText(terrainError?'高程结果读取失败，请刷新重试':'读取已保存的高程预测…',cx,cy);return;}
    const dots=[];
    for(const p of reference.points){const row=records?.[p.id],e=predicted?row?.prediction?.elevation_m:p.elevation_m;if(e==null)continue;const [x,y,z]=project(p.lat,p.lon,1+Math.max(-10000,Math.min(10000,e))/40000);if(z<=0)continue;const [sx,sy]=project(p.lat,p.lon);const wrong=predicted&&(row.label!==p.truth||(p.elevation_m!=null&&Math.abs(e-p.elevation_m)>500));dots.push({id:p.id,x:cx+x*R,y:cy-y*R,z,sx:cx+sx*R,sy:cy-sy*R,color:wrong?colors.error:(predicted?row.label:p.truth)?colors.solid:colors.water});}
    dots.sort((a,b)=>a.z-b.z);
    for(const d of dots){if(d.id===selected){ctx.strokeStyle=d.color;ctx.globalAlpha=.45;ctx.beginPath();ctx.moveTo(d.sx,d.sy);ctx.lineTo(d.x,d.y);ctx.stroke();ctx.globalAlpha=1;}ctx.fillStyle=d.color;ctx.beginPath();ctx.arc(d.x,d.y,d.id===selected?4:1.1,0,Math.PI*2);ctx.fill();hits[id].push(d);}
    const chosen=dots.find(d=>d.id===selected);if(chosen){ctx.strokeStyle=colors.ink;ctx.lineWidth=1.3;ctx.beginPath();ctx.arc(chosen.x,chosen.y,7,0,Math.PI*2);ctx.stroke();}
  }
  function scatter() {
    const {ctx,w,h}=canvas('scatter'),left=64,right=w-20,top=24,bottom=h-42;hits.scatter=[];
    let lo=-10000,hi=8000;const data=[];
    if(records)for(const p of reference.points){const r=records[p.id];if(p.elevation_m==null||r?.status!=='ok')continue;const e=r.prediction.elevation_m;lo=Math.min(lo,p.elevation_m,e);hi=Math.max(hi,p.elevation_m,e);data.push([p,r,e]);}
    const span=hi-lo,step=10**Math.floor(Math.log10(span/4)),tick=step*(span/4/step>5?10:span/4/step>2?5:2);
    lo=Math.floor(lo/tick)*tick;hi=Math.ceil(hi/tick)*tick;
    const x=v=>left+(v-lo)/(hi-lo)*(right-left),y=v=>bottom-(v-lo)/(hi-lo)*(bottom-top),fmt=v=>Math.abs(v)>=1e6?v.toExponential(0):Math.abs(v)>=1000?(v/1000)+'k':String(v);
    ctx.strokeStyle=colors.grid;ctx.fillStyle=colors.muted;
    for(let t=lo;t<=hi+tick*.01;t+=tick){ctx.beginPath();ctx.moveTo(x(t),top);ctx.lineTo(x(t),bottom);ctx.moveTo(left,y(t));ctx.lineTo(right,y(t));ctx.stroke();ctx.textAlign='center';ctx.fillText(fmt(t),x(t),bottom+18);ctx.textAlign='right';ctx.fillText(fmt(t),left-8,y(t)+4);}
    ctx.strokeStyle=colors.muted;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x(lo),y(lo));ctx.lineTo(x(hi),y(hi));ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=colors.ink;ctx.textAlign='left';ctx.fillText('模型高程（m）',left,12);ctx.textAlign='right';ctx.fillText('参考高程（m）',right,h-4);
    for(const [p,r,e] of data){const px=x(p.elevation_m),py=y(e);ctx.fillStyle=Math.abs(e-p.elevation_m)>500||r.label!==p.truth?colors.error:colors.water;ctx.globalAlpha=p.id===selected?1:.22;ctx.beginPath();ctx.arc(px,py,p.id===selected?4:1.5,0,Math.PI*2);ctx.fill();hits.scatter.push({id:p.id,x:px,y:py});}ctx.globalAlpha=1;
  }
  function explainReason(reason) { return (reason||'').split(';').map(s=>({unsupported_lake:'湖底高程参考未验收',unsupported_river_lake:'河湖参考未验收',unsupported_island_in_lake:'嵌套岛屿参考未验收',unsupported_floating_ice_shelf:'冰架下海床参考未验收',classification_reference_disagreement_or_boundary:'参考分类冲突 / 边界点',ocean_bed_vs_surface_height_conflict:'海洋类型与高程冲突'}[s]||s)).join('；'); }
  function answerStatus(row) { return row?.error_type === 'content_filter' ? '请求未决 · 内容过滤' : statusNames[row?.status]; }

  function details() {
    const p = reference.points[selected], r = records?.[selected], c = classificationRecords?.[selected];
    const kind = p.truth ? 'Solid' : 'Water';
    $('point-values').textContent = '参考 ' + kind + ' · ' + (p.elevation_m == null ? '高程不可评分' : meters(p.elevation_m)) +
      ' → 预测 ' + (r?.prediction?.type || statusNames[r?.status] || '读取中') + ' · ' + meters(r?.prediction?.elevation_m);
    $('point-error').textContent = '#' + selected + ' · ' + p.lat + '°, ' + p.lon + '° · ' +
      (p.elevation_m == null ? explainReason(p.height_reference_reason) :
        r?.prediction ? '绝对误差 ' + meters(Math.abs(r.prediction.elevation_m - p.elevation_m)) :
        answerStatus(r) || '读取回答中');
    $('prompt').textContent = reference.prompt_template.replace('{lat:.1f}', p.lat.toFixed(1)).replace('{lon:.1f}', p.lon.toFixed(1));
    $('answer').textContent = terrainError ?? r?.text ?? (r?.status === 'infra_error' ? '请求未决；无可用模型回答。' : '读取回答中…');
    $('classification-label').textContent = c?.status === 'ok' ? (c.label ? 'Solid' : 'Water') : statusNames[c?.status] || '';
    $('classification-point-note').textContent = model?.classification ?
      '独立分类任务 · ' + (model.classification.source_model || model.name) + ' · 同一坐标 · 参考 ' + kind + (c ? ' · ' + answerStatus(c) : '') :
      '此模型没有独立分类任务结果。';
    $('classification-answer').textContent = classificationError ?? c?.text ??
      (model?.classification ? c?.status === 'infra_error' ? '请求未决；无可用模型回答。' : '读取回答中…' : '—');
    $('point').value = String(selected);
    $('previous').disabled = selected === 0;
    $('next').disabled = selected === reference.points.length - 1;
  }
  function draw() {
    if (!reference) return;
    globe('reference', false);
    globe('prediction', true);
    scatter();
    details();
  }
  function queueDraw() {
    if (redraw) return;
    redraw = true;
    requestAnimationFrame(() => { redraw = false; draw(); });
  }
  function updateStrata() {
    $('strata').replaceChildren();
    for (const [key, [name, range]] of Object.entries(strataNames)) {
      const s = model?.terrain?.metrics.by_stratum?.[key], value = s?.joint_accuracy?.['500'];
      const card = el('div', 'stratum'), track = el('div', 'bar-track'), bar = el('div', 'bar');
      bar.style.width = ((value || 0) * 100) + '%';
      track.append(bar);
      card.append(el('div', 'stratum-name', name), el('div', 'stratum-range', range + ' · ' + (s?.expected ?? '—') + ' 点'),
        track, el('div', 'bar-value', pct(value)));
      $('strata').append(card);
    }
  }
  function updateSummary() {
    const s = model.terrain.metrics;
    $('model-summary').replaceChildren();
    const metrics = [
      ['更严格 · 联合 ±100 m', pct(s.joint_accuracy['100']), '分类与高程同时正确'],
      ['更宽松 · 联合 ±1,000 m', pct(s.joint_accuracy['1000']), '分类与高程同时正确'],
      ['联合任务 · 只看分类', pct(s.classification_accuracy), '来自同一次高程预测回答'],
      ['联合任务 · 无效 / 未决', s.invalid + ' / ' + s.infra_errors, '未决剔除，覆盖率单列']
    ];
    for (const [label, value, note] of metrics) {
      const stat = el('div', 'summary-stat');
      stat.append(el('span', '', label), el('strong', '', value), el('small', '', note));
      $('model-summary').append(stat);
    }
  }
  async function setModel(name) {
    const chosen = metadata.models.find(m => m.name === name && m.terrain);
    if (!chosen) return;
    const token = ++loading;
    model = chosen;
    records = classificationRecords = null;
    terrainError = classificationError = null;
    $('prediction').setAttribute('aria-busy', 'true');
    $('model').value = name;
    $('selected-model').textContent = nameOf(chosen);
    $('selected-model').dataset.model = name;
    document.querySelectorAll('#ranking tr').forEach(row => row.classList.toggle('row-selected', row.dataset.model === name));
    updateSummary();
    updateStrata();
    queueDraw();
    const results = await Promise.allSettled([
      loadRecords(chosen.terrain.details_url),
      chosen.classification ? loadRecords(chosen.classification.details_url) : Promise.resolve(null)
    ]);
    if (token !== loading) return;
    if (results[0].status === 'fulfilled') records = results[0].value;
    else terrainError = results[0].reason.message;
    if (results[1].status === 'fulfilled') classificationRecords = results[1].value;
    else classificationError = results[1].reason.message;
    draw();
    $('prediction').setAttribute('aria-busy', 'false');
  }
  function scoreCell(value) {
    const cell = el('td', 'primary-col');
    if (value == null) { cell.append(el('span', 'missing', '—')); return cell; }
    const score = el('div', 'score', (value * 100).toFixed(2));
    score.append(el('small', '', '%'));
    const track = el('div', 'score-track'), fill = el('div', 'score-fill');
    fill.style.width = (value * 100) + '%';
    track.append(fill);
    cell.append(score, track);
    return cell;
  }
  function paintStatus() {
    const r = metadata.recovery;
    $('load-status').textContent = r && r.state !== 'complete' ?
      '未决重试 ' + r.completed.toLocaleString('en-US') + ' / ' + r.selected.toLocaleString('en-US') +
      ' · 已恢复有效 ' + r.recovered_valid.toLocaleString('en-US') + ' 条 · 当前成绩为重试前快照' :
      '两项任务均已完成 · 按联合准确率排序';
  }
  function paintMetadata(preferredName) {
    $('ranking').replaceChildren();
    let rank = 0;
    for (const m of metadata.models) {
      const s = m.terrain?.metrics, c = m.classification?.metrics;
      const row = el('tr', s ? '' : 'only-classification');
      row.dataset.model = m.name;
      const td = el('td'), identity = el('div', 'model-cell');
      const family = m.name.split('-')[0], badge = {claude:'C',seed:'S',gpt:'G',kimi:'K',mimo:'M',qwen:'Q',step:'S',deepseek:'D'}[family] || '·';
      identity.append(el('span', 'rank', s ? String(++rank).padStart(2, '0') : '—'), el('span', 'family-icon ' + family, badge));
      const title = el('div'), modelName = el(s ? 'button' : 'span', 'model-name' + (s ? ' model-button' : ''), nameOf(m));
      if (s) {
        modelName.setAttribute('aria-label', '查看 ' + nameOf(m) + ' 的联合任务');
        modelName.addEventListener('click', () => {
          setModel(m.name);
          $('explore').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
        });
      }
      title.append(modelName, el('span', 'model-alias', s ? m.name : m.name + ' · 仅分类任务'));
      identity.append(title); td.append(identity); row.append(td);
      row.append(scoreCell(s?.joint_accuracy['500']));
      const mae = el('td');
      if (s?.elevation_mae_m != null) {
        mae.append(el('span', 'metric', Math.round(s.elevation_mae_m).toLocaleString('en-US')), el('span', 'metric-unit', ' m'));
      } else mae.append(el('span', 'missing', '—'));
      row.append(mae);
      const classification = el('td', 'classification-col');
      classification.textContent = pct(c?.weighted_accuracy);
      classification.dataset.task = 'classification';
      classification.title = c ? '独立分类任务 · 覆盖率 ' + pct(c.coverage) + ' · 无效 ' + c.invalid + ' · 未决 ' + c.infra_errors : '此模型版本未运行独立分类任务';
      row.append(classification);
      const coverage = el('td', s && s.coverage < .95 ? 'coverage-low' : '');
      if (s) {
        coverage.append(el('span', 'coverage-main', pct(s.coverage)),
          el('span', 'coverage-note', s.invalid + ' 无效 · ' + s.infra_errors + ' 未决'));
      } else coverage.append(el('span', 'missing', '—'));
      row.append(coverage);
      $('ranking').append(row);
    }
    for (const [id, task] of [['terrain-count','terrain'], ['classification-count','classification']]) {
      $(id).replaceChildren(document.createTextNode(metadata.sources[task].model_count), el('small', '', ' models'));
    }
    paintStatus();
    $('reference-coverage').textContent = '完整请求 16,200 个坐标；其中 ' + metadata.height_scorable.toLocaleString('en-US') +
      ' 点可评分高程，另 ' + metadata.height_unscorable + ' 点仅参与分类评分。';
    const onlyTerrain = metadata.models.filter(m => m.terrain && !m.classification);
    const onlyClassification = metadata.models.filter(m => !m.terrain && m.classification);
    const notes = [
      onlyTerrain.length ? onlyTerrain.map(nameOf).join('、') + ' 仅有联合任务结果。' : '',
      onlyClassification.length ? onlyClassification.map(nameOf).join('、') + ' 仅有分类任务结果。' : ''
    ].filter(Boolean);
    for (const identity of metadata.model_aliases || []) {
      const m = metadata.models.find(item => item.name === identity.name);
      if (m?.terrain && m?.classification) notes.push(nameOf(m) + ' 的 ' +
        m.terrain.source_model + ' 与 ' + m.classification.source_model + ' 为同一模型，已合并展示。');
    }
    $('version-note').textContent = notes.join(' ');
    $('version-note').hidden = !notes.length;
    $('extraction-note').textContent = Object.values(metadata.sources).every(s => s.reextraction) ?
      '已按新版答案抽取规则重算：兼容解释正文、公式、重复答案与明确更正。内容过滤统一归为未决；格式错误、空回答和输出截断仍计为无效。' :
      '本页沿用冻结成绩。联合任务的公式大括号抽取、重复答案判定和内容过滤归类问题仍包含在该结果快照中。';
    $('source-notes').textContent = '联合任务：' + metadata.sources.terrain.run_id + '；仅分类任务：' +
      metadata.sources.classification.run_id + '。完整来源和指标见页尾「结果与口径」。';
    const available = metadata.models.filter(m => m.terrain);
    $('model').replaceChildren();
    for (const m of available) $('model').add(new Option(nameOf(m), m.name));
    $('model').disabled = !available.length;
    if (available.length) setModel(available.find(m => m.name === preferredName)?.name || available[0].name);
  }

  async function refreshRecovery() {
    if (!metadata) return;
    try {
      const fresh = await json('data.json');
      if (fresh.sources.terrain.dataset_sha256 !== reference.dataset_sha256) throw new Error('参考版本变化，请刷新页面。');
      if (fresh.run_id !== metadata.run_id || fresh.built_at !== metadata.built_at) {
        const current = model?.name;
        metadata = fresh;
        cache.clear();
        paintMetadata(current);
      } else {
        metadata.recovery = fresh.recovery;
        paintStatus();
      }
    } catch (error) { $('load-status').textContent = error.message; }
  }

  function pick(id,event){const r=$(id).getBoundingClientRect(),x=event.clientX-r.left,y=event.clientY-r.top;let best=null,dist=14;for(const p of hits[id]||[]){const d=Math.hypot(p.x-x,p.y-y);if(d<dist){best=p;dist=d;}}if(best){selected=best.id;queueDraw();}}
  for(const id of ['reference','prediction']){let first,last,moved;$(id).addEventListener('pointerdown',e=>{first=last={x:e.clientX,y:e.clientY};moved=false;$(id).setPointerCapture(e.pointerId);});$(id).addEventListener('pointermove',e=>{if(!last)return;moved=moved||Math.hypot(e.clientX-first.x,e.clientY-first.y)>4;yaw+=(e.clientX-last.x)*.45;pitch=Math.max(-80,Math.min(80,pitch-(e.clientY-last.y)*.35));last={x:e.clientX,y:e.clientY};queueDraw();});$(id).addEventListener('pointerup',e=>{if(!moved)pick(id,e);last=null;});$(id).addEventListener('pointercancel',()=>last=null);}
  $('scatter').addEventListener('click',e=>pick('scatter',e));$('model').addEventListener('change',()=>setModel($('model').value));$('point').addEventListener('change',()=>{selected=Math.max(0,Math.min(16199,Math.round(Number($('point').value)||0)));const p=reference.points[selected];yaw=-p.lon;pitch=Math.max(-80,Math.min(80,p.lat));queueDraw();});$('previous').addEventListener('click',()=>{selected=Math.max(0,selected-1);queueDraw();});$('next').addEventListener('click',()=>{selected=Math.min(16199,selected+1);queueDraw();});

  Promise.all([json('data.json'), json('reference.details.json')]).then(([data, ref]) => {
    if (data.schema !== 'land-water-unified/1.0' || data.sources.terrain.dataset_sha256 !== ref.dataset_sha256) {
      throw new Error('结果与参考版本不一致。');
    }
    metadata = data;
    reference = ref;
    paintMetadata();
    queueDraw();
    new ResizeObserver(queueDraw).observe($('reference'));
    new ResizeObserver(queueDraw).observe($('scatter'));
    setInterval(refreshRecovery, 15000);
  }).catch(error => {
    $('load-status').textContent = error.message;
    $('load-status').setAttribute('role', 'alert');
  });
})();
