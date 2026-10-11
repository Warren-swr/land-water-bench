// Canvas charts: tolerance curves, animated ranking, value fingerprint, height scatter.
import { colors } from './palette.js';

const FONT = '11px "SFMono-Regular",Consolas,monospace';
const pct = v => v == null ? '—' : (100 * v).toFixed(1) + '%';

export function setupCanvas(canvas) {
  const dpr = devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.font = FONT;
  return {ctx, w, h};
}

function fit(ctx, text, width) {
  if (ctx.measureText(text).width <= width) return text;
  while (text.length > 1 && ctx.measureText(text + '…').width > width) text = text.slice(0, -1);
  return text + '…';
}

function spreadLabels(items, top, bottom, gap) {
  items.sort((a, b) => a.y - b.y);
  for (let i = 0; i < items.length; i++) items[i].ly = Math.max(items[i].y, i ? items[i - 1].ly + gap : top);
  const overflow = items.length ? items[items.length - 1].ly - bottom : 0;
  if (overflow > 0) for (let i = items.length - 1; i >= 0; i--) {
    items[i].ly = Math.min(items[i].ly - overflow, i < items.length - 1 ? items[i + 1].ly - gap : bottom);
  }
  return items;
}

// Focus + context: every model is a quiet line, the focused one is the only colored line.
export class ToleranceChart {
  constructor(canvas, tip, {tolerances, series, official, onFocus}) {
    Object.assign(this, {canvas, tip, tolerances, series, official, onFocus});
    this.focus = null; this.hoverName = null; this.tolerance = official;
    canvas.addEventListener('pointermove', event => this.hover(event));
    canvas.addEventListener('pointerleave', () => { this.hoverName = null; this.tip.hidden = true; this.draw(); });
    canvas.addEventListener('click', event => { const hit = this.nearest(event); if (hit) this.onFocus?.(hit.s.name); });
  }
  geometry() {
    const {w, h} = this.size, left = 46, right = w - (w < 560 ? 108 : 168), top = 18, bottom = h - 38;
    const lows = this.series.map(s => Math.min(...s.curve));
    const y0 = Math.max(0, Math.floor(Math.min(...lows) * 10) / 10), y1 = 1;
    const t0 = this.tolerances[0], t1 = this.tolerances[this.tolerances.length - 1];
    return {left, right, top, bottom, y0, y1, x: t => left + (t - t0) / (t1 - t0) * (right - left),
            y: v => bottom - (v - y0) / (y1 - y0) * (bottom - top)};
  }
  draw() {
    const {ctx, w, h} = this.size = setupCanvas(this.canvas), g = this.geometry(), active = this.hoverName || this.focus;
    ctx.clearRect(0, 0, w, h);
    ctx.lineWidth = 1; ctx.strokeStyle = colors.grid; ctx.fillStyle = colors.dim; ctx.textAlign = 'right';
    for (let v = g.y0; v <= 1.0001; v += 0.1) {
      ctx.beginPath(); ctx.moveTo(g.left, g.y(v)); ctx.lineTo(g.right, g.y(v)); ctx.stroke();
      ctx.fillText(Math.round(v * 100) + '%', g.left - 8, g.y(v) + 4);
    }
    ctx.textAlign = 'center';
    for (const t of this.tolerances) if (t % 500 === 0 || t === this.tolerances[0]) ctx.fillText(t.toLocaleString('en-US'), g.x(t), g.bottom + 18);
    ctx.fillText('容差（m）', (g.left + g.right) / 2, h - 4);
    ctx.strokeStyle = colors.axis; ctx.beginPath(); ctx.moveTo(g.x(this.official), g.top); ctx.lineTo(g.x(this.official), g.bottom); ctx.stroke();
    ctx.fillStyle = colors.dim; ctx.textAlign = 'left'; ctx.fillText('官方 ±500 m', g.x(this.official) + 6, g.top + 10);
    ctx.strokeStyle = '#8b96b0'; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(g.x(this.tolerance), g.top); ctx.lineTo(g.x(this.tolerance), g.bottom); ctx.stroke();
    const path = s => { ctx.beginPath(); s.curve.forEach((v, i) => ctx[i ? 'lineTo' : 'moveTo'](g.x(this.tolerances[i]), g.y(v))); ctx.stroke(); };
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    for (const s of this.series) if (s.name !== active) { ctx.strokeStyle = '#3b4762'; ctx.lineWidth = 1.5; path(s); }
    const index = this.tolerances.indexOf(this.tolerance);
    for (const s of this.series) {
      if (s.name === active) continue;
      ctx.fillStyle = '#56627e'; ctx.beginPath(); ctx.arc(g.x(this.tolerance), g.y(s.curve[index]), 2.5, 0, Math.PI * 2); ctx.fill();
    }
    const focus = this.series.find(s => s.name === active);
    if (focus) {
      ctx.strokeStyle = colors.surface; ctx.lineWidth = 6; path(focus);
      ctx.strokeStyle = colors.a; ctx.lineWidth = 2.5; path(focus);
      const x = g.x(this.tolerance), y = g.y(focus.curve[index]);
      ctx.fillStyle = colors.a; ctx.strokeStyle = colors.surface; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = colors.ink; ctx.textAlign = x > g.right - 70 ? 'right' : 'left';
      ctx.fillText(pct(focus.curve[index]), x + (ctx.textAlign === 'left' ? 9 : -9), y - 9);
    }
    const end = this.tolerances.length - 1;
    const labels = spreadLabels(this.series.map(s => ({s, y: g.y(s.curve[end])})), g.top + 4, g.bottom, 13);
    for (const item of labels) {
      const on = item.s.name === active;
      ctx.strokeStyle = on ? colors.a : '#2f3a52'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(g.right + 3, item.y); ctx.lineTo(g.right + 12, item.ly); ctx.stroke();
      ctx.fillStyle = on ? colors.ink : colors.dim; ctx.textAlign = 'left';
      ctx.font = (on ? '600 ' : '') + FONT; ctx.fillText(fit(ctx, item.s.label, w - g.right - 18), g.right + 16, item.ly + 4); ctx.font = FONT;
    }
  }
  nearest(event) {
    if (!this.size) return null;
    const r = this.canvas.getBoundingClientRect(), mx = event.clientX - r.left, my = event.clientY - r.top, g = this.geometry();
    if (mx < g.left - 10 || mx > g.right + 10) return null;
    const step = this.tolerances[1] - this.tolerances[0];
    const i = Math.max(0, Math.min(this.tolerances.length - 1, Math.round((mx - g.left) / (g.right - g.left) * (this.tolerances.length - 1))));
    let best = null;
    for (const s of this.series) {
      const d = Math.abs(g.y(s.curve[i]) - my);
      if (d < 16 && (!best || d < best.d)) best = {s, d, i};
    }
    return best && {...best, step};
  }
  hover(event) {
    const hit = this.nearest(event), name = hit?.s.name || null;
    if (hit) {
      const r = this.canvas.getBoundingClientRect();
      this.tip.hidden = false;
      this.tip.replaceChildren(Object.assign(document.createElement('strong'), {textContent: hit.s.label}),
        document.createTextNode(' ±' + this.tolerances[hit.i].toLocaleString('en-US') + ' m · ' + pct(hit.s.curve[hit.i])));
      this.tip.style.left = Math.min(r.width - 200, event.clientX - r.left + 14) + 'px';
      this.tip.style.top = (event.clientY - r.top - 34) + 'px';
    } else this.tip.hidden = true;
    if (name !== this.hoverName) { this.hoverName = name; this.draw(); }
  }
}

