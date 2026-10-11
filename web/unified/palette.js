// Shared colors for maps, globes and charts. Values are sRGB; checked with the dataviz validator on #0c1222.
export const colors = {
  a: '#4f93f0', b: '#d95926', ink: '#eef2fb', muted: '#a3adc4', dim: '#7a859d',
  grid: '#1d263b', axis: '#2c3650', surface: '#0c1222',
  solid: '#d8a56c', water: '#1f4f9c', error: '#e66767', correct: '#26324b',
  invalid: '#c98500', unresolved: '#9085e9', masked: '#3a4256'
};

// Hypsometric ramp: deep ocean -> shelf -> coast -> lowland -> highland -> snow.
export const heightStops = [
  [-10000, [8, 22, 74]], [-6000, [15, 47, 122]], [-4000, [26, 79, 163]], [-2500, [44, 111, 196]],
  [-1000, [77, 147, 220]], [-200, [134, 189, 236]], [0, [196, 226, 245]], [1, [201, 192, 143]],
  [500, [199, 154, 94]], [1500, [168, 103, 63]], [3000, [122, 70, 54]], [4500, [155, 138, 168]],
  [6000, [241, 238, 246]]
];

export function heightRGB(height) {
  const value = Math.max(-10000, Math.min(6000, height));
  const upper = heightStops.findIndex(([stop]) => stop >= value);
  if (upper <= 0) return heightStops[0][1].slice();
  const [low, a] = heightStops[upper - 1], [high, b] = heightStops[upper], t = (value - low) / (high - low);
  return a.map((v, i) => Math.round(v + (b[i] - v) * t));
}

// Diverging ramp for signed differences: blue = lower, red = higher, neutral slate in the middle.
const neutral = [58, 64, 80], low = [79, 147, 240], high = [230, 103, 103];
export function deltaRGB(delta, span = 2000) {
  const t = Math.min(1, Math.abs(delta) / span), end = delta < 0 ? low : high;
  return neutral.map((v, i) => Math.round(v + (end[i] - v) * Math.sqrt(t)));
}

export const css = rgb => 'rgb(' + rgb.join(',') + ')';
export const hexRGB = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
export const heightGradient = () => 'linear-gradient(90deg,' +
  heightStops.map(([value, rgb]) => css(rgb) + ' ' + ((value + 10000) / 160).toFixed(2) + '%').join(',') + ')';
export const deltaGradient = span => 'linear-gradient(90deg,' + css(deltaRGB(-span, span)) + ',' +
  css(deltaRGB(0, span)) + ',' + css(deltaRGB(span, span)) + ')';
