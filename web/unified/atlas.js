// Swipe comparison map, A/B pair statistics and the coordinate inspector.
import { colors, css, heightRGB, deltaRGB, heightGradient, deltaGradient } from './palette.js';
import { setupCanvas, drawScatter } from './charts.js';

const meters = n => n == null ? '—' : Math.round(n).toLocaleString('en-US') + ' m';
const statusNames = {ok: '有效回答', invalid: '无效回答', infra_error: '请求未决'};
const reasons = {unsupported_lake: '湖底高程参考未验收', unsupported_river_lake: '河湖参考未验收',
  unsupported_island_in_lake: '嵌套岛屿参考未验收', unsupported_floating_ice_shelf: '冰架下海床参考未验收',
  classification_reference_disagreement_or_boundary: '参考分类冲突 / 边界点', ocean_bed_vs_surface_height_conflict: '海洋类型与高程冲突'};
const $ = id => document.getElementById(id);
const el = (tag, className, text) => { const n = document.createElement(tag); if (className) n.className = className; if (text != null) n.textContent = text; return n; };
export const coordinate = p => Math.abs(p.lat) + '° ' + (p.lat >= 0 ? 'N' : 'S') + ' / ' + Math.abs(p.lon) + '° ' + (p.lon >= 0 ? 'E' : 'W');

export function rowsFromGrid(grid) {
  return Array.from(grid.state, (state, i) => state === 'S' || state === 'W' ?
    {status: 'ok', label: state === 'S' ? 1 : 0, prediction: {type: state === 'S' ? 'Solid' : 'Water', elevation_m: grid.elevation_m[i]}} :
    state === 'I' ? {status: 'invalid'} : state === 'U' ? {status: 'infra_error'} : undefined);
}
export function wrong(p, row, tolerance = 500) {
  return row?.status === 'invalid' || (row?.status === 'ok' &&
    (row.label !== p.truth || Math.abs(row.prediction.elevation_m - p.elevation_m) > tolerance));
}
const comparable = (a, b, height = false) => a?.status === 'ok' && b?.status === 'ok' &&
  (!height || (Number.isFinite(a.prediction.elevation_m) && Number.isFinite(b.prediction.elevation_m)));