// A ranked bar list that slides rows to their new places (FLIP) when the tolerance changes.
export class RankingBars {
  constructor(list, series, {onFocus}) {
    this.list = list; this.rows = new Map();
    for (const s of series) {
      const li = document.createElement('li'); li.dataset.model = s.name;
      li.innerHTML = '<span class="rk"></span><button type="button" class="nm"></button><span class="tr"><i></i></span><span class="vl"></span><span class="dl"></span>';
      li.querySelector('.nm').textContent = s.label;
      li.querySelector('.nm').addEventListener('click', () => onFocus(s.name));
      this.rows.set(s.name, {li, s}); list.append(li);
    }
  }
  update(index, baseIndex, focus, animate) {
    const order = [...this.rows.values()].sort((a, b) => b.s.curve[index] - a.s.curve[index] || a.s.name.localeCompare(b.s.name));
    const base = [...this.rows.values()].sort((a, b) => b.s.curve[baseIndex] - a.s.curve[baseIndex] || a.s.name.localeCompare(b.s.name));
    const first = new Map(order.map(r => [r.s.name, r.li.getBoundingClientRect().top]));
    order.forEach((row, rank) => {
      const was = base.indexOf(row) + 1, li = row.li, delta = was - (rank + 1);
      li.querySelector('.rk').textContent = String(rank + 1).padStart(2, '0');
      li.querySelector('.tr i').style.width = (row.s.curve[index] * 100) + '%';
      li.querySelector('.vl').textContent = pct(row.s.curve[index]);
      const dl = li.querySelector('.dl');
      dl.textContent = delta > 0 ? '▲' + delta : delta < 0 ? '▼' + -delta : '—';
      dl.className = 'dl' + (delta > 0 ? ' up' : delta < 0 ? ' down' : '');
      dl.title = delta ? '相对官方 ±500 m 排名' + (delta > 0 ? '上升 ' : '下降 ') + Math.abs(delta) + ' 名' : '与官方 ±500 m 排名相同';
      li.classList.toggle('focus', row.s.name === focus);
      this.list.append(li);
    });
    if (!animate) return;
    for (const row of order) {
      const dy = first.get(row.s.name) - row.li.getBoundingClientRect().top;
      if (!dy) continue;
      row.li.animate([{transform: `translateY(${dy}px)`}, {transform: 'translateY(0)'}], {duration: 650, easing: 'cubic-bezier(.2,.8,.2,1)'});
    }
  }
}

