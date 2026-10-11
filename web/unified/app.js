// Entry point: loads frozen results and wires the leaderboard, tolerance study, globes and atlas.
import { ReliefGlobe, terrainCells, errorCells } from './globe.js';
import { ToleranceChart, RankingBars, drawFingerprint } from './charts.js';
import { Atlas, rowsFromGrid, coordinate } from './atlas.js';
import { heightGradient, deltaGradient } from './palette.js';

const $ = id => document.getElementById(id);
const el = (tag, className, text) => { const n = document.createElement(tag); if (className) n.className = className; if (text != null) n.textContent = text; return n; };
const pct = n => n == null ? '—' : (100 * n).toFixed(2) + '%';
const names = {'gpt-6-astra': 'GPT-6 Astra', 'gpt-5.6-sol': 'GPT-5.6 Sol', 'gpt-5.6-terra': 'GPT-5.6 Terra', 'claude-opus-5-5': 'Claude Opus 5.5',
  'claude-sonnet-5-5': 'Claude Sonnet 5.5', 'claude-haiku-5-5': 'Claude Haiku 5.5', 'kimi-k3': 'Kimi K3', 'mimo-v2.6-pro': 'MiMo V2.6 Pro',
  'qwen3.8-max': 'Qwen 3.8 Max', 'deepseek-v4.1-flash': 'DeepSeek V4.1 Flash', 'step-5-preview': 'Step 5 Preview', 'seed-2.1': 'Seed 2.1'};
const strata = [['solid_low', '低地', '< 200 m'], ['solid_mid', '丘陵', '200–1,000 m'], ['solid_high', '高地', '1,000–2,500 m'],
  ['solid_alpine', '高山 / 冰原', '≥ 2,500 m'], ['water_shelf', '浅海', '< 500 m'], ['water_slope', '大陆坡', '500–2,500 m'],
  ['water_deep', '深海', '2,500–4,500 m'], ['water_abyssal', '深海平原', '≥ 4,500 m']];
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
const smooth = () => reduceMotion.matches ? 'instant' : 'smooth';

const app = {
  selected: 6596, pct, metadata: null, reference: null, atlas: null, models: [], rowCache: new Map(), frame: 0,
  nameOf: m => names[m.name] || m.name,
  async json(path) {
    let response;
    try { response = await fetch(path, {cache: 'no-store'}); } catch { throw new Error('结果读取失败，请刷新重试。'); }
    if (!response.ok) throw new Error('结果读取失败（HTTP ' + response.status + '），请刷新重试。');
    return response.json();
  },
  rows(name) {
    if (!this.rowCache.has(name)) this.rowCache.set(name, rowsFromGrid(this.atlas.models[name].grid));
    return this.rowCache.get(name);
  },
  queue() { if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; this.view.draw(); }); },
  select(id, fly = false) {
    if (!this.reference) return;
    this.selected = Math.max(0, Math.min(this.reference.points.length - 1, id));
    $('explore-message').textContent = '';
    globes.model?.select(this.selected, {fly});
    globeReadout(); this.queue();
  }
};
app.view = new Atlas(app);
const globes = {};
let motion = !reduceMotion.matches, family = 'all', search = '', globeModel = 'reference', globeMode = 'terrain', playTimer = 0;

