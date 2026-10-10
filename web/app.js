"use strict";

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (value) => Number(value || 0).toLocaleString("en-US");
const pct = (value, digits = 2) => value == null ? "—" : (value * 100).toFixed(digits) + "%";
const coordinate = (id) => ({ lat: 89 - Math.floor(id / 180) * 2, lon: -179 + id % 180 * 2 });
const position = (id) => { const {lat, lon} = coordinate(id); return `${Math.abs(lat)}° ${lat >= 0 ? "N" : "S"} / ${Math.abs(lon)}° ${lon >= 0 ? "E" : "W"}`; };
const label = (value) => ({ "0": "Water", "1": "Land", "2": "格式无效", "3": "未决", "4": "未完成" }[value] || "未知");
const colors = { water: "#edf1fa", land: "#596cd8", correct: "#909cb9", fp: "#e97b42", fn: "#8b64c0", invalid: "#b08536", unknown: "#997ab9", difference: "#f0a552" };
const rgb = Object.fromEntries(Object.entries(colors).map(([key, hex]) => [key, [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))]));
let data = null, models = [], family = "all", search = "", mode = "map";
let modelA = null, modelB = null, hovered = null, inspected = null, pointToken = 0;
let detailsCache = new Map(), maps = new Map(), toastTimer = null;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const modelFor = (name) => models.find((model) => model.name === name);
const pixelsFor = (name) => name === "reference" ? data.truth : modelFor(name).pixels;
const nameFor = (name) => name === "reference" ? "GSHHG 参考地图" : modelFor(name).display_name;
const visibleModels = () => models.filter((m) => (family === "all" || (family === "other" ? !["GPT", "Claude"].includes(m.family) : m.family === family)) && `${m.name} ${m.display_name} ${m.family}`.toLowerCase().includes(search));

function renderLeaderboard() {
  const visible = visibleModels();
  $("ranking-body").innerHTML = visible.map((m) => {
    const rank = models.indexOf(m) + 1;
    const score = m.metrics.weighted_accuracy;
    const flag = m.metrics.excluded_unresolved ? `<span class="coverage-flag" title="这 ${m.metrics.excluded_unresolved} 个接口未决点已从本模型分子与分母中剔除">${m.metrics.excluded_unresolved} 未决</span>` : "";
    return `<tr class="${rank === 1 ? "leader" : ""}" data-model="${esc(m.name)}"><td class="rank-col"><span class="rank-number">${String(rank).padStart(2, "0")}</span></td><td><div class="model-cell"><span class="model-symbol family-${esc(m.family)}" aria-hidden="true">${esc(m.symbol)}</span><div><button class="model-name" data-inspect="${esc(m.name)}" title="查看 ${esc(m.name)} 的地图">${esc(m.display_name)}</button><div class="model-subline"><span>${esc(m.family.toUpperCase())}</span>${flag}</div></div></div></td><td class="accuracy-cell"><div class="accuracy-inner" title="面积加权准确率 ${pct(score, 4)}"><span class="score-value">${(score * 100).toFixed(2)}<small>%</small></span><span class="score-track" aria-hidden="true"><span style="width:${score * 100}%;animation-delay:${Math.min(rank * 35, 250)}ms"></span></span></div></td><td class="aux-col">${pct(m.metrics.land_recall)}</td><td class="aux-col">${pct(m.metrics.water_recall)}</td><td class="inspect-cell"><button class="inspect-button" data-inspect="${esc(m.name)}" aria-label="查看 ${esc(m.display_name)} 地图">↗</button></td></tr>`;
  }).join("");
  $("empty-state").hidden = visible.length > 0;
  $("result-count").textContent = `${String(visible.length).padStart(2, "0")} / ${String(models.length).padStart(2, "0")} MODELS`;
}

function chooseModel(name) {
  modelA = name;
  if (modelB === name) modelB = "reference";
  $("model-a").value = modelA;
  $("model-b").value = modelB;
  renderAtlas();
  $("atlas").scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "start" });
}

function colorFor(value, id, reference) {
  if (value === "2") return "invalid";
  if (value !== "0" && value !== "1") return "unknown";
  if (reference || mode === "map") return value === "1" ? "land" : "water";
  if (mode === "error") return value === data.truth[id] ? (value === "1" ? "correct" : "water") : value === "1" ? "fp" : "fn";
  const a = pixelsFor(modelA)[id], b = pixelsFor(modelB)[id];
  return "01".includes(a) && "01".includes(b) && a !== b ? "difference" : value === "1" ? "correct" : "water";
}