// Histogram of predicted heights against the reference outline; square-root counts keep both readable.
export function drawFingerprint(canvas, hist, model, reference, range = [-8000, 6000]) {
  const {ctx, w, h} = setupCanvas(canvas), left = 8, right = w - 8, top = 10, bottom = h - 22;
  const first = (range[0] - hist.start) / hist.step, last = (range[1] - hist.start) / hist.step;
  const bins = last - first, peak = Math.sqrt(Math.max(1, ...model.slice(first, last), ...reference.slice(first, last)));
  const x = i => left + (i - first) / bins * (right - left), y = c => bottom - Math.sqrt(c) / peak * (bottom - top);
  ctx.strokeStyle = colors.grid; ctx.beginPath(); ctx.moveTo(left, bottom + .5); ctx.lineTo(right, bottom + .5); ctx.stroke();
  const zero = x((0 - hist.start) / hist.step);
  ctx.strokeStyle = colors.axis; ctx.beginPath(); ctx.moveTo(zero, top); ctx.lineTo(zero, bottom); ctx.stroke();
  const bw = (right - left) / bins;
  ctx.fillStyle = colors.a;
  for (let i = first; i < last; i++) if (model[i]) ctx.fillRect(x(i) + .5, y(model[i]), Math.max(1, bw - 1), bottom - y(model[i]));
  ctx.strokeStyle = colors.ink; ctx.globalAlpha = .55; ctx.lineWidth = 1.2; ctx.beginPath();
  for (let i = first; i < last; i++) { ctx.lineTo(x(i), y(reference[i])); ctx.lineTo(x(i + 1), y(reference[i])); }
  ctx.stroke(); ctx.globalAlpha = 1;
  ctx.fillStyle = colors.dim; ctx.font = FONT;
  for (const v of [range[0], -4000, 0, 4000]) { ctx.textAlign = v === range[0] ? 'left' : 'center'; ctx.fillText((v / 1000) + 'k', x((v - hist.start) / hist.step), h - 6); }
}

export function drawScatter(canvas, {points, rows, selected, focusLabel}) {
  const {ctx, w, h} = setupCanvas(canvas), left = 58, right = w - 18, top = 24, bottom = h - 40, hits = [];
  let lo = -10000, hi = 8000; const data = [];
  if (rows) for (const p of points) {
    const r = rows[p.id];
    if (p.elevation_m == null || r?.status !== 'ok') continue;
    const e = r.prediction.elevation_m; lo = Math.min(lo, p.elevation_m, e); hi = Math.max(hi, p.elevation_m, e); data.push([p, r, e]);
  }
  const span = hi - lo, step = 10 ** Math.floor(Math.log10(span / 4)), tick = step * (span / 4 / step > 5 ? 10 : span / 4 / step > 2 ? 5 : 2);
  lo = Math.floor(lo / tick) * tick; hi = Math.ceil(hi / tick) * tick;
  const x = v => left + (v - lo) / (hi - lo) * (right - left), y = v => bottom - (v - lo) / (hi - lo) * (bottom - top);
  const fmt = v => Math.abs(v) >= 1e6 ? v.toExponential(0) : Math.abs(v) >= 1000 ? (v / 1000) + 'k' : String(v);
  ctx.strokeStyle = colors.grid; ctx.fillStyle = colors.dim; ctx.lineWidth = 1;
  for (let t = lo; t <= hi + tick * .01; t += tick) {
    ctx.beginPath(); ctx.moveTo(x(t), top); ctx.lineTo(x(t), bottom); ctx.moveTo(left, y(t)); ctx.lineTo(right, y(t)); ctx.stroke();
    ctx.textAlign = 'center'; ctx.fillText(fmt(t), x(t), bottom + 17); ctx.textAlign = 'right'; ctx.fillText(fmt(t), left - 8, y(t) + 4);
  }
  ctx.strokeStyle = colors.axis; ctx.beginPath(); ctx.moveTo(x(lo), y(lo)); ctx.lineTo(x(hi), y(hi)); ctx.stroke();
  ctx.fillStyle = colors.muted; ctx.textAlign = 'left'; ctx.fillText((focusLabel || '模型') + ' 预测（m）', left, 13);
  ctx.textAlign = 'right'; ctx.fillText('参考高程（m）', right, h - 4);
  ctx.globalAlpha = .28;
  for (const [p, r, e] of data) {
    const px = x(p.elevation_m), py = y(e), miss = Math.abs(e - p.elevation_m) > 500 || r.label !== p.truth;
    ctx.fillStyle = miss ? colors.error : colors.a; ctx.fillRect(px - 1, py - 1, 2.2, 2.2); hits.push({id: p.id, x: px, y: py});
  }
  ctx.globalAlpha = 1;
  const chosen = hits.find(d => d.id === selected);
  if (chosen) { ctx.strokeStyle = colors.ink; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(chosen.x, chosen.y, 6, 0, Math.PI * 2); ctx.stroke(); }
  return hits;
}