// ---------- Leaderboard ----------
function visibleModels() {
  return app.metadata.models.filter(m => {
    const group = m.name.split('-')[0];
    return (family === 'all' || (family === 'other' ? !['gpt', 'claude'].includes(group) : group === family)) &&
      (m.name + ' ' + app.nameOf(m)).toLowerCase().includes(search);
  });
}
function renderLeaderboard() {
  $('ranking').replaceChildren();
  const visible = visibleModels(), best = app.models[0]?.terrain.metrics.joint_accuracy['500'] || 1;
  for (const m of visible) {
    const rank = app.models.indexOf(m) + 1, s = m.terrain?.metrics, c = m.classification?.metrics;
    const row = el('tr', rank === 1 ? 'leader' : ''); row.dataset.model = m.name;
    const family = m.name.split('-')[0], td = el('td'), identity = el('div', 'model-cell');
    identity.append(el('span', 'rank', s ? String(rank).padStart(2, '0') : '—'),
      el('span', 'family-icon ' + family, {claude: 'C', seed: 'S', gpt: 'G', kimi: 'K', mimo: 'M', qwen: 'Q', step: 'S', deepseek: 'D'}[family] || '·'));
    const title = el('div'), button = el(s ? 'button' : 'span', 'model-name' + (s ? ' model-button' : ''), app.nameOf(m));
    if (s) {
      button.setAttribute('aria-label', '在地球和对比地图上查看 ' + app.nameOf(m));
      button.addEventListener('click', () => { showGlobeModel(m.name); app.view.setPanel('b', m.name); $('globe').scrollIntoView({behavior: smooth()}); });
    }
    title.append(button, el('span', 'model-alias', family.toUpperCase()));
    identity.append(title); td.append(identity); row.append(td);
    const score = el('td', 'primary-col');
    if (s) {
      const value = s.joint_accuracy['500'], number = el('div', 'score', (value * 100).toFixed(2)), track = el('div', 'score-track'), fill = el('div', 'score-fill');
      number.append(el('small', '', '%')); fill.style.width = (value / best * 100) + '%'; track.append(fill); score.append(number, track);
    } else score.append(el('span', 'missing', '—'));
    row.append(score);
    const mae = el('td');
    if (s?.elevation_mae_m != null) mae.append(el('span', 'metric', Math.round(s.elevation_mae_m).toLocaleString('en-US')), el('span', 'metric-unit', ' m'));
    else mae.append(el('span', 'missing', '—'));
    row.append(mae);
    const cls = el('td', 'classification-col', pct(c?.weighted_accuracy)); row.append(cls);
    const coverage = el('td', s && s.coverage < .99 ? 'coverage-low' : '', s ? pct(s.coverage) : '—');
    if (s) coverage.title = s.invalid + ' 条无效回答计为错误；' + s.infra_errors + ' 条未得到回答的请求不计入';
    row.append(coverage);
    $('ranking').append(row);
  }
  $('empty-state').hidden = visible.length > 0;
  $('result-count').textContent = visible.length + ' / ' + app.metadata.models.length + ' MODELS';
}
function filterLeaderboard() {
  document.querySelectorAll('[data-family]').forEach(b => { const on = b.dataset.family === family; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  if (app.metadata) renderLeaderboard();
}
document.querySelectorAll('[data-family]').forEach(b => b.addEventListener('click', () => { family = b.dataset.family; filterLeaderboard(); }));
$('model-search').addEventListener('input', () => { search = $('model-search').value.trim().toLowerCase(); filterLeaderboard(); });
$('reset-filters').addEventListener('click', () => { family = 'all'; search = ''; $('model-search').value = ''; filterLeaderboard(); });
document.addEventListener('keydown', e => {
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !e.target.matches('input,textarea,select,[contenteditable]')) { e.preventDefault(); $('model-search').focus(); }
});
$('download-csv').addEventListener('click', () => {
  if (!app.metadata) return;
  const rows = [['model', 'joint_accuracy_500', 'elevation_mae_m', 'independent_classification_accuracy', 'terrain_coverage', 'terrain_invalid', 'terrain_unresolved']];
  for (const m of visibleModels()) { const t = m.terrain?.metrics; rows.push([m.name, t?.joint_accuracy['500'], t?.elevation_mae_m, m.classification?.metrics.weighted_accuracy, t?.coverage, t?.invalid, t?.infra_errors]); }
  const csv = rows.map(r => r.map(v => '"' + String(v ?? '').replaceAll('"', '""') + '"').join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob(['\ufeff' + csv], {type: 'text/csv;charset=utf-8'})), link = el('a');
  link.href = url; link.download = 'land-water-elevation-leaderboard.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// ---------- Tolerance study ----------
let chart, bars, focus = null;
function setupTolerance() {
  const t = app.atlas.tolerances_m, series = app.models.map(m => ({name: m.name, label: app.nameOf(m), curve: app.atlas.models[m.name].tolerance_curve}));
  const rank = (i) => [...series].sort((a, b) => b.curve[i] - a.curve[i]).map(s => s.name);
  const base = rank(t.indexOf(500)), wide = rank(t.length - 1);
  focus = series.map(s => [s.name, Math.abs(wide.indexOf(s.name) - base.indexOf(s.name))]).sort((a, b) => b[1] - a[1])[0][0];
  chart = new ToleranceChart($('tolerance-chart'), $('tolerance-tip'), {tolerances: t, series, official: 500, onFocus: setFocus});
  bars = new RankingBars($('tolerance-ranking'), series, {onFocus: setFocus});
  const ticks = $('tolerance-ticks');
  for (const v of t) { const o = el('option'); o.value = v; if (v % 500 === 0 || v === 100) o.label = v.toLocaleString('en-US'); ticks.append(o); }
  for (const v of [100, 500, 1000, 1500, 2000]) $('tolerance-scale').append(el('span', '', v.toLocaleString('en-US')));
  $('tolerance-slider').min = t[0]; $('tolerance-slider').max = t[t.length - 1]; $('tolerance-slider').step = t[1] - t[0]; $('tolerance-slider').value = 500;
  $('tolerance-slider').addEventListener('input', () => updateTolerance(true));
  new ResizeObserver(() => chart.draw()).observe($('tolerance-chart'));
  updateTolerance(false);
}
function setFocus(name) { focus = name; updateTolerance(false); }
function updateTolerance(animate) {
  const t = app.atlas.tolerances_m, value = Number($('tolerance-slider').value), index = t.indexOf(value), baseIndex = t.indexOf(500);
  chart.tolerance = value; chart.focus = focus; chart.draw();
  bars.update(index, baseIndex, focus, animate && !reduceMotion.matches);
  $('tolerance-slider').setAttribute('aria-valuetext', '±' + value.toLocaleString('en-US') + ' m');
  $('tolerance-value').textContent = '±' + value.toLocaleString('en-US');
  const order = i => [...app.models].sort((a, b) => app.atlas.models[b.name].tolerance_curve[i] - app.atlas.models[a.name].tolerance_curve[i]);
  const now = order(index), before = order(baseIndex);
  const moves = now.map((m, r) => [m, before.indexOf(m) - r]).filter(([, d]) => d).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const insight = $('tolerance-insight'); insight.replaceChildren();
  insight.append(el('strong', '', value === 500 ? '官方口径 ±500 m' : '放宽到 ±' + value.toLocaleString('en-US') + ' m'), ' · 第一名 ' + app.nameOf(now[0]) + ' ' + pct(app.atlas.models[now[0].name].tolerance_curve[index]) + '。');
  insight.append(value === 500 ? '拖动滑块，看看哪些模型的排名会变。' : moves.length ? moves.slice(0, 3).map(([m, d]) => app.nameOf(m) + (d > 0 ? ' 上升 ' : ' 下降 ') + Math.abs(d) + ' 名').join('，') + '。' : '排名与官方口径相同。');
  const f = app.models.find(m => m.name === focus);
  if (f) $('tolerance-focus').textContent = '聚焦 · ' + app.nameOf(f) + ' · 点击曲线或名字切换';
}

// ---------- Model globe ----------
const referenceCells = () => terrainCells(app.reference.points.map(p => p.elevation_m), app.reference.points.map(p => p.truth ? 'S' : 'W'));
function globeCells(name, mode) {
  if (name === 'reference') return referenceCells();
  const g = app.atlas.models[name].grid;
  return mode === 'error' ? errorCells(g.elevation_m, g.state, app.reference.points) : terrainCells(g.elevation_m, g.state);
}
function showGlobeModel(name, mode = globeMode) {
  if (name === 'reference' && mode === 'error') mode = 'terrain';
  globeModel = name; globeMode = mode;
  globes.model?.show(globeCells(name, mode));
  globes.model?.setSea(mode === 'terrain' ? 0 : null);
  $('model-globe').dataset.model = name; $('model-globe').dataset.mode = mode;
  document.querySelectorAll('[data-globe-model]').forEach(b => { const on = b.dataset.globeModel === name; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  document.querySelectorAll('[data-globe-mode]').forEach(b => {
    const on = b.dataset.globeMode === mode; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on));
    b.disabled = b.dataset.globeMode === 'error' && name === 'reference';
  });
  $('globe-legend').replaceChildren();
  const scale = (left, gradient, right) => { const s = el('span', 'scale'), i = el('i', 'height-scale'); i.style.background = gradient; s.append(el('span', '', left), i, el('span', '', right)); $('globe-legend').append(s); };
  if (mode === 'error') scale('预测偏低 3,000 m', deltaGradient(3000), '预测偏高 3,000 m');
  else scale('−10,000 m', heightGradient(), '+6,000 m');
  $('globe-caption').textContent = mode === 'error' ? '误差超过 500 m 的格子会竖起来：偏高向外凸，偏低向内凹；误差在 500 m 以内的格子保持平整。径向夸张约 100 倍。' :
    '每根柱子是一个 2° 网格，高度是' + (name === 'reference' ? '参考' : '模型预测的') + '高程，径向夸张约 50 倍；半透明海面在 0 m。';
  globeStats(); globeReadout();
}
function globeStats() {
  const box = $('globe-stats'); box.replaceChildren();
  const m = app.models.find(x => x.name === globeModel), ref = app.atlas.reference;
  $('globe-title').textContent = m ? app.nameOf(m) : '全球参考';
  const prof = m ? app.atlas.models[m.name].profile : null;
  const stats = m ? [['联合准确率 ±500 m', pct(m.terrain.metrics.joint_accuracy['500'])], ['高程 MAE', Math.round(m.terrain.metrics.elevation_mae_m).toLocaleString('en-US') + ' m'],
    ['误差超过 2 km', prof.errors_over_2000_m.toLocaleString('en-US') + ' 点'], ['海底起伏', Math.round(prof.seabed_roughness_m) + ' m', '参考 ' + Math.round(ref.seabed_roughness_m) + ' m'],
    ['整百取整', Math.round(prof.multiple_100 * 100) + '%', '500 的倍数 ' + Math.round(prof.multiple_500 * 100) + '%'], ['不同取值', prof.distinct_values.toLocaleString('en-US') + ' 种']] :
    [['海底起伏', Math.round(ref.seabed_roughness_m) + ' m', '相邻海洋格子的平均高差'], ['采样点', '16,200', '2° 全球网格']];
  for (const [label, value, note] of stats) { const s = el('div', 'stat'); s.append(el('span', '', label), el('strong', '', value)); if (note) s.append(el('small', '', note)); box.append(s); }
  drawFingerprint($('fingerprint'), app.atlas.histogram, prof ? prof.histogram : ref.histogram, ref.histogram);
  $('fingerprint-note').textContent = m ? '蓝色柱是 ' + app.nameOf(m) + ' 回答的高程分布，白线是参考。尖刺说明模型偏爱整百、整千这样的取整值。' : '白线是参考高程分布，选择模型后叠加该模型的回答分布。';
}
function globeReadout() {
  if (!app.reference) return;
  const p = app.reference.points[app.selected], row = globeModel === 'reference' ? null : app.rows(globeModel)[p.id];
  const value = row?.status === 'ok' ? row.prediction.type + ' · ' + Math.round(row.prediction.elevation_m).toLocaleString('en-US') + ' m' : row ? '无有效回答' : null;
  $('globe-readout').textContent = coordinate(p) + ' · 参考 ' + (p.truth ? 'Solid' : 'Water') + ' ' +
    (p.elevation_m == null ? '高程不可评分' : Math.round(p.elevation_m).toLocaleString('en-US') + ' m') + (value ? ' · 模型 ' + value : '');
}
function setupGlobeControls() {
  const list = $('globe-models');
  for (const m of [{name: 'reference'}, ...app.models]) {
    const b = el('button', 'chip'); b.type = 'button'; b.dataset.globeModel = m.name;
    b.append(el('span', '', m.name === 'reference' ? '全球参考' : app.nameOf(m)), el('small', '', m.terrain ? (m.terrain.metrics.joint_accuracy['500'] * 100).toFixed(1) : 'REF'));
    b.addEventListener('click', () => { stopPlay(); showGlobeModel(m.name); }); list.append(b);
  }
  document.querySelectorAll('[data-globe-mode]').forEach(b => b.addEventListener('click', () => showGlobeModel(globeModel, b.dataset.globeMode)));
  $('globe-play').addEventListener('click', () => playTimer ? stopPlay() : startPlay());
}
function startPlay() {
  const order = app.models.map(m => m.name); let i = Math.max(0, order.indexOf(globeModel));
  if (globeMode === 'terrain' && globeModel === 'reference') i = -1;
  const step = () => { i = (i + 1) % order.length; showGlobeModel(order[i]); };
  step(); playTimer = setInterval(step, 3200);
  $('globe-play').setAttribute('aria-pressed', 'true'); $('globe-play').textContent = '暂停巡览';
}
function stopPlay() { clearInterval(playTimer); playTimer = 0; $('globe-play').setAttribute('aria-pressed', 'false'); $('globe-play').textContent = '依次播放全部模型'; }

// ---------- Draining the ocean ----------
function setupAbyss() {
  const steps = [...document.querySelectorAll('.abyss-step')];
  for (const step of steps) {
    const key = step.dataset.stratum, list = el('ol', 'step-bars');
    const value = m => key === 'solid' ? m.terrain.metrics.by_type.Solid.joint_accuracy['500'] : m.terrain.metrics.by_stratum[key].joint_accuracy['500'];
    const sorted = [...app.models].sort((a, b) => value(b) - value(a));
    for (const m of sorted) {
      const li = el('li'), track = el('span', 'tr'), fill = el('i'); fill.style.width = value(m) * 100 + '%'; track.append(fill);
      li.append(el('span', 'nm', app.nameOf(m)), track, el('span', 'vl', (value(m) * 100).toFixed(1) + '%')); list.append(li);
    }
    step.append(list);
  }
  const activate = step => {
    steps.forEach(s => s.classList.toggle('active', s === step));
    const [lo, hi] = step.dataset.band.split(',').map(Number);
    globes.abyss?.setSea(step.dataset.sea === 'none' ? null : Number(step.dataset.sea));
    globes.abyss?.setBand([lo, hi]);
    $('abyss-depth').textContent = step.dataset.sea === 'none' ? '海水已排干' : '海平面 ' + Number(step.dataset.sea).toLocaleString('en-US').replace('-', '−') + ' m';
  };
  const observer = new IntersectionObserver(entries => { for (const e of entries) if (e.isIntersecting) activate(e.target); }, {rootMargin: '-45% 0px -45% 0px'});
  steps.forEach(s => observer.observe(s));
  activate(steps[0]);
  const table = $('strata-matrix'), head = el('tr');
  head.append(el('th', '', '模型'));
  for (const [, label, range] of strata) { const th = el('th'); th.append(label, el('span', '', range)); head.append(th); }
  table.querySelector('thead').append(head);
  for (const m of app.models) {
    const tr = el('tr'); tr.append(el('th', '', app.nameOf(m)));
    for (const [key] of strata) {
      const v = m.terrain.metrics.by_stratum[key].joint_accuracy['500'], td = el('td', '', (v * 100).toFixed(1));
      td.style.setProperty('--v', Math.max(0, (v - .3) / .7).toFixed(3)); tr.append(td);
    }
    table.querySelector('tbody').append(tr);
  }
}

// ---------- Hero, motion and boot ----------
function syncMotion() {
  $('motion-toggle').setAttribute('aria-pressed', String(motion));
  $('motion-toggle').setAttribute('aria-label', motion ? '暂停地球旋转' : '播放地球旋转');
  $('motion-toggle').textContent = motion ? 'Ⅱ' : '▶';
  globes.hero?.setAutoRotate(motion);
  for (const g of Object.values(globes)) g.setMotion(!reduceMotion.matches);
}
$('motion-toggle').addEventListener('click', () => { motion = !motion; syncMotion(); });
reduceMotion.addEventListener('change', () => { motion = !reduceMotion.matches; syncMotion(); });
function locate(lat, lon) {
  const id = Math.max(0, Math.min(89, Math.round((89 - lat) / 2))) * 180 + Math.max(0, Math.min(179, Math.round((lon + 179) / 2)));
  app.select(id, true);
  $('explore-message').textContent = '已定位到最近的采样点：' + coordinate(app.reference.points[id]) + '。';
}
document.querySelectorAll('[data-coordinate]').forEach(b => b.addEventListener('click', () => { if (app.reference) locate(...b.dataset.coordinate.split(',').map(Number)); }));
$('coordinate-form').addEventListener('submit', e => { e.preventDefault(); if (!app.reference) return; locate(Number($('latitude').value), Number($('longitude').value)); document.querySelector('.coordinate-picker').open = false; });
$('point').addEventListener('change', () => app.select(Math.round(Number($('point').value) || 0), true));
$('previous').addEventListener('click', () => app.select(app.selected - 1, true));
$('next').addEventListener('click', () => app.select(app.selected + 1, true));

function makeGlobe(id, options) {
  const globe = new ReliefGlobe($(id), {points: app.reference.points, ...options});
  if (globe.failed) { $(id).closest('figure').classList.add('no-webgl'); return null; }
  return globe;
}
Promise.all([app.json('data.json'), app.json('reference.details.json'), app.json('atlas.json')]).then(([data, ref, atlas]) => {
  if (data.schema !== 'land-water-unified/1.0' || data.sources.terrain.dataset_sha256 !== ref.dataset_sha256 || atlas.schema !== 'land-water-atlas/1.0') throw new Error('结果与参考版本不一致。');
  Object.assign(app, {metadata: data, reference: ref, atlas, models: data.models.filter(m => m.terrain && atlas.models[m.name])});
  app.referenceRows = ref.points.map(p => ({status: 'ok', label: p.truth, prediction: {type: p.truth ? 'Solid' : 'Water', elevation_m: p.elevation_m}}));
  $('terrain-count').textContent = app.models.length; $('count-all').textContent = data.models.length;
  $('hero-best').textContent = pct(app.models[0].terrain.metrics.joint_accuracy['500']); $('hero-best-name').textContent = app.nameOf(app.models[0]);
  $('reference-coverage').textContent = '16,200 个坐标中有 ' + data.height_scorable.toLocaleString('en-US') + ' 个有可用的高程参考，其余 ' + data.height_unscorable + ' 个只参与分类评分。';
  renderLeaderboard();
  setupTolerance();
  const heroCells = referenceCells();
  globes.hero = makeGlobe('hero-globe', {autoRotate: motion, view: {lat: 22, lon: 60}, distance: .92});
  globes.hero?.show(heroCells, {animate: false}); globes.hero?.setSea(0, {animate: false});
  globes.model = makeGlobe('model-globe', {view: {lat: 18, lon: 95}, onPick: id => app.select(id)});
  globes.abyss = makeGlobe('abyss-globe', {view: {lat: 8, lon: -150}, distance: .95});
  globes.abyss?.show(heroCells, {animate: false});
  setupGlobeControls(); showGlobeModel('reference'); globes.model?.select(app.selected);
  setupAbyss(); syncMotion();
  app.view.fillSelects(app.models); app.view.setPanel('a', 'reference'); app.view.setPanel('b', app.models[0].name);
  new ResizeObserver(() => app.queue()).observe($('atlas-map'));
  new ResizeObserver(() => app.queue()).observe($('scatter'));
  new ResizeObserver(() => globeStats()).observe($('fingerprint'));
  document.body.dataset.ready = 'true';
}).catch(error => { $('load-status').hidden = false; $('load-status').textContent = error.message; $('load-status').setAttribute('role', 'alert'); });