function makeMap(pixels, reference) {
  const canvas = document.createElement("canvas");
  canvas.width = 180; canvas.height = 90;
  const ctx = canvas.getContext("2d"), raster = ctx.createImageData(180, 90);
  for (let id = 0; id < pixels.length; id++) {
    const color = rgb[colorFor(pixels[id], id, reference)];
    raster.data.set([...color, 255], id * 4);
  }
  ctx.putImageData(raster, 0, 0);
  return canvas;
}

function paintMaps() {
  for (const key of ["a", "b"]) {
    const canvas = $("map-" + key), ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(maps.get(key), 0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#6375a51a"; ctx.lineWidth = 1; ctx.beginPath();
    for (let col = 15; col < 180; col += 15) { ctx.moveTo(col * 6, 0); ctx.lineTo(col * 6, 540); }
    for (let row = 15; row < 90; row += 15) { ctx.moveTo(0, row * 6); ctx.lineTo(1080, row * 6); }
    ctx.stroke();
    if (hovered != null) {
      const x = (hovered % 180 + .5) * 6, y = (Math.floor(hovered / 180) + .5) * 6;
      ctx.strokeStyle = "#344775aa"; ctx.lineWidth = 1; ctx.setLineDash([4, 4]); ctx.beginPath();
      ctx.moveTo(x, 0); ctx.lineTo(x, 540); ctx.moveTo(0, y); ctx.lineTo(1080, y); ctx.stroke();
      ctx.setLineDash([]); ctx.strokeStyle = "#233bb5"; ctx.lineWidth = 2; ctx.strokeRect(x - 9, y - 9, 18, 18);
    }
  }
}

function pairDifferences() {
  const a = pixelsFor(modelA), b = pixelsFor(modelB);
  const ids = [];
  for (let i = 0; i < a.length; i++) if ("01".includes(a[i]) && "01".includes(b[i]) && a[i] !== b[i]) ids.push(i);
  return ids;
}

function renderAtlas() {
  for (const [key, name] of [["a", modelA], ["b", modelB]]) {
    const reference = name === "reference";
    maps.set(key, makeMap(pixelsFor(name), reference));
    $("score-" + key).textContent = reference ? "REF" : pct(modelFor(name).metrics.weighted_accuracy);
    $("caption-" + key).textContent = reference ? "FULL-RESOLUTION REFERENCE" : `${num(modelFor(name).metrics.scored_points)} 个计分坐标`;
  }
  const differences = pairDifferences();
  $("pair-summary").innerHTML = `${modelB === "reference" ? "与参考不同" : "答案不同"} <strong>${num(differences.length)}</strong> 点`;
  $("pair-summary").title = "仅比较双方均有有效 Land / Water 标签的采样点";
  $("difference-example").disabled = !differences.length;
  $("difference-example").innerHTML = `${modelB === "reference" ? "与参考不同的点" : "两模型的分歧点"} <span>↗</span>`;
  const legend = mode === "map" ? [["land", "陆地"], ["water", "水域"]] : mode === "error" ? [["fp", "水域误判为陆地"], ["fn", "陆地误判为水域"]] : [["difference", "答案不同"], ["correct", "答案一致"]];
  const all = [...legend, ["invalid", "无效"], ["unknown", "未决"]];
  $("legend").innerHTML = all.map(([key, text]) => `<span><i style="background:${colors[key]}"></i>${text}</span>`).join("");
  $("explore-message").textContent = "";
  paintMaps();
}

function mapPoint(event, canvas) {
  const bounds = canvas.getBoundingClientRect();
  const x = Math.min(179, Math.max(0, Math.floor((event.clientX - bounds.left) / bounds.width * 180)));
  const y = Math.min(89, Math.max(0, Math.floor((event.clientY - bounds.top) / bounds.height * 90)));
  return y * 180 + x;
}

function setupMap(canvas) {
  canvas.addEventListener("mousemove", (event) => {
    if (!data) return;
    hovered = mapPoint(event, canvas); paintMaps();
    $("hover-coordinate").textContent = position(hovered);
    const tooltip = $("map-tooltip");
    tooltip.innerHTML = `<strong>${position(hovered)}</strong><br>A · ${esc(label(pixelsFor(modelA)[hovered]))}<br>B · ${esc(label(pixelsFor(modelB)[hovered]))}`;
    tooltip.hidden = false;
    tooltip.style.left = Math.max(8, Math.min(event.clientX + 16, innerWidth - 220)) + "px";
    tooltip.style.top = Math.max(8, Math.min(event.clientY + 15, innerHeight - 100)) + "px";
  });
  canvas.addEventListener("mouseleave", () => { $("map-tooltip").hidden = true; hovered = $("point-dialog").open ? inspected : null; if (data) paintMaps(); });
  canvas.addEventListener("click", (event) => { if (data) showPoint(mapPoint(event, canvas)); });
  canvas.addEventListener("keydown", (event) => { if (event.key === "Enter" && data) showPoint(hovered ?? 3826); });
}

async function getDetails(name) {
  if (detailsCache.has(name)) return detailsCache.get(name);
  const path = name === "reference" ? "reference.details.json" : modelFor(name).details_url;
  const url = new URL(path, location.href);
  if (url.origin !== location.origin) throw new Error("不能读取外部数据地址");
  const request = fetch(url, { cache: "force-cache" }).then((response) => {
    if (!response.ok) throw new Error("回答暂时无法读取，请稍后重试。");
    return response.json();
  }).catch((error) => { detailsCache.delete(name); throw error; });
  detailsCache.set(name, request);
  return request;
}

async function showPoint(id, all = false) {
  inspected = hovered = id;
  const token = ++pointToken;
  $("map-tooltip").hidden = true;
  $("point-title").textContent = position(id);
  $("point-truth").innerHTML = `<span class="ref-tag">REFERENCE</span> ${label(data.truth[id])} · ${data.truth[id] === "1" ? "陆地" : "水域"}`;
  $("point-reference").textContent = "读取参考…";
  $("point-reference").classList.remove("warning");
  $("point-answers").textContent = "正在读取原始回答…";
  $("all-answers").hidden = all;
  if (!$("point-dialog").open) $("point-dialog").showModal();
  paintMaps();
  const selected = all ? models : [...new Set([modelA, modelB])].filter((name) => name !== "reference").map(modelFor);
  try {
    const [reference, answers] = await Promise.all([
      getDetails("reference").then((rows) => rows[id]),
      Promise.all(selected.map(async (model) => ({ model, row: (await getDetails(model.name))[id] })))
    ]);
    if (token !== pointToken) return;
    const category = {land:"陆地",ocean:"海洋",lake:"湖泊",river_lake:"河流湖",island_in_lake:"湖中岛",pond_in_island:"岛内湖",grounded_ice:"接地冰体",floating_ice_shelf:"漂浮冰架"}[reference.category] || "参考边界";
    $("point-reference").textContent = `GSHHG · ${category}${reference.lake_names.length ? " · " + reference.lake_names.join("、") : ""}。${reference.comparison_disagreement ? `此点与 Natural Earth (${label(reference.natural_earth_label)}) 存在分歧；当前按 GSHHG 计分。` : "两个参考数据源在此点一致。"}${reference.ice_policy_sensitive ? " 此点对冰架分类口径敏感。" : ""}`;
    $("point-reference").classList.toggle("warning", reference.comparison_disagreement || reference.ice_policy_sensitive);
    $("point-answers").innerHTML = answers.map(({model, row}) => {
      const correct = row.status === "ok" && String(row.label) === data.truth[id];
      const status = row.status === "ok" ? (correct ? "✓ 正确" : "× 与参考不符") : row.status === "invalid" ? "格式无效 · 计错" : "未决 · 不计分";
      const body = row.text ?? "接口未返回可评分答案。";
      const content = `<code>${esc(body)}</code>`;
      return `<article class="answer-row" data-model="${esc(model.name)}"><header><h3>${esc(model.display_name)}</h3><span class="answer-verdict ${row.status === "infra_error" ? "unknown" : correct ? "" : "wrong"}">${status}</span></header><p class="answer-label">${row.status === "ok" ? "提取答案：" + label(row.label) : "没有有效标签"}</p>${body.length > 180 ? `<details><summary>查看完整正文 (${num(body.length)} 字符)</summary>${content}</details>` : content}</article>`;
    }).join("");
  } catch (error) { if (token === pointToken) $("point-answers").textContent = error.message; }
}

function downloadCSV() {
  const lines = [["排名", "模型", "面积加权准确率(%)", "陆地召回率(%)", "水域召回率(%)", "计分坐标", "格式无效", "剔除未决"]];
  for (const m of visibleModels()) lines.push([models.indexOf(m) + 1, m.name, (m.metrics.weighted_accuracy * 100).toFixed(4), (m.metrics.land_recall * 100).toFixed(4), (m.metrics.water_recall * 100).toFixed(4), m.metrics.scored_points, m.metrics.invalid, m.metrics.excluded_unresolved]);
  const csv = "\uFEFF" + lines.map((row) => row.map((cell) => '"' + String(cell).replace(/"/g, '""') + '"').join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], {type:"text/csv;charset=utf-8"}));
  const link = document.createElement("a"); link.href = url; link.download = "land-water-leaderboard.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $("toast").hidden = false; $("toast").textContent = `已导出 ${visibleModels().length} 个模型的成绩`;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 2500);
}

function startGlobe(truth) {
  const canvas = $("globe"), ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height, cx = w / 2, cy = h / 2 + 2, radius = 255;
  const samples = [];
  for (let id = 0; id < truth.length; id++) {
    const {lat, lon} = coordinate(id);
    if (truth[id] === "1" || (id % 3 === 0 && Math.floor(id / 180) % 3 === 0)) samples.push({ lat: lat * Math.PI / 180, lon: lon * Math.PI / 180, land: truth[id] === "1" });
  }
  let rotation = .12, playing = !reduceMotion.matches, inView = true, frame = 0, last = 0;
  function project(lat, lon) {
    const x = Math.cos(lat) * Math.sin(lon + rotation), y = Math.sin(lat), z = Math.cos(lat) * Math.cos(lon + rotation);
    return [cx + radius * x, cy - radius * (y * .974 - z * .225), z * .974 + y * .225];
  }
  function draw() {
    ctx.clearRect(0, 0, w, h);
    const glow = ctx.createRadialGradient(cx, cy, radius * .45, cx, cy, radius * 1.3);
    glow.addColorStop(0, "#698be926"); glow.addColorStop(.65, "#7396e312"); glow.addColorStop(1, "#7396e300");
    ctx.fillStyle = glow; ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "#a1afcf"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(cx, cy, radius + .5, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = "#7085b34d"; ctx.beginPath();
    for (let meridian = -180; meridian < 180; meridian += 30) {
      let first = true;
      for (let latitude = -90; latitude <= 90; latitude += 3) {
        const p = project(latitude * Math.PI / 180, meridian * Math.PI / 180);
        if (p[2] < 0) { first = true; continue; }
        if (first) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); first = false;
      }
    }
    for (let latitude = -60; latitude <= 60; latitude += 30) {
      let first = true;
      for (let longitude = -180; longitude <= 180; longitude += 3) {
        const p = project(latitude * Math.PI / 180, longitude * Math.PI / 180);
        if (p[2] < 0) { first = true; continue; }
        if (first) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); first = false;
      }
    }
    ctx.stroke();
    for (const point of samples) {
      const [x, y, depth] = project(point.lat, point.lon);
      if (depth < -.08) continue;
      const alpha = point.land ? .35 + .6 * Math.max(0, depth) : .18 + .16 * Math.max(0, depth);
      ctx.fillStyle = point.land ? `rgba(49,73,153,${alpha})` : `rgba(126,141,178,${alpha})`;
      const size = point.land ? 1.15 + Math.max(0, depth) * .75 : .8;
      ctx.fillRect(x - size / 2, y - size / 2, size * 1.2, size * 1.2);
    }
    ctx.strokeStyle = "#8294bd99"; ctx.lineWidth = .8; ctx.beginPath();
    ctx.ellipse(cx, cy, radius * 1.15, radius * .35, -.24, 0, Math.PI * 2); ctx.stroke();
    const angle = rotation * 1.7 + 1.3, ex = Math.cos(angle) * radius * 1.15, ey = Math.sin(angle) * radius * .35;
    const sx = cx + ex * Math.cos(-.24) - ey * Math.sin(-.24), sy = cy + ex * Math.sin(-.24) + ey * Math.cos(-.24);
    ctx.fillStyle = "#d9793b"; ctx.shadowColor = "#edba91"; ctx.shadowBlur = 12; ctx.beginPath(); ctx.arc(sx, sy, 2.8, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
  }
  function tick(time) {
    if (time - last >= 32) { rotation += Math.min(50, time - last || 32) * .000055; last = time; draw(); }
    frame = requestAnimationFrame(tick);
  }
  function updateMotion() {
    cancelAnimationFrame(frame);
    $("motion-toggle").textContent = playing ? "Ⅱ" : "▷";
    $("motion-toggle").setAttribute("aria-pressed", String(playing));
    $("motion-toggle").setAttribute("aria-label", playing ? "暂停地球动画" : "播放地球动画");
    if (playing && inView && !document.hidden) { last = performance.now(); frame = requestAnimationFrame(tick); }
  }
  $("motion-toggle").addEventListener("click", () => { playing = !playing; updateMotion(); });
  reduceMotion.addEventListener("change", () => { playing = !reduceMotion.matches; updateMotion(); });
  new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; updateMotion(); }).observe(canvas);
  document.addEventListener("visibilitychange", updateMotion);
  draw(); updateMotion();
}

async function init() {
  try {
    const response = await fetch("data.json", {cache:"no-store"});
    if (!response.ok) throw new Error("数据读取失败");
    data = await response.json();
    if (!Array.isArray(data.models) || data.truth.length !== 16200 || data.models.some((m) => m.pixels.length !== 16200 || m.metrics.weighted_accuracy == null)) throw new Error("排行榜数据不完整");
    models = [...data.models].sort((a, b) => b.metrics.weighted_accuracy - a.metrics.weighted_accuracy || a.name.localeCompare(b.name));
    $("model-count").textContent = String(models.length).padStart(2, "0");
    $("count-all").textContent = models.length;
    modelA = models[0].name; modelB = models[1]?.name || "reference";
    const options = models.map((m) => `<option value="${esc(m.name)}">${esc(m.display_name)}</option>`).join("");
    $("model-a").innerHTML = options;
    $("model-b").innerHTML = options + '<option value="reference">GSHHG 参考地图</option>';
    $("model-a").value = modelA; $("model-b").value = modelB;
    const unknown = models.reduce((sum, m) => sum + m.metrics.excluded_unresolved, 0);
    const invalid = models.reduce((sum, m) => sum + m.metrics.invalid, 0);
    $("coverage-note").textContent = `本榜单共剔除 ${num(unknown)} 个未决结果；${num(invalid)} 条格式无效答案仍计错。每个模型的原始采样范围均为 16,200 点。`;
    $("model-settings").innerHTML = models.map((m) => `<div><span>${esc(m.display_name)}</span><span>${esc(m.settings)}</span></div>`).join("");
    renderLeaderboard(); renderAtlas(); startGlobe(data.truth);
    document.body.dataset.ready = "true";
  } catch (error) {
    $("error").hidden = false;
    $("error").textContent = `${error.message}。请刷新页面重试。`;
  }
}

$("ranking-body").addEventListener("click", (event) => { const target = event.target.closest("[data-inspect]"); if (target) chooseModel(target.dataset.inspect); });
document.querySelectorAll("[data-family]").forEach((button) => button.addEventListener("click", () => {
  if (!data) return;
  family = button.dataset.family;
  document.querySelectorAll("[data-family]").forEach((item) => { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button)); });
  renderLeaderboard();
}));
$("model-search").addEventListener("input", (event) => { search = event.target.value.trim().toLowerCase(); if (data) renderLeaderboard(); });
$("reset-filters").addEventListener("click", () => { $("model-search").value = ""; search = ""; document.querySelector('[data-family="all"]').click(); });
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !$("point-dialog").open) { event.preventDefault(); $("model-search").focus(); }
});
for (const key of ["a", "b"]) {
  $("model-" + key).addEventListener("change", (event) => { if (key === "a") modelA = event.target.value; else modelB = event.target.value; renderAtlas(); });
  setupMap($("map-" + key));
}
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  if (!data) return;
  mode = button.dataset.mode;
  document.querySelectorAll("[data-mode]").forEach((item) => { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button)); });
  renderAtlas();
}));
$("lake-example").addEventListener("click", () => { if (data) showPoint(3826); });
$("difference-example").addEventListener("click", () => { const differences = pairDifferences(); if (differences.length) showPoint(differences[0]); });
$("reference-example").addEventListener("click", () => { if (data) showPoint(data.reference_disagreement_ids[0]); });
$("coordinate-form").addEventListener("submit", (event) => {
  event.preventDefault(); if (!data) return;
  const row = Math.min(89, Math.max(0, Math.round((89 - Number($("latitude").value)) / 2)));
  const col = Math.min(179, Math.max(0, Math.round((Number($("longitude").value) + 179) / 2)));
  const id = row * 180 + col;
  $("explore-message").textContent = `最近的采样坐标：${position(id)}`;
  showPoint(id);
});
$("close-dialog").addEventListener("click", () => $("point-dialog").close());
$("point-dialog").addEventListener("close", () => { pointToken++; hovered = null; if (data) paintMaps(); });
$("point-dialog").addEventListener("click", (event) => {
  if (event.target !== $("point-dialog")) return;
  const r = event.target.getBoundingClientRect();
  if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close();
});
$("all-answers").addEventListener("click", () => { if (inspected != null) showPoint(inspected, true); });
$("download-csv").addEventListener("click", () => { if (data) downloadCSV(); });
init();
