// Instanced relief globe: one radial column per 2° grid cell, morphing between datasets.
import {
  WebGLRenderer, Scene, PerspectiveCamera, Group, Mesh, Points, LineSegments, BoxGeometry, SphereGeometry,
  BufferGeometry, InstancedBufferGeometry, Float32BufferAttribute, InstancedBufferAttribute, ShaderMaterial,
  MeshBasicMaterial, PointsMaterial, LineBasicMaterial, Color, Vector2, Vector3, Raycaster, Sphere,
  AdditiveBlending, BackSide
} from './vendor/three.module.min.js';
import { heightRGB, deltaRGB, hexRGB, colors } from './palette.js';

const TERRAIN_K = 0.06 / 8000;   // ≈ 50× radial exaggeration
const ERROR_K = 0.1 / 5000;
const CORE = 0.915;
export const terrainRadius = h => 1 + Math.max(-11000, Math.min(9000, h)) * TERRAIN_K;
const rgb01 = rgb => rgb.map(v => v / 255);
const surface01 = {S: rgb01(hexRGB(colors.solid)), W: rgb01(hexRGB(colors.water))};
const status01 = {I: rgb01(hexRGB(colors.invalid)), U: rgb01(hexRGB(colors.unresolved)), '-': rgb01(hexRGB(colors.masked))};

export function terrainCells(heights, states = null) {
  const n = heights.length, cells = blank(n);
  for (let i = 0; i < n; i++) {
    const h = heights[i], state = states ? states[i] : 'S';
    cells.bottom[i] = CORE;
    if (h == null || !Number.isFinite(h) || !'SW'.includes(state)) {
      // No usable height: keep the cell flat at sea level, coloured by its surface type when known.
      cells.top[i] = 'SW'.includes(state) ? 0.999 : 1.002; cells.ref[i] = NaN;
      cells.color.set(state === 'S' ? surface01.S : state === 'W' ? surface01.W : status01[state] || status01['-'], i * 3);
    } else {
      cells.top[i] = terrainRadius(h); cells.color.set(rgb01(heightRGB(h)), i * 3); cells.ref[i] = h;
    }
  }
  return cells;
}

// Signed error spikes: too high grows outward (red), too low sinks inward (blue); hits stay flat.
export function errorCells(heights, states, points, tolerance = 500) {
  const n = heights.length, cells = blank(n), flat = rgb01(hexRGB(colors.correct)), miss = rgb01(hexRGB(colors.error));
  for (let i = 0; i < n; i++) {
    const p = points[i], h = heights[i], state = states[i];
    cells.ref[i] = NaN; cells.bottom[i] = 0.996; cells.top[i] = 1.0;
    if (!'SW'.includes(state)) { cells.color.set(status01[state] || status01['-'], i * 3); continue; }
    if (p.elevation_m == null) { cells.color.set(status01['-'], i * 3); continue; }
    const delta = h - p.elevation_m, typeWrong = (state === 'S') !== Boolean(p.truth);
    if (Math.abs(delta) <= tolerance && !typeWrong) { cells.color.set(flat, i * 3); continue; }
    const r = 1 + Math.max(-5000, Math.min(5000, delta)) * ERROR_K;
    cells.bottom[i] = Math.min(r, 0.996); cells.top[i] = Math.max(r, 1.0);
    cells.color.set(Math.abs(delta) <= tolerance ? miss : rgb01(deltaRGB(delta, 3000)), i * 3);
  }
  return cells;
}

function blank(n) {
  return {bottom: new Float32Array(n), top: new Float32Array(n), color: new Float32Array(n * 3), ref: new Float32Array(n)};
}

