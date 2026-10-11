'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const pct = n => n == null ? '—' : (100 * n).toFixed(2) + '%';
  const meters = n => n == null ? '—' : Math.round(n).toLocaleString('en-US') + ' m';
  const colors = {solid:'#596cd8',water:'#edf1fa',error:'#cb6789',invalid:'#b08536',unresolved:'#9674b1',masked:'#b5bdcc',correct:'#becbe2',muted:'#647086',grid:'#dce2ed',ink:'#192133'};
  const strataNames = {solid_low:['低地','< 200 m'],solid_mid:['丘陵 / 中低地','200–1,000 m'],solid_high:['高地','1,000–2,500 m'],solid_alpine:['高山 / 高原 / 冰原','≥ 2,500 m'],water_shelf:['浅海','水深 < 500 m'],water_slope:['中等水深','500–2,500 m'],water_deep:['深海','2,500–4,500 m'],water_abyssal:['更深海域','≥ 4,500 m']};
  const displayNames = {'gpt-6-astra':'GPT-6 Astra','gpt-5.6-sol':'GPT-5.6 Sol','gpt-5.6-terra':'GPT-5.6 Terra','claude-opus-5-5':'Claude Opus 5.5','claude-sonnet-5-5':'Claude Sonnet 5.5','claude-haiku-5-5':'Claude Haiku 5.5','kimi-k3':'Kimi K3','mimo-v2.6-pro':'MiMo V2.6 Pro','qwen3.8-max':'Qwen 3.8 Max','deepseek-v4.1-flash':'DeepSeek V4.1 Flash','step-5-preview':'Step 5 Preview','seed-2.1':'Seed 2.1'};
  const statusNames = {ok:'有效回答',invalid:'无效回答',infra_error:'请求未决'};
  const nameOf = m => displayNames[m.name] || m.name;
  let metadata, reference, referenceRows, model = null, records = null;
  let selected = 6596, yaw = -100, pitch = 20, hits = {}, redraw = false;
  const panels = Object.fromEntries(['a','b'].map(key => [key, {
    name:'reference', model:null, rows:null, classRows:null, error:null, classError:null, busy:false, token:0
  }]));
  let pair = null;
  let family = 'all', search = '', view = 'map', layer = 'elevation';
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let motion = !reduceMotion.matches, heroVisible = false, heroFrame = 0, heroTime = 0, heroYaw = 12;
  const rasters = new Map();
  const cache = new Map();
  const pending = new Map();
  const panelName = panel => panel.model ? nameOf(panel.model) : '全球参考';
  const panelRow = (panel, p) => panel.model ? panel.rows?.[p.id] : referenceRows?.[p.id];
  const ready = panel => !panel.model || Boolean(panel.rows);
  const focusPanel = () => panels.b.model ? panels.b : panels.a.model ? panels.a : null;
  const differenceLayer = () => ['class-diff','height-diff'].includes(layer);
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }
  async function json(path) {
    let response;
    try { response = await fetch(path,{cache:'no-store'}); }
    catch { throw new Error('结果读取失败，请刷新重试。'); }
    if (!response.ok) throw new Error('结果读取失败（HTTP ' + response.status + '），请刷新重试。');
    return response.json();
  }
  async function loadRecords(path) {
    if (cache.has(path)) return cache.get(path);
    if (!pending.has(path)) pending.set(path, json(path).then(rows => {
      cache.set(path, rows);
      if (cache.size > 8) cache.delete(cache.keys().next().value);
      return rows;
    }).finally(() => pending.delete(path)));
    return pending.get(path);
  }

  function canvas(id) { const c=$(id),dpr=window.devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);const ctx=c.getContext('2d');ctx.scale(dpr,dpr);ctx.font='11px Arial';return {ctx,w,h}; }
  function project(lat,lon,r=1,rotation=yaw,tilt=pitch) { const a=(lon+rotation)*Math.PI/180,b=lat*Math.PI/180,t=tilt*Math.PI/180,x=Math.cos(b)*Math.sin(a),y=Math.sin(b),z=Math.cos(b)*Math.cos(a);return [r*x,r*(y*Math.cos(t)-z*Math.sin(t)),r*(y*Math.sin(t)+z*Math.cos(t))]; }
  const heightStops = [[-10000,[39,67,157]],[-4000,[98,142,201]],[0,[224,233,246]],[200,[233,197,162]],[2500,[168,98,67]],[6000,[100,73,130]]];
  function heightColor(height) {
    if (height == null) return colors.masked;
    const value = Math.max(-10000, Math.min(6000, height));
    const upper = heightStops.findIndex(([stop]) => stop >= value);
    if (!upper) return 'rgb(' + heightStops[0][1].join(',') + ')';
    const [low, a] = heightStops[upper - 1], [high, b] = heightStops[upper], t = (value - low) / (high - low);
    return 'rgb(' + a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(',') + ')';
  }
  function wrong(p, row) {
    return row?.status === 'invalid' || (row?.status === 'ok' &&
      (row.label !== p.truth || Math.abs(row.prediction.elevation_m - p.elevation_m) > 500));
  }
  function comparable(a, b, height = false) {
    return a?.status === 'ok' && b?.status === 'ok' && (!height ||
      (Number.isFinite(a.prediction.elevation_m) && Number.isFinite(b.prediction.elevation_m)));
  }
  function deltaColor(delta) {
    const t = Math.min(1,Math.abs(delta) / 2000), neutral = [234,238,247];
    const end = delta < 0 ? [57,85,213] : [184,101,55];
    return 'rgb(' + neutral.map((v,i) => Math.round(v + (end[i] - v) * t)).join(',') + ')';
  }
  function pointColor(p, panel) {
    const row = panelRow(panel,p);
    if (differenceLayer()) {
      const a = panelRow(panels.a,p), b = panelRow(panels.b,p);
      if (a?.status === 'infra_error' || b?.status === 'infra_error') return colors.unresolved;
      if (a?.status === 'invalid' || b?.status === 'invalid') return colors.invalid;
      if (!comparable(a,b,layer === 'height-diff')) return colors.masked;
      if (layer === 'class-diff') return a.label === b.label ? colors.correct : row.label ? '#b86537' : '#3955d5';
      const delta = b.prediction.elevation_m - a.prediction.elevation_m;
      return deltaColor(panel === panels.a ? -delta : delta);
    }
    if (row?.status !== 'ok') return row?.status === 'invalid' ? colors.invalid : colors.unresolved;
    if (layer === 'classification') return row.label ? colors.solid : colors.water;
    if (p.elevation_m == null) return colors.masked;
    if (layer === 'error') return panel.model && wrong(p,row) ? colors.error : colors.correct;
    return heightColor(row.prediction.elevation_m);
  }
  function updatePair() {
    pair = null;
    const summary = $('pair-summary'); summary.replaceChildren();
    for (const key of ['classComparable','classDifferent','heightComparable','heightDifferent','heightMean']) delete summary.dataset[key];
    $('difference-example').disabled = true;
    if (!ready(panels.a) || !ready(panels.b)) {
      summary.textContent = panels.a.error || panels.b.error || '正在读取左右两侧结果…';
      return;
    }
    pair = {classComparable:0,classIds:[],heightComparable:0,heightIds:[],heightSum:0};
    for (const p of reference.points) {
      const a = panelRow(panels.a,p), b = panelRow(panels.b,p);
      if (comparable(a,b)) {
        pair.classComparable++;
        if (a.label !== b.label) pair.classIds.push(p.id);
      }
      if (comparable(a,b,true)) {
        pair.heightComparable++;
        const delta = Math.abs(a.prediction.elevation_m - b.prediction.elevation_m);
        pair.heightSum += delta;
        if (delta > 500) pair.heightIds.push(p.id);
      }
    }
    const mean = pair.heightComparable ? pair.heightSum / pair.heightComparable : null;
    Object.assign(summary.dataset,{classComparable:pair.classComparable,classDifferent:pair.classIds.length,
      heightComparable:pair.heightComparable,heightDifferent:pair.heightIds.length,heightMean:mean ?? ''});
    for (const [label,value,total] of [['分类分歧',pair.classIds.length,pair.classComparable],['高程差 >500 m',pair.heightIds.length,pair.heightComparable]]) {
      const item = el('span');
      item.append(document.createTextNode(label + ' '),el('strong','',value.toLocaleString('en-US')),
        document.createTextNode(' / ' + total.toLocaleString('en-US') + ' 点'));
      summary.append(item);
    }
    const meanItem = el('span'); meanItem.append(document.createTextNode('平均 |高程差| '),el('strong','',meters(mean))); summary.append(meanItem);
    $('difference-example').disabled = !differenceIds().length;
  }
  function differenceIds() {
    if (!pair) return [];
    if (layer === 'class-diff' || layer === 'classification') return pair.classIds;
    if (layer === 'height-diff' || layer === 'elevation') return pair.heightIds;
    return [...new Set([...pair.classIds,...pair.heightIds])].sort((a,b) => a - b);
  }
  function heroGlobe() {
    if (!reference) return;
    const {ctx,w,h} = canvas('hero-globe'), cx = w / 2, cy = h / 2, R = Math.min(w * .36, h * .41);
    const glow = ctx.createRadialGradient(cx - R * .3, cy - R * .5, 0, cx, cy, R);
    glow.addColorStop(0, '#fbfcff'); glow.addColorStop(.8, '#eaf0fc'); glow.addColorStop(1, '#dce5f8');
    ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
    ctx.lineWidth = .65; ctx.strokeStyle = '#a7b8df70';
    for (let lat = -60; lat <= 60; lat += 30) {
      ctx.beginPath();
      for (let lon = -180; lon <= 180; lon += 3) {
        const [x,y] = project(lat, lon, 1, heroYaw, 16);
        if (lon === -180) ctx.moveTo(cx + x * R, cy - y * R); else ctx.lineTo(cx + x * R, cy - y * R);
      }
      ctx.stroke();
    }
    for (let lon = 0; lon < 180; lon += 30) {
      ctx.beginPath();
      for (let lat = -180; lat <= 180; lat += 3) {
        const [x,y] = project(lat, lon, 1, heroYaw, 16);
        if (lat === -180) ctx.moveTo(cx + x * R, cy - y * R); else ctx.lineTo(cx + x * R, cy - y * R);
      }
      ctx.stroke();
    }
    for (const p of reference.points) {
      const [x,y,z] = project(p.lat, p.lon, 1 + (p.elevation_m || 0) / 95000, heroYaw, 16);
      if (z < 0 || (!p.truth && p.id % 3)) continue;
      ctx.globalAlpha = p.truth ? .32 + z * .52 : .1 + z * .17;
      ctx.fillStyle = p.truth ? (p.elevation_m > 2500 ? '#9970ac' : '#536fc5') : '#7196d7';
      ctx.beginPath(); ctx.arc(cx + x * R, cy - y * R, Math.max(.65, R * .0052), 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1; ctx.strokeStyle = '#99add344';
    ctx.beginPath(); ctx.ellipse(cx, cy, R * 1.12, R * .30, -.34, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = '#b17b51'; ctx.beginPath(); ctx.arc(cx + R * .93, cy - R * .34, 2, 0, Math.PI * 2); ctx.fill();
  }
  function animateHero(time) {
    heroFrame = 0;
    if (!motion || !heroVisible || document.hidden) return;
    if (!heroTime || time - heroTime >= 40) {
      heroYaw += Math.min(heroTime ? time - heroTime : 0, 80) * .004;
      heroTime = time; heroGlobe();
    }
    heroFrame = requestAnimationFrame(animateHero);
  }
  function syncMotion() {
    $('motion-toggle').setAttribute('aria-pressed', String(motion));
    $('motion-toggle').setAttribute('aria-label', motion ? '暂停地球动画' : '播放地球动画');
    $('motion-toggle').textContent = motion ? 'Ⅱ' : '▶';
    cancelAnimationFrame(heroFrame); heroTime = 0;
    if (motion && heroVisible && !document.hidden) heroFrame = requestAnimationFrame(animateHero);
  }
  function mapReady(panel) {
    return ready(panel) && (!differenceLayer() || (ready(panels.a) && ready(panels.b)));
  }
  function flatMap(id, panel) {
    const {ctx,w,h} = canvas(id), key = id;
    if (!mapReady(panel)) { loadingMap(ctx,w,h,panel); return; }
    if (!rasters.has(key)) {
      const raster = document.createElement('canvas'); raster.width = 180; raster.height = 90;
      const pixels = raster.getContext('2d');
      for (const p of reference.points) {
        pixels.fillStyle = pointColor(p,panel);
        pixels.fillRect(p.id % 180, Math.floor(p.id / 180), 1, 1);
      }
      rasters.set(key, raster);
    }
    ctx.imageSmoothingEnabled = false; ctx.drawImage(rasters.get(key), 0, 0, w, h);
    ctx.strokeStyle = '#758bb52e'; ctx.lineWidth = .6; ctx.beginPath();
    for (let x = 0; x <= 180; x += 30) { ctx.moveTo(x / 180 * w,0); ctx.lineTo(x / 180 * w,h); }
    for (let y = 15; y < 90; y += 15) { ctx.moveTo(0,y / 90 * h); ctx.lineTo(w,y / 90 * h); }
    ctx.stroke();
    const x = (selected % 180 + .5) / 180 * w, y = (Math.floor(selected / 180) + .5) / 90 * h;
    ctx.strokeStyle = '#283e85'; ctx.lineWidth = 1; ctx.setLineDash([3,4]); ctx.beginPath();
    ctx.moveTo(x,0); ctx.lineTo(x,h); ctx.moveTo(0,y); ctx.lineTo(w,y); ctx.stroke(); ctx.setLineDash([]);
    ctx.lineWidth = 1.5; ctx.strokeRect(x - 5,y - 5,10,10);
  }
  function loadingMap(ctx,w,h,panel) {
    ctx.fillStyle = colors.muted; ctx.textAlign = 'center';
    ctx.fillText(panel.error || (differenceLayer() && (panels.a.error || panels.b.error)) || '读取已保存的高程预测…',w / 2,h / 2);
  }
  function globe(id,panel) {
    const {ctx,w,h}=canvas(id),cx=w/2,cy=h/2,R=Math.min(w*.39,h*.40);hits[id]=[];
    ctx.beginPath();ctx.arc(cx,cy,R,0,Math.PI*2);ctx.fillStyle='#eef1f8';ctx.fill();ctx.strokeStyle=colors.grid;ctx.lineWidth=.7;
    for(let lat=-60;lat<=60;lat+=30){ctx.beginPath();let on=false;for(let lon=-180;lon<=180;lon+=3){const [x,y,z]=project(lat,lon);if(z>=0){if(on)ctx.lineTo(cx+x*R,cy-y*R);else ctx.moveTo(cx+x*R,cy-y*R);on=true;}else on=false;}ctx.stroke();}
    for(let lon=-180;lon<180;lon+=30){ctx.beginPath();let on=false;for(let lat=-90;lat<=90;lat+=3){const [x,y,z]=project(lat,lon);if(z>=0){if(on)ctx.lineTo(cx+x*R,cy-y*R);else ctx.moveTo(cx+x*R,cy-y*R);on=true;}else on=false;}ctx.stroke();}
    ctx.fillStyle='#c6cddd';for(const p of reference.points){if(!p.truth)continue;const [x,y,z]=project(p.lat,p.lon);if(z>0){ctx.beginPath();ctx.arc(cx+x*R,cy-y*R,Math.max(.6,R*.008),0,Math.PI*2);ctx.fill();}}
    if(!mapReady(panel)){loadingMap(ctx,w,h,panel);return;}
    const dots=[];
    for(const p of reference.points){const e=panelRow(panel,p)?.prediction?.elevation_m;const radius=layer==='classification'||differenceLayer()?1:1+Math.max(-10000,Math.min(10000,e||0))/40000;const [x,y,z]=project(p.lat,p.lon,radius);if(z<=0)continue;const [sx,sy]=project(p.lat,p.lon);dots.push({id:p.id,x:cx+x*R,y:cy-y*R,z,sx:cx+sx*R,sy:cy-sy*R,color:pointColor(p,panel)});}
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
    for(const [p,r,e] of data){const px=x(p.elevation_m),py=y(e);ctx.fillStyle=Math.abs(e-p.elevation_m)>500||r.label!==p.truth?colors.error:'#4c68d7';ctx.globalAlpha=p.id===selected?1:.22;ctx.beginPath();ctx.arc(px,py,p.id===selected?4:1.5,0,Math.PI*2);ctx.fill();hits.scatter.push({id:p.id,x:px,y:py});}ctx.globalAlpha=1;
  }
  function explainReason(reason) { return (reason||'').split(';').map(s=>({unsupported_lake:'湖底高程参考未验收',unsupported_river_lake:'河湖参考未验收',unsupported_island_in_lake:'嵌套岛屿参考未验收',unsupported_floating_ice_shelf:'冰架下海床参考未验收',classification_reference_disagreement_or_boundary:'参考分类冲突 / 边界点',ocean_bed_vs_surface_height_conflict:'海洋类型与高程冲突'}[s]||s)).join('；'); }
  function answerStatus(row) { return row?.error_type === 'content_filter' ? '请求未决 · 内容过滤' : statusNames[row?.status]; }

  function details() {
    const p = reference.points[selected];
    const kind = p.truth ? 'Solid' : 'Water';
    $('point-title').textContent = coordinate(p);
    $('inspect-point').setAttribute('aria-label','查看已选点 ' + coordinate(p) + ' 的两侧回答');
    $('point-values').textContent = '冻结参考 ' + kind + ' · ' + (p.elevation_m == null ? '高程不可评分' : meters(p.elevation_m));
    $('point-error').textContent = '#' + selected + ' · ' + p.lat + '°, ' + p.lon + '° · ' +
      (p.elevation_m == null ? explainReason(p.height_reference_reason) : '分类与高程参考有效');
    $('prompt').textContent = reference.prompt_template.replace('{lat:.1f}', p.lat.toFixed(1)).replace('{lon:.1f}', p.lon.toFixed(1));
    for (const [key,suffix] of [['a','-a'],['b','']]) {
      const panel = panels[key], row = panelRow(panel,p), c = panel.classRows?.[selected];
      $('answer-title' + suffix).textContent = key.toUpperCase() + ' · ' + panelName(panel);
      $('point-value' + suffix).textContent = panel.error || (row?.status === 'ok' ? row.prediction.type + ' · ' +
        (row.prediction.elevation_m == null ? '高程不可评分' : meters(row.prediction.elevation_m)) +
        (panel.model && p.elevation_m != null ? ' · 对参考误差 ' + meters(Math.abs(row.prediction.elevation_m - p.elevation_m)) : '') : answerStatus(row) || '读取回答中…');
      $('terrain-answer-panel' + suffix).hidden = !panel.model;
      $('answer' + suffix).textContent = panel.error ?? row?.text ?? (panel.model ? row?.status === 'infra_error' ? '请求未决；无可用模型回答。' : '读取回答中…' : '冻结参考，不是模型回答。');
      $('classification-answer-panel' + suffix).hidden = !panel.model;
      $('classification-label' + suffix).textContent = c?.status === 'ok' ? (c.label ? 'Solid' : 'Water') : statusNames[c?.status] || '';
      $('classification-point-note' + suffix).textContent = panel.model?.classification ?
        '独立分类任务 · ' + (panel.model.classification.source_model || panel.name) + ' · 同一坐标 · 参考 ' + kind + (c ? ' · ' + answerStatus(c) : '') : '此模型没有独立分类任务结果。';
      $('classification-answer' + suffix).textContent = panel.classError ?? c?.text ?? (panel.model?.classification ? c?.status === 'infra_error' ? '请求未决；无可用模型回答。' : '读取回答中…' : '—');
    }
    const a = panelRow(panels.a,p), b = panelRow(panels.b,p);
    const classNote = comparable(a,b) ? a.label === b.label ? '分类一致' : '分类不同' : '分类无法比较';
    const delta = comparable(a,b,true) ? b.prediction.elevation_m - a.prediction.elevation_m : null;
    const deltaNode = $('point-delta');
    deltaNode.textContent = classNote + ' · ' + (delta == null ? '高程无法比较：缺少一侧有效数值' : '高程 B − A = ' + (delta > 0 ? '+' : '') + meters(delta));
    deltaNode.dataset.heightDelta = delta ?? '';
    deltaNode.dataset.classDifferent = comparable(a,b) ? String(a.label !== b.label) : '';
    $('point').value = String(selected);
    $('previous').disabled = selected === 0;
    $('next').disabled = selected === reference.points.length - 1;
  }
  function draw() {
    if (!reference) return;
    const paint = view === 'map' ? flatMap : globe;
    paint('reference', panels.a);
    paint('prediction', panels.b);
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
    $('model-summary').replaceChildren();
    if (!model) {
      $('model-summary').append(el('p','caption','两侧均为参考。选择模型后可查看评分。'));
      return;
    }
    const s = model.terrain.metrics;
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
  function syncPanels() {
    const focus = focusPanel(), side = focus === panels.b ? 'B' : 'A';
    model = focus?.model || null;
    records = focus?.rows || null;
    rasters.clear();
    for (const [key,selectId,canvasId,scoreId] of [['a','model-a','reference','reference-score'],['b','model','prediction','prediction-score']]) {
      const panel = panels[key];
      $(selectId).value = panel.name;
      $(canvasId).setAttribute('aria-busy',String(panel.busy));
      $(canvasId).dataset.model = panel.name;
      $(scoreId).textContent = panel.model ? pct(panel.model.terrain.metrics.joint_accuracy['500']) : 'REF';
      $(scoreId).title = panel.model ? '联合准确率 · ±500 m' : '冻结地理参考';
    }
    $('selected-model').textContent = panelName(panels.b);
    $('selected-model').dataset.model = panels.b.name;
    $('focus-model').textContent = model ? side + ' · ' + nameOf(model) : '请选择一个模型';
    $('scatter-model').textContent = model ? '参考 vs. ' + side + ' · ' + nameOf(model) : '请选择模型';
    $('scatter').setAttribute('aria-label',model ? '参考高程与 ' + side + ' ' + nameOf(model) + ' 预测的散点图' : '选择模型后显示散点图');
    $('strata-model').textContent = model ? side + ' · ' + nameOf(model) + ' · 联合 ±500 m' : '请选择模型查看各高程档成绩';
    $('error-example').disabled = !records || !reference.points.some(p => p.elevation_m != null && wrong(p,records[p.id]));
    $('error-example').title = model ? '查看 ' + side + ' · ' + nameOf(model) + ' 的联合判断错误' : '请选择模型';
    document.querySelectorAll('#ranking tr').forEach(row => row.classList.toggle('row-selected',row.dataset.model === model?.name));
    updateSummary();
    updateStrata();
    updatePair();
    updateMapControls();
  }
  async function setPanel(key,name) {
    if (!metadata) return;
    const chosen = metadata.models.find(m => m.name === name && m.terrain) || null;
    if (name !== 'reference' && !chosen) return;
    const panel = panels[key], token = ++panel.token;
    Object.assign(panel,{name,model:chosen,rows:null,classRows:null,error:null,classError:null,busy:!!chosen});
    $('explore-message').textContent = '';
    syncPanels();
    if (!chosen) { draw(); return; }
    const results = await Promise.allSettled([
      loadRecords(chosen.terrain.details_url),
      chosen.classification ? loadRecords(chosen.classification.details_url) : Promise.resolve(null)
    ]);
    if (token !== panel.token) return;
    if (results[0].status === 'fulfilled') panel.rows = results[0].value;
    else panel.error = results[0].reason.message;
    if (results[1].status === 'fulfilled') panel.classRows = results[1].value;
    else panel.classError = results[1].reason.message;
    panel.busy = false;
    syncPanels();
    draw();
  }
  const setModel = name => setPanel('b',name);
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
  function visibleModels() {
    return metadata.models.filter(m => {
      const group = m.name.split('-')[0];
      return (family === 'all' || (family === 'other' ? !['gpt','claude'].includes(group) : group === family)) &&
        (m.name + ' ' + nameOf(m)).toLowerCase().includes(search);
    });
  }
  function renderLeaderboard() {
    $('ranking').replaceChildren();
    const visible = visibleModels();
    for (const m of visible) {
      const rank = metadata.models.filter(item => item.terrain).indexOf(m) + 1;
      const s = m.terrain?.metrics, c = m.classification?.metrics;
      const row = el('tr', s ? '' : 'only-classification');
      row.dataset.model = m.name;
      row.classList.toggle('leader', rank === 1);
      row.classList.toggle('row-selected', model?.name === m.name);
      const td = el('td'), identity = el('div', 'model-cell');
      const family = m.name.split('-')[0], badge = {claude:'C',seed:'S',gpt:'G',kimi:'K',mimo:'M',qwen:'Q',step:'S',deepseek:'D'}[family] || '·';
      identity.append(el('span', 'rank', s ? String(rank).padStart(2, '0') : '—'), el('span', 'family-icon ' + family, badge));
      const title = el('div'), modelName = el(s ? 'button' : 'span', 'model-name' + (s ? ' model-button' : ''), nameOf(m));
      if (s) {
        modelName.setAttribute('aria-label', '在右侧查看 ' + nameOf(m) + ' 的联合任务');
        modelName.addEventListener('click', () => {
          setModel(m.name);
          $('explore').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
        });
      }
      title.append(modelName, el('span', 'model-alias', family.toUpperCase() + (s ? '' : ' · 仅分类任务')));
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
    $('empty-state').hidden = visible.length > 0;
    $('result-count').textContent = visible.length + ' / ' + metadata.models.length + ' MODELS';
  }
  function paintMetadata(preferred = {}) {
    renderLeaderboard();
    $('count-all').textContent = metadata.models.length;
    for (const [id, task] of [['terrain-count','terrain'], ['classification-count','classification']]) {
      $(id).textContent = metadata.sources[task].model_count;
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
    for (const [key,id] of [['a','model-a'],['b','model']]) {
      $(id).replaceChildren(new Option('全球参考','reference'));
      for (const m of available) $(id).add(new Option(nameOf(m),m.name));
      $(id).disabled = false;
      const fallback = key === 'a' ? 'reference' : available[0]?.name || 'reference';
      const name = preferred[key] === 'reference' || available.some(m => m.name === preferred[key]) ? preferred[key] : fallback;
      setPanel(key,name);
    }
  }

  async function refreshRecovery() {
    if (!metadata) return;
    try {
      const fresh = await json('data.json');
      if (fresh.sources.terrain.dataset_sha256 !== reference.dataset_sha256) throw new Error('参考版本变化，请刷新页面。');
      if (fresh.run_id !== metadata.run_id || fresh.built_at !== metadata.built_at) {
        const current = {a:panels.a.name,b:panels.b.name};
        metadata = fresh;
        cache.clear();
        paintMetadata(current);
      } else {
        metadata.recovery = fresh.recovery;
        paintStatus();
      }
    } catch (error) { $('load-status').textContent = error.message; }
  }

  function coordinate(p) {
    return Math.abs(p.lat) + '° ' + (p.lat >= 0 ? 'N' : 'S') + ' / ' + Math.abs(p.lon) + '° ' + (p.lon >= 0 ? 'E' : 'W');
  }
  function selectPoint(id, orient = false) {
    if (!reference) return;
    $('explore-message').textContent = '';
    selected = Math.max(0, Math.min(reference.points.length - 1, id));
    const p = reference.points[selected];
    if (orient) { yaw = -p.lon; pitch = Math.max(-80,Math.min(80,p.lat)); }
    $('hover-coordinate').textContent = coordinate(p) + ' · #' + selected;
    queueDraw();
  }
  function locate(lat,lon) {
    const id = Math.max(0,Math.min(89,Math.round((89 - lat) / 2))) * 180 + Math.max(0,Math.min(179,Math.round((lon + 179) / 2)));
    selectPoint(id,true);
    $('explore-message').textContent = '已定位到最近的采样点：' + coordinate(reference.points[id]) + '。';
  }
  function hitPoint(id,event) {
    const r = $(id).getBoundingClientRect(), x = event.clientX - r.left, y = event.clientY - r.top;
    if (id !== 'scatter' && view === 'map') return Math.min(89,Math.max(0,Math.floor(y / r.height * 90))) * 180 + Math.min(179,Math.max(0,Math.floor(x / r.width * 180)));
    let best = null, dist = 14;
    for (const p of hits[id] || []) {
      const d = Math.hypot(p.x - x,p.y - y);
      if (d < dist) { best = p.id; dist = d; }
    }
    return best;
  }
  function updateMapControls() {
    rasters.clear();
    $('worlds').classList.toggle('is-map',view === 'map');
    for (const [key,id] of [['a','reference'],['b','prediction']]) {
      const panel = panels[key], label = key.toUpperCase() + ' · ' + panelName(panel);
      $(id).setAttribute('aria-label',label + (view === 'globe' ? '地球，可拖动旋转' : '平面地图') + '，点击采样点或用方向键选择坐标');
      $('panel-' + key + '-caption').textContent = layer === 'height-diff' ? key.toUpperCase() + ' − ' + (key === 'a' ? 'B' : 'A') + ' · 高程差' :
        layer === 'class-diff' ? key.toUpperCase() + ' · 仅高亮分类分歧' : panel.model ? 'MODEL PREDICTION' : 'FROZEN REFERENCE';
    }
    for (const key of ['view','layer']) document.querySelectorAll('[data-' + key + ']').forEach(button => {
      const active = button.dataset[key] === (key === 'view' ? view : layer);
      button.classList.toggle('active',active); button.setAttribute('aria-pressed',String(active));
    });
    const legend = $('map-legend'); legend.replaceChildren();
    const entries = layer === 'elevation' ? [] : layer === 'classification' ?
      [[colors.solid,'固体'],[colors.water,'水体']] : layer === 'class-diff' ?
        [['#b86537','分歧 · 该侧为固体'],['#3955d5','分歧 · 该侧为水体'],[colors.correct,'分类一致']] :
        layer === 'height-diff' ? [] : [[colors.correct,'联合正确'],[colors.error,'联合错误']];
    if (layer === 'elevation') {
      const scale = el('span'), swatch = el('i','height-scale');
      swatch.style.background = 'linear-gradient(90deg,' + heightStops.map(([value,rgb]) => 'rgb(' + rgb.join(',') + ') ' + ((value + 10000) / 160) + '%').join(',') + ')';
      scale.append(el('span','','−10,000 m'),swatch,el('span','','+6,000 m')); legend.append(scale);
    }
    if (layer === 'height-diff') {
      const scale = el('span'), swatch = el('i','height-scale');
      swatch.style.background = 'linear-gradient(90deg,' + deltaColor(-2000) + ',' + deltaColor(0) + ',' + deltaColor(2000) + ')';
      scale.append(el('span','','低于另一侧 −2,000 m'),swatch,el('span','','+2,000 m 高于另一侧')); legend.append(scale);
    }
    const showMask = !['classification','class-diff'].includes(layer);
    for (const [color,label] of [...entries,[colors.invalid,differenceLayer() ? '任一无效' : '无效'],[colors.unresolved,differenceLayer() ? '任一未决' : '未决'],...(showMask ? [[colors.masked,layer === 'height-diff' ? '缺少高程数值' : '高程参考不可评分']] : [])]) {
      const item = el('span'), swatch = el('i'); swatch.style.background = color; item.append(swatch,el('span','',label)); legend.append(item);
    }
    $('hover-coordinate').textContent = view === 'map' ? '点击网格 · 查看坐标回答 ↗' : '拖动旋转 · 点击采样点 ↗';
    const note = layer === 'classification' ? '固体 / 水体视图来自联合任务回答；榜单“仅分类准确率”来自另一次独立任务。' :
      layer === 'class-diff' ? '仅高亮两侧分类不同的点，颜色表示该侧的固体 / 水体回答。分类来自联合任务，分歧不等于错误。' :
      layer === 'height-diff' ? '每侧显示“该侧 − 另一侧”的高程差：蓝色更低、橙色更高；色阶固定在 ±2,000 m，原始差值见坐标详情。模型之间可比较的高程不要求有参考真值；差异不代表误差。' :
      layer === 'error' ? '各模型分别与冻结参考比较：类型错误或高程误差 >500 m 的点标为联合错误；高程参考不可评分点、无效与未决分别标注。' :
        '颜色表示海床到山峰的高程，色阶两端截在 −10,000 / +6,000 m。';
    $('plot-note').textContent = note + (view === 'globe' && ['elevation','error'].includes(layer) ? ' 径向高程夸张约 160 倍，显示范围 ±10,000 m。' : '') +
      ' 分歧统计仅比较两侧有效值，逐点计数、不加权；所有采样点直接绘制，不做插值。';
    $('difference-example').disabled = !differenceIds().length;
    queueDraw();
  }
  for (const id of ['reference','prediction']) {
    let first, last, moved;
    $(id).addEventListener('pointerdown',event => {
      first = last = {x:event.clientX,y:event.clientY}; moved = false; $(id).setPointerCapture(event.pointerId);
    });
    $(id).addEventListener('pointermove',event => {
      if (last) {
        moved = moved || Math.hypot(event.clientX - first.x,event.clientY - first.y) > 4;
        if (view === 'globe') {
          yaw += (event.clientX - last.x) * .45;
          pitch = Math.max(-80,Math.min(80,pitch - (event.clientY - last.y) * .35));
          queueDraw();
        }
        last = {x:event.clientX,y:event.clientY};
      } else if (reference) {
        const point = hitPoint(id,event);
        if (point != null) $('hover-coordinate').textContent = coordinate(reference.points[point]) + ' · #' + point;
      }
    });
    $(id).addEventListener('pointerup',event => {
      if (last && !moved) { const point = hitPoint(id,event); if (point != null) selectPoint(point); }
      last = null;
    });
    $(id).addEventListener('pointercancel',() => last = null);
    $(id).addEventListener('keydown',event => {
      const step = {ArrowLeft:-1,ArrowRight:1,ArrowUp:-180,ArrowDown:180}[event.key];
      if (step) { event.preventDefault(); selectPoint(selected + step,true); }
    });
  }
  $('scatter').addEventListener('click',event => { const point = hitPoint('scatter',event); if (point != null) selectPoint(point,true); });
  $('model').addEventListener('change',() => setModel($('model').value));
  $('model-a').addEventListener('change',() => setPanel('a',$('model-a').value));
  $('swap-models').addEventListener('click',() => {
    const a = panels.a.name, b = panels.b.name;
    setPanel('a',b); setPanel('b',a);
  });
  $('point').addEventListener('change',() => selectPoint(Math.round(Number($('point').value) || 0),true));
  $('previous').addEventListener('click',() => selectPoint(selected - 1,true));
  $('next').addEventListener('click',() => selectPoint(selected + 1,true));
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click',() => { view = button.dataset.view; updateMapControls(); }));
  document.querySelectorAll('[data-layer]').forEach(button => button.addEventListener('click',() => { layer = button.dataset.layer; updateMapControls(); }));
  document.querySelectorAll('[data-coordinate]').forEach(button => button.addEventListener('click',() => { if (reference) locate(...button.dataset.coordinate.split(',').map(Number)); }));
  $('coordinate-form').addEventListener('submit',event => {
    event.preventDefault(); if (!reference) return;
    locate(Number($('latitude').value),Number($('longitude').value));
    document.querySelector('.coordinate-picker').open = false;
  });
  $('error-example').addEventListener('click',() => {
    if (!records) return;
    const candidates = reference.points.filter(p => p.elevation_m != null && wrong(p,records[p.id]));
    if (!candidates.length) return;
    const p = candidates.find(p => p.id > selected) || candidates[0];
    selectPoint(p.id,true);
    $('explore-message').textContent = nameOf(model) + ' 联合判断错误：' + coordinate(p) + '。原始回答见下方。';
  });
  $('difference-example').addEventListener('click',() => {
    const ids = differenceIds();
    if (!ids.length) return;
    const id = ids.find(id => id > selected) ?? ids[0];
    selectPoint(id,true);
    $('explore-message').textContent = (['classification','class-diff'].includes(layer) ? '分类分歧' : ['elevation','height-diff'].includes(layer) ? '高程相差超过 500 m' : '分类或高程分歧') +
      '：' + coordinate(reference.points[id]) + '。两侧原始回答见下方。';
  });
  function filterLeaderboard() {
    document.querySelectorAll('[data-family]').forEach(button => {
      const active = button.dataset.family === family;
      button.classList.toggle('active',active); button.setAttribute('aria-pressed',String(active));
    });
    if (metadata) renderLeaderboard();
  }
  document.querySelectorAll('[data-family]').forEach(button => button.addEventListener('click',() => { family = button.dataset.family; filterLeaderboard(); }));
  $('model-search').addEventListener('input',() => { search = $('model-search').value.trim().toLowerCase(); filterLeaderboard(); });
  $('reset-filters').addEventListener('click',() => { family = 'all'; search = ''; $('model-search').value = ''; filterLeaderboard(); });
  document.addEventListener('keydown',event => {
    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.matches('input,textarea,select,[contenteditable]')) { event.preventDefault(); $('model-search').focus(); }
  });
  $('download-csv').addEventListener('click',() => {
    if (!metadata) return;
    const rows = [['model','joint_accuracy_500','elevation_mae_m','independent_classification_accuracy','terrain_coverage','terrain_invalid','terrain_unresolved']];
    for (const m of visibleModels()) {
      const t = m.terrain?.metrics;
      rows.push([m.name,t?.joint_accuracy['500'],t?.elevation_mae_m,m.classification?.metrics.weighted_accuracy,t?.coverage,t?.invalid,t?.infra_errors]);
    }
    const csv = rows.map(row => row.map(value => '"' + String(value ?? '').replaceAll('"','""') + '"').join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['\ufeff' + csv],{type:'text/csv;charset=utf-8'})), link = el('a');
    link.href = url; link.download = 'land-water-elevation-leaderboard.csv'; link.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  $('motion-toggle').addEventListener('click',() => { motion = !motion; syncMotion(); });
  reduceMotion.addEventListener('change',() => { motion = !reduceMotion.matches; syncMotion(); });
  document.addEventListener('visibilitychange',syncMotion);
  syncMotion();

  Promise.all([json('data.json'), json('reference.details.json')]).then(([data, ref]) => {
    if (data.schema !== 'land-water-unified/1.0' || data.sources.terrain.dataset_sha256 !== ref.dataset_sha256) {
      throw new Error('结果与参考版本不一致。');
    }
    metadata = data;
    reference = ref;
    referenceRows = Object.fromEntries(ref.points.map(p => [p.id,{
      status:'ok',label:p.truth,prediction:{type:p.truth ? 'Solid' : 'Water',elevation_m:p.elevation_m}
    }]));
    paintMetadata();
    updateMapControls();
    heroGlobe();
    queueDraw();
    new ResizeObserver(queueDraw).observe($('reference'));
    new ResizeObserver(queueDraw).observe($('scatter'));
    new ResizeObserver(heroGlobe).observe($('hero-globe'));
    new IntersectionObserver(entries => { heroVisible = entries[0].isIntersecting; syncMotion(); }).observe($('hero-globe'));
    setInterval(refreshRecovery, 15000);
  }).catch(error => {
    $('load-status').textContent = error.message;
    $('load-status').setAttribute('role', 'alert');
  });
})();