export class Atlas {
  constructor(app) {
    this.app = app; this.layer = 'elevation'; this.split = 0.5; this.rasters = new Map(); this.cache = new Map(); this.pending = new Map();
    this.panels = Object.fromEntries(['a', 'b'].map(k => [k, {key: k, name: 'reference', model: null, rows: null, answers: null, classRows: null, error: null, classError: null, token: 0}]));
    this.bind();
  }
  get points() { return this.app.reference.points; }
  panelName(panel) { return panel.model ? this.app.nameOf(panel.model) : '全球参考'; }
  row(panel, p) { return panel.model ? panel.rows?.[p.id] : this.app.referenceRows[p.id]; }
  focusPanel() { return this.panels.b.model ? this.panels.b : this.panels.a.model ? this.panels.a : null; }
  differenceLayer() { return ['class-diff', 'height-diff'].includes(this.layer); }
  async load(path) {
    if (this.cache.has(path)) return this.cache.get(path);
    if (!this.pending.has(path)) this.pending.set(path, this.app.json(path).then(rows => {
      this.cache.set(path, rows); if (this.cache.size > 6) this.cache.delete(this.cache.keys().next().value); return rows;
    }).finally(() => this.pending.delete(path)));
    return this.pending.get(path);
  }
  fillSelects(models) {
    for (const id of ['model-a', 'model']) {
      $(id).replaceChildren(new Option('全球参考', 'reference'));
      for (const m of models) $(id).add(new Option(this.app.nameOf(m), m.name));
      $(id).disabled = false;
    }
  }
  setPanel(key, name) {
    const chosen = this.app.models.find(m => m.name === name) || null;
    if (name !== 'reference' && !chosen) return;
    const panel = this.panels[key], token = ++panel.token;
    Object.assign(panel, {name, model: chosen, rows: chosen ? this.app.rows(chosen.name) : null, answers: null, classRows: null, error: null, classError: null});
    this.rasters.clear(); this.sync(); this.draw();
    if (!chosen) return;
    $('answer' + (key === 'a' ? '-a' : '')).closest('.point-answer').setAttribute('aria-busy', 'true');
    Promise.allSettled([this.load(chosen.terrain.details_url),
      chosen.classification ? this.load(chosen.classification.details_url) : Promise.resolve(null)]).then(([terrain, classification]) => {
      if (token !== panel.token) return;
      if (terrain.status === 'fulfilled') panel.answers = terrain.value; else panel.error = terrain.reason.message;
      if (classification.status === 'fulfilled') panel.classRows = classification.value; else panel.classError = classification.reason.message;
      $('answer' + (key === 'a' ? '-a' : '')).closest('.point-answer').setAttribute('aria-busy', 'false');
      this.details();
    });
  }
  sync() {
    for (const [key, select, label] of [['a', 'model-a', 'swipe-label-a'], ['b', 'model', 'swipe-label-b']]) {
      const panel = this.panels[key];
      $(select).value = panel.name;
      $(label).textContent = key.toUpperCase() + ' · ' + this.panelName(panel) + (panel.model ? ' · ' + this.app.pct(panel.model.terrain.metrics.joint_accuracy['500']) : '');
    }
    const map = $('atlas-map'); map.dataset.modelA = this.panels.a.name; map.dataset.modelB = this.panels.b.name;
    $('selected-model').textContent = this.panelName(this.panels.b); $('selected-model').dataset.model = this.panels.b.name;
    const focus = this.focusPanel(), side = focus === this.panels.b ? 'B' : 'A';
    $('scatter-model').textContent = focus ? side + ' · ' + this.app.nameOf(focus.model) : '请选择模型';
    $('error-example').disabled = !focus || !this.points.some(p => p.elevation_m != null && wrong(p, focus.rows[p.id]));
    this.updatePair(); this.controls();
  }
  pointColor(p, panel) {
    const row = this.row(panel, p), A = this.panels.a, B = this.panels.b;
    if (this.differenceLayer()) {
      const a = this.row(A, p), b = this.row(B, p);
      if (a?.status === 'infra_error' || b?.status === 'infra_error') return colors.unresolved;
      if (a?.status === 'invalid' || b?.status === 'invalid') return colors.invalid;
      if (!comparable(a, b, this.layer === 'height-diff')) return colors.masked;
      if (this.layer === 'class-diff') return a.label === b.label ? colors.correct : row.label ? colors.solid : colors.a;
      return css(deltaRGB(b.prediction.elevation_m - a.prediction.elevation_m));
    }
    if (row?.status !== 'ok') return row?.status === 'invalid' ? colors.invalid : colors.unresolved;
    if (this.layer === 'classification') return row.label ? colors.solid : colors.water;
    if (p.elevation_m == null) return colors.masked;
    if (this.layer === 'error') return panel.model && wrong(p, row) ? colors.error : colors.correct;
    return css(heightRGB(row.prediction.elevation_m));
  }
  raster(panel) {
    const key = panel.key + ':' + this.layer;
    if (!this.rasters.has(key)) {
      const canvas = document.createElement('canvas'); canvas.width = 180; canvas.height = 90;
      const ctx = canvas.getContext('2d');
      for (const p of this.points) { ctx.fillStyle = this.pointColor(p, panel); ctx.fillRect(p.id % 180, Math.floor(p.id / 180), 1, 1); }
      this.rasters.set(key, canvas);
    }
    return this.rasters.get(key);
  }
  single() { return this.layer === 'height-diff'; }
  draw() {
    if (!this.app.reference) return;
    const {ctx, w, h} = setupCanvas($('atlas-map')), split = this.single() ? 0 : Math.round(this.split * w);
    ctx.imageSmoothingEnabled = false;
    const a = this.raster(this.panels.a), b = this.raster(this.panels.b);
    if (split > 0) ctx.drawImage(a, 0, 0, split / w * 180, 90, 0, 0, split, h);
    if (split < w) ctx.drawImage(b, split / w * 180, 0, 180 - split / w * 180, 90, split, 0, w - split, h);
    ctx.strokeStyle = 'rgba(255,255,255,.07)'; ctx.lineWidth = 1; ctx.beginPath();
    for (let x = 0; x <= 180; x += 30) { ctx.moveTo(x / 180 * w, 0); ctx.lineTo(x / 180 * w, h); }
    for (let y = 15; y < 90; y += 15) { ctx.moveTo(0, y / 90 * h); ctx.lineTo(w, y / 90 * h); }
    ctx.stroke();
    const s = this.app.selected, x = (s % 180 + .5) / 180 * w, y = (Math.floor(s / 180) + .5) / 90 * h;
    ctx.strokeStyle = 'rgba(238,242,251,.55)'; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    ctx.strokeStyle = colors.ink; ctx.lineWidth = 2; ctx.strokeRect(x - 6, y - 6, 12, 12);
    $('swipe-handle').style.left = (this.split * 100) + '%';
    $('swipe-handle').hidden = this.single();
    $('swipe-label-a').hidden = this.single();
    $('atlas-map').setAttribute('aria-busy', 'false');
    const focus = this.focusPanel();
    this.scatterHits = drawScatter($('scatter'), {points: this.points, rows: focus?.rows, selected: s, focusLabel: focus ? this.app.nameOf(focus.model) : null});
    this.details();
  }
  updatePair() {
    const summary = $('pair-summary'), A = this.panels.a, B = this.panels.b; summary.replaceChildren();
    const pair = this.pair = {classComparable: 0, classIds: [], heightComparable: 0, heightIds: [], heightSum: 0};
    for (const p of this.points) {
      const a = this.row(A, p), b = this.row(B, p);
      if (comparable(a, b)) { pair.classComparable++; if (a.label !== b.label) pair.classIds.push(p.id); }
      if (comparable(a, b, true)) {
        pair.heightComparable++; const d = Math.abs(a.prediction.elevation_m - b.prediction.elevation_m);
        pair.heightSum += d; if (d > 500) pair.heightIds.push(p.id);
      }
    }
    const mean = pair.heightComparable ? pair.heightSum / pair.heightComparable : null;
    Object.assign(summary.dataset, {classComparable: pair.classComparable, classDifferent: pair.classIds.length,
      heightComparable: pair.heightComparable, heightDifferent: pair.heightIds.length, heightMean: mean ?? ''});
    for (const [label, value, total] of [['分类分歧', pair.classIds.length, pair.classComparable], ['高程差 >500 m', pair.heightIds.length, pair.heightComparable]]) {
      const item = el('span'); item.append(label + ' ', el('strong', '', value.toLocaleString('en-US')), ' / ' + total.toLocaleString('en-US') + ' 点'); summary.append(item);
    }
    const item = el('span'); item.append('平均 |高程差| ', el('strong', '', meters(mean))); summary.append(item);
    $('difference-example').disabled = !this.differenceIds().length;
  }
  differenceIds() {
    if (!this.pair) return [];
    if (['class-diff', 'classification'].includes(this.layer)) return this.pair.classIds;
    if (['height-diff', 'elevation'].includes(this.layer)) return this.pair.heightIds;
    return [...new Set([...this.pair.classIds, ...this.pair.heightIds])].sort((a, b) => a - b);
  }
  controls() {
    document.querySelectorAll('[data-layer]').forEach(b => { const on = b.dataset.layer === this.layer; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
    const legend = $('map-legend'); legend.replaceChildren();
    const scale = (left, gradient, right) => { const s = el('span', 'scale'), i = el('i', 'height-scale'); i.style.background = gradient; s.append(el('span', '', left), i, el('span', '', right)); legend.append(s); };
    if (this.layer === 'elevation') scale('−10,000 m', heightGradient(), '+6,000 m');
    if (this.layer === 'height-diff') scale('B 比 A 低 2,000 m', deltaGradient(2000), 'B 比 A 高 2,000 m');
    const entries = {classification: [[colors.solid, '固体'], [colors.water, '水体']],
      'class-diff': [[colors.solid, '分歧 · 该侧答固体'], [colors.a, '分歧 · 该侧答水体'], [colors.correct, '分类一致']],
      error: [[colors.correct, '联合正确'], [colors.error, '联合错误']]}[this.layer] || [];
    const masked = !['classification', 'class-diff'].includes(this.layer);
    for (const [color, label] of [...entries, [colors.invalid, this.differenceLayer() ? '任一无效' : '无效'],
      [colors.unresolved, this.differenceLayer() ? '任一未决' : '未决'], ...(masked ? [[colors.masked, this.layer === 'height-diff' ? '缺少高程数值' : '高程参考不可评分']] : [])]) {
      const item = el('span'), sw = el('i'); sw.style.background = color; item.append(sw, el('span', '', label)); legend.append(item);
    }
    $('plot-note').textContent = {elevation: '颜色表示从海床到山峰的高程。拖动中间的分界线，左边是 A，右边是 B。',
      error: '类型错误或高程误差超过 500 m 的点标为联合错误。',
      classification: '固体 / 水体来自联合任务的回答，与榜单中的“仅分类准确率”不是同一次回答。',
      'class-diff': '只高亮两侧分类不同的点，颜色表示该侧的回答；分歧不等于错误。',
      'height-diff': '整张图显示 B − A 的高程差，色阶截在 ±2,000 m；差异不代表误差。'}[this.layer] +
      ' 每个 2° 网格直接绘制一个采样点，不做插值。';
  }
  details() {
    const app = this.app, p = this.points[app.selected], kind = p.truth ? 'Solid' : 'Water';
    $('point-title').textContent = coordinate(p);
    $('point-values').textContent = '参考 ' + kind + ' · ' + (p.elevation_m == null ? '高程不可评分' : meters(p.elevation_m));
    $('point-error').textContent = '#' + p.id + ' · ' + p.lat + '°, ' + p.lon + '° · ' +
      (p.elevation_m == null ? (p.height_reference_reason || '').split(';').map(r => reasons[r] || r).join('；') : '分类与高程参考有效');
    $('prompt').textContent = app.reference.prompt_template.replace('{lat:.1f}', p.lat.toFixed(1)).replace('{lon:.1f}', p.lon.toFixed(1));
    for (const [key, suffix] of [['a', '-a'], ['b', '']]) {
      const panel = this.panels[key], row = this.row(panel, p), raw = panel.answers?.[p.id], c = panel.classRows?.[p.id];
      $('answer-title' + suffix).textContent = key.toUpperCase() + ' · ' + this.panelName(panel);
      const status = raw?.error_type === 'content_filter' ? '请求未决 · 内容过滤' : statusNames[row?.status] || '请求未决';
      $('point-value' + suffix).textContent = row?.status === 'ok' ? row.prediction.type + ' · ' + meters(row.prediction.elevation_m) +
        (panel.model && p.elevation_m != null ? ' · 误差 ' + meters(Math.abs(row.prediction.elevation_m - p.elevation_m)) : '') : status;
      $('terrain-answer-panel' + suffix).hidden = !panel.model;
      $('answer' + suffix).textContent = panel.error ?? (raw ? raw.text ?? '请求未决；无可用模型回答。' : panel.model ? '读取回答中…' : '');
      $('classification-answer-panel' + suffix).hidden = !panel.model;
      $('classification-label' + suffix).textContent = c?.status === 'ok' ? (c.label ? 'Solid' : 'Water') : statusNames[c?.status] || '';
      $('classification-point-note' + suffix).textContent = panel.model?.classification ? '同一坐标在“只判断陆地或水体”任务里的回答 · 参考 ' + kind : '此模型没有独立分类任务结果。';
      $('classification-answer' + suffix).textContent = panel.classError ?? (c ? c.text ?? '请求未决；无可用模型回答。' : panel.model?.classification ? '读取回答中…' : '—');
    }
    const a = this.row(this.panels.a, p), b = this.row(this.panels.b, p), node = $('point-delta');
    const delta = comparable(a, b, true) ? b.prediction.elevation_m - a.prediction.elevation_m : null;
    node.textContent = (comparable(a, b) ? a.label === b.label ? '分类一致' : '分类不同' : '分类无法比较') + ' · ' +
      (delta == null ? '高程无法比较：缺少一侧有效数值' : '高程 B − A = ' + (delta > 0 ? '+' : '') + meters(delta));
    node.dataset.heightDelta = delta ?? ''; node.dataset.classDifferent = comparable(a, b) ? String(a.label !== b.label) : '';
    $('point').value = String(p.id);
    $('previous').disabled = p.id === 0; $('next').disabled = p.id === this.points.length - 1;
  }
  cellAt(event) {
    const r = $('atlas-map').getBoundingClientRect();
    return Math.min(89, Math.max(0, Math.floor((event.clientY - r.top) / r.height * 90))) * 180 +
      Math.min(179, Math.max(0, Math.floor((event.clientX - r.left) / r.width * 180)));
  }
  bind() {
    const map = $('atlas-map'), handle = $('swipe-handle'), tip = $('map-tip');
    const setSplit = value => { this.split = Math.max(0, Math.min(1, value)); handle.setAttribute('aria-valuenow', String(Math.round(this.split * 100))); this.app.queue(); };
    let dragging = false;
    handle.addEventListener('pointerdown', e => { dragging = true; handle.setPointerCapture(e.pointerId); e.preventDefault(); });
    handle.addEventListener('pointermove', e => { if (dragging) { const r = map.getBoundingClientRect(); setSplit((e.clientX - r.left) / r.width); } });
    handle.addEventListener('pointerup', () => { dragging = false; });
    handle.addEventListener('keydown', e => {
      const step = {ArrowLeft: -.05, ArrowRight: .05, Home: -1, End: 1}[e.key];
      if (step != null) { e.preventDefault(); setSplit(this.split + step); }
    });
    map.addEventListener('click', e => this.app.select(this.cellAt(e)));
    map.addEventListener('pointermove', e => {
      if (!this.app.reference) return;
      const id = this.cellAt(e), p = this.points[id], r = map.getBoundingClientRect(), side = this.single() ? null : (e.clientX - r.left) / r.width < this.split ? 'a' : 'b';
      const value = panel => { const row = this.row(panel, p); return row?.status === 'ok' ? row.prediction.type + ' ' + meters(row.prediction.elevation_m) : statusNames[row?.status] || '—'; };
      tip.hidden = false; tip.replaceChildren(el('strong', '', coordinate(p)));
      for (const key of side ? [side] : ['a', 'b']) tip.append(el('span', '', key.toUpperCase() + ' · ' + this.panelName(this.panels[key]) + ' · ' + value(this.panels[key])));
      tip.style.left = Math.min(r.width - 240, e.clientX - r.left + 14) + 'px'; tip.style.top = (e.clientY - r.top + 16) + 'px';
    });
    map.addEventListener('pointerleave', () => { tip.hidden = true; });
    map.addEventListener('keydown', e => {
      const step = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -180, ArrowDown: 180}[e.key];
      if (step) { e.preventDefault(); this.app.select(this.app.selected + step, true); }
    });
    $('scatter').addEventListener('click', e => {
      const r = $('scatter').getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      let best = null, dist = 12;
      for (const d of this.scatterHits || []) { const k = Math.hypot(d.x - x, d.y - y); if (k < dist) { best = d.id; dist = k; } }
      if (best != null) this.app.select(best, true);
    });
    $('model').addEventListener('change', () => this.setPanel('b', $('model').value));
    $('model-a').addEventListener('change', () => this.setPanel('a', $('model-a').value));
    $('swap-models').addEventListener('click', () => { const a = this.panels.a.name, b = this.panels.b.name; this.setPanel('a', b); this.setPanel('b', a); });
    document.querySelectorAll('[data-layer]').forEach(b => b.addEventListener('click', () => { this.layer = b.dataset.layer; this.rasters.clear(); this.controls(); this.app.queue(); }));
    $('difference-example').addEventListener('click', () => {
      const ids = this.differenceIds(); if (!ids.length) return;
      const id = ids.find(i => i > this.app.selected) ?? ids[0]; this.app.select(id, true);
      $('explore-message').textContent = (['classification', 'class-diff'].includes(this.layer) ? '分类分歧' : ['elevation', 'height-diff'].includes(this.layer) ? '高程相差超过 500 m' : '分类或高程分歧') +
        '：' + coordinate(this.points[id]) + '。两侧原始回答见下方。';
    });
    $('error-example').addEventListener('click', () => {
      const focus = this.focusPanel(); if (!focus) return;
      const candidates = this.points.filter(p => p.elevation_m != null && wrong(p, focus.rows[p.id]));
      const p = candidates.find(c => c.id > this.app.selected) || candidates[0]; if (!p) return;
      this.app.select(p.id, true);
      $('explore-message').textContent = this.app.nameOf(focus.model) + ' 联合判断错误：' + coordinate(p) + '。原始回答见下方。';
    });
  }
}