const columnVertex = `
attribute vec2 aCell; attribute vec2 aRange0; attribute vec2 aRange1;
attribute vec3 aColor0; attribute vec3 aColor1; attribute float aRef; attribute float aIndex;
uniform float uMix, uWidth, uSelected, uBandOn; uniform vec2 uBand;
varying vec3 vColor; varying vec3 vNormalV; varying vec3 vUpV; varying vec3 vViewPos; varying float vGlow;
void main() {
  float lat = aCell.x, lon = aCell.y, cl = cos(lat);
  vec3 up = vec3(cl * sin(lon), sin(lat), cl * cos(lon));
  vec3 east = vec3(cos(lon), 0.0, -sin(lon));
  vec3 north = cross(up, east);
  vec2 range = mix(aRange0, aRange1, uMix);
  float r = mix(range.x, range.y, position.y);
  vec3 p = up * r + east * (position.x * uWidth * max(cl, 0.03) * r) + north * (position.z * uWidth * r);
  vNormalV = normalize(normalMatrix * normalize(east * normal.x + up * normal.y + north * normal.z));
  vUpV = normalize(normalMatrix * up);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vViewPos = mv.xyz;
  vColor = mix(aColor0, aColor1, uMix);
  vGlow = abs(aIndex - uSelected) < 0.5 ? 2.0 : uBandOn * step(uBand.x, aRef) * step(aRef, uBand.y);
  gl_Position = projectionMatrix * mv;
}`;
const columnFragment = `
uniform vec3 uLight; uniform float uDim;
varying vec3 vColor; varying vec3 vNormalV; varying vec3 vUpV; varying vec3 vViewPos; varying float vGlow;
void main() {
  vec3 n = normalize(mix(normalize(vNormalV), normalize(vUpV), 0.6));
  float diffuse = max(dot(n, normalize(uLight)), 0.0);
  float rim = pow(1.0 - max(dot(n, normalize(-vViewPos)), 0.0), 3.0);
  vec3 c = vColor * (0.5 + 0.62 * diffuse) + rim * vec3(0.05, 0.08, 0.14);
  if (vGlow > 1.5) c = mix(c, vec3(1.0), 0.7);
  else if (vGlow > 0.5) c = c * 1.3 + vec3(0.04, 0.08, 0.15);
  else c *= uDim;
  gl_FragColor = vec4(c, 1.0);
}`;
const fresnelVertex = `
varying vec3 vN; varying vec3 vV;
void main() { vN = normalize(normalMatrix * normal); vec4 mv = modelViewMatrix * vec4(position, 1.0); vV = mv.xyz; gl_Position = projectionMatrix * mv; }`;
const waterFragment = `
uniform vec3 uDeep, uEdge; uniform float uOpacity;
varying vec3 vN; varying vec3 vV;
void main() { float f = pow(1.0 - abs(dot(normalize(vN), normalize(-vV))), 2.2);
  gl_FragColor = vec4(mix(uDeep, uEdge, f), uOpacity * (0.62 + 0.38 * f)); }`;
const haloFragment = `
uniform vec3 uColor; varying vec3 vN; varying vec3 vV;
void main() { float f = pow(max(dot(normalize(vN), normalize(vV)), 0.0), 1.5); gl_FragColor = vec4(uColor, f * 0.8); }`;

export class ReliefGlobe {
  constructor(canvas, {points, autoRotate = false, onPick = null, view = {lat: 18, lon: 20}, distance = 1}) {
    this.canvas = canvas; this.points = points; this.onPick = onPick;
    this.autoRotate = autoRotate; this.motion = true; this.visible = false;
    this.yaw = -view.lon * Math.PI / 180; this.tilt = view.lat * Math.PI / 180; this.spin = 0;
    this.distanceScale = distance; this.selected = -1; this.frame = 0; this.last = 0; this.tweens = [];
    try {
      this.renderer = new WebGLRenderer({canvas, antialias: true, alpha: true, preserveDrawingBuffer: true});
    } catch (error) {
      canvas.dataset.webgl = 'unavailable'; this.failed = true; return;
    }
    canvas.dataset.webgl = 'ready';
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.scene = new Scene();
    this.camera = new PerspectiveCamera(30, 1, 0.1, 100);
    this.group = new Group(); this.scene.add(this.group);
    this.buildColumns(); this.buildShells(); this.buildStars(); this.buildBeacon();
    this.bindPointer();
    new ResizeObserver(() => this.resize()).observe(canvas);
    new IntersectionObserver(entries => { this.visible = entries[0].isIntersecting; this.wake(); }).observe(canvas);
    this.resize();
  }
  buildColumns() {
    const n = this.points.length, box = new BoxGeometry(1, 1, 1); box.translate(0, 0.5, 0);
    const geometry = new InstancedBufferGeometry();
    geometry.index = box.index;
    for (const name of ['position', 'normal']) geometry.setAttribute(name, box.getAttribute(name));
    const cell = new Float32Array(n * 2), index = new Float32Array(n);
    this.points.forEach((p, i) => { cell[i * 2] = p.lat * Math.PI / 180; cell[i * 2 + 1] = p.lon * Math.PI / 180; index[i] = i; });
    const attr = (name, size, array = new Float32Array(n * size)) => {
      const a = new InstancedBufferAttribute(array, size); geometry.setAttribute(name, a); return a;
    };
    attr('aCell', 2, cell); attr('aIndex', 1, index);
    this.attrs = {range0: attr('aRange0', 2), range1: attr('aRange1', 2), color0: attr('aColor0', 3),
                  color1: attr('aColor1', 3), ref: attr('aRef', 1)};
    geometry.instanceCount = n;
    geometry.boundingSphere = new Sphere(new Vector3(), 1.2);
    this.uniforms = {uMix: {value: 1}, uWidth: {value: 2 * Math.PI / 180 * 1.015}, uSelected: {value: -1},
      uBandOn: {value: 0}, uBand: {value: new Vector2(0, 0)}, uLight: {value: new Vector3(-0.35, 0.45, 1.0)},
      uDim: {value: 1}};
    this.columns = new Mesh(geometry, new ShaderMaterial({uniforms: this.uniforms, vertexShader: columnVertex, fragmentShader: columnFragment}));
    this.columns.frustumCulled = false;
    this.group.add(this.columns);
    this.core = new Mesh(new SphereGeometry(CORE + 0.002, 64, 32), new MeshBasicMaterial({color: new Color('#0a1a44')}));
    this.group.add(this.core);
  }
  buildShells() {
    this.water = new Mesh(new SphereGeometry(1, 96, 48), new ShaderMaterial({
      uniforms: {uDeep: {value: new Color('#0d3a8a')}, uEdge: {value: new Color('#7fc0ff')}, uOpacity: {value: 0.78}},
      vertexShader: fresnelVertex, fragmentShader: waterFragment, transparent: true, depthWrite: false}));
    this.water.visible = false; this.seaLevel = null; this.group.add(this.water);
    const halo = new Mesh(new SphereGeometry(1.16, 64, 32), new ShaderMaterial({
      uniforms: {uColor: {value: new Color('#3f7fe8')}}, vertexShader: fresnelVertex, fragmentShader: haloFragment,
      transparent: true, depthWrite: false, side: BackSide, blending: AdditiveBlending}));
    this.scene.add(halo);
  }
  buildStars() {
    let seed = 7; const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const positions = [];
    for (let i = 0; i < 900; i++) {
      const z = random() * 2 - 1, a = random() * Math.PI * 2, r = 24 + random() * 12, s = Math.sqrt(1 - z * z);
      positions.push(r * s * Math.cos(a), r * s * Math.sin(a), r * z);
    }
    const geometry = new BufferGeometry(); geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
    this.scene.add(new Points(geometry, new PointsMaterial({color: new Color('#8fa4cf'), size: 0.07, transparent: true, opacity: 0.55})));
  }
  buildBeacon() {
    const geometry = new BufferGeometry(); geometry.setAttribute('position', new Float32BufferAttribute([0, 0, 0, 0, 0, 0], 3));
    this.beacon = new LineSegments(geometry, new LineBasicMaterial({color: new Color('#ffffff'), transparent: true, opacity: 0.85}));
    this.beacon.visible = false; this.group.add(this.beacon);
  }
  resize() {
    if (this.failed) return;
    const w = this.canvas.clientWidth || 1, h = this.canvas.clientHeight || 1, aspect = w / h;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = aspect;
    this.camera.position.set(0, 0, 1.24 / Math.tan(15 * Math.PI / 180) / Math.min(1, aspect) * this.distanceScale);
    this.camera.updateProjectionMatrix();
    this.render();
  }
  // Morph from whatever is on screen now to `cells`.
  show(cells, {animate = true} = {}) {
    if (this.failed) return;
    const {range0, range1, color0, color1, ref} = this.attrs, m = this.uniforms.uMix.value, n = this.points.length;
    for (let i = 0; i < n; i++) {
      for (let k = 0; k < 2; k++) range0.array[i * 2 + k] += (range1.array[i * 2 + k] - range0.array[i * 2 + k]) * m;
      for (let k = 0; k < 3; k++) color0.array[i * 3 + k] += (color1.array[i * 3 + k] - color0.array[i * 3 + k]) * m;
      range1.array[i * 2] = cells.bottom[i]; range1.array[i * 2 + 1] = cells.top[i];
    }
    if (!this.hasData) { range0.array.set(range1.array); color0.array.set(cells.color); }
    color1.array.set(cells.color); ref.array.set(cells.ref);
    for (const a of [range0, range1, color0, color1, ref]) a.needsUpdate = true;
    const instant = !animate || !this.motion || !this.hasData;
    this.hasData = true;
    this.tween('mix', instant ? 0 : 1100, 0, 1, v => { this.uniforms.uMix.value = v; });
    this.placeBeacon();
  }
  setSea(level, {animate = true} = {}) {
    if (this.failed) return;
    const from = this.seaLevel ?? 0, to = level ?? -11000;
    this.seaLevel = level;
    this.water.visible = true;
    this.tween('sea', animate && this.motion ? 1400 : 0, from, to, v => {
      this.water.scale.setScalar(terrainRadius(v));
      this.water.visible = level != null || v > -10900;
    });
  }
  setBand(range) {
    if (this.failed) return;
    this.uniforms.uBandOn.value = range ? 1 : 0;
    this.uniforms.uDim.value = range ? 0.7 : 1;
    if (range) this.uniforms.uBand.value.set(range[0], range[1]);
    this.wake();
  }
  select(id, {fly = false} = {}) {
    if (this.failed) return;
    this.selected = id; this.uniforms.uSelected.value = id;
    this.placeBeacon();
    if (fly && id >= 0) this.flyTo(this.points[id].lat, this.points[id].lon); else this.wake();
  }
  placeBeacon() {
    const p = this.points[this.selected];
    if (!p || !this.beacon) { if (this.beacon) this.beacon.visible = false; return; }
    const lat = p.lat * Math.PI / 180, lon = p.lon * Math.PI / 180;
    const up = [Math.cos(lat) * Math.sin(lon), Math.sin(lat), Math.cos(lat) * Math.cos(lon)];
    const array = this.beacon.geometry.getAttribute('position');
    array.array.set([...up.map(v => v * 1.0), ...up.map(v => v * 1.2)]); array.needsUpdate = true;
    this.beacon.visible = true; this.wake();
  }
  flyTo(latDeg, lonDeg) {
    const targetYaw = -lonDeg * Math.PI / 180, targetTilt = Math.max(-1.2, Math.min(1.2, latDeg * Math.PI / 180));
    let delta = ((targetYaw - this.yaw) % (2 * Math.PI) + 3 * Math.PI) % (2 * Math.PI) - Math.PI;
    const yaw0 = this.yaw, tilt0 = this.tilt; this.spin = 0;
    this.tween('fly', this.motion ? 900 : 0, 0, 1, t => { this.yaw = yaw0 + delta * t; this.tilt = tilt0 + (targetTilt - tilt0) * t; });
  }
  setMotion(on) { this.motion = on; this.wake(); }
  setAutoRotate(on) { this.autoRotate = on; this.wake(); }
  tween(key, duration, from, to, apply) {
    this.tweens = this.tweens.filter(t => t.key !== key);
    if (!duration) { apply(to); this.wake(); return; }
    this.tweens.push({key, start: performance.now(), duration, from, to, apply});
    this.wake();
  }
  wake() {
    if (this.failed || this.frame) return;
    this.frame = requestAnimationFrame(time => this.tick(time));
  }
  tick(time) {
    this.frame = 0;
    const dt = this.last ? Math.min(64, time - this.last) : 16; this.last = time;
    for (const t of this.tweens) {
      const k = Math.min(1, (time - t.start) / t.duration), e = k < .5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
      t.apply(t.from + (t.to - t.from) * e); t.done = k >= 1;
    }
    this.tweens = this.tweens.filter(t => !t.done);
    if (!this.dragging) {
      if (Math.abs(this.spin) > 1e-5) { this.yaw += this.spin * dt; this.spin *= 0.94; }
      else if (this.autoRotate && this.motion) this.yaw += dt * 0.00009;
    }
    this.render();
    const busy = this.tweens.length || this.dragging || Math.abs(this.spin) > 1e-5 || (this.autoRotate && this.motion);
    if (busy && this.visible && !document.hidden) this.frame = requestAnimationFrame(next => this.tick(next));
    else this.last = 0;
  }
  render() {
    if (this.failed || !this.hasData) return;
    this.group.rotation.set(this.tilt, this.yaw, 0, 'XYZ');
    this.renderer.render(this.scene, this.camera);
  }
  bindPointer() {
    let first = null, last = null;
    this.canvas.addEventListener('pointerdown', event => {
      first = last = {x: event.clientX, y: event.clientY, t: performance.now()}; this.moved = false;
      this.canvas.setPointerCapture(event.pointerId);
    });
    this.canvas.addEventListener('pointermove', event => {
      if (!last) { this.hover?.(this.pick(event)); return; }
      const dx = event.clientX - last.x, dy = event.clientY - last.y, now = performance.now();
      if (!this.moved && Math.hypot(event.clientX - first.x, event.clientY - first.y) < 5) return;
      this.moved = true; this.dragging = true;
      this.yaw += dx * 0.006; this.tilt = Math.max(-1.25, Math.min(1.25, this.tilt + dy * 0.005));
      this.spin = dx * 0.006 / Math.max(8, now - last.t);
      last = {x: event.clientX, y: event.clientY, t: now}; this.wake();
    });
    const end = event => {
      if (last && !this.moved) { const id = this.pick(event); if (id != null) this.onPick?.(id); }
      first = last = null; this.dragging = false; this.wake();
    };
    this.canvas.addEventListener('pointerup', end);
    this.canvas.addEventListener('pointercancel', () => { first = last = null; this.dragging = false; });
  }
  pick(event) {
    if (this.failed) return null;
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    const ray = new Raycaster(); ray.setFromCamera(ndc, this.camera);
    this.group.updateMatrixWorld();
    const hit = ray.ray.intersectSphere(new Sphere(new Vector3(), 1.0), new Vector3());
    if (!hit) return null;
    const local = this.group.worldToLocal(hit).normalize();
    const lat = Math.asin(local.y) * 180 / Math.PI, lon = Math.atan2(local.x, local.z) * 180 / Math.PI;
    const row = Math.max(0, Math.min(89, Math.round((89 - lat) / 2))), col = Math.max(0, Math.min(179, Math.round((lon + 179) / 2)));
    return row * 180 + col;
  }
}
