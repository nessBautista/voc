// Mock-data illustrations for the UMAP and PCA panels. Self-contained: no notebook data, no imports.
// Exposed as window.VOCReductionDemos = { mountUMAP(), mountPCA() }; view.js calls them when a panel opens.

const SVG_NS = 'http://www.w3.org/2000/svg';
const $ = (id) => document.getElementById(id);
const svgEl = (tag, attributes = {}) => {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
};
// Deterministic pseudo-random numbers (mulberry32) so the mock data is the same on every visit.
const seeded = (seed) => () => {
  seed = (seed + 0x6D2B79F5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const gaussian = (random) => Math.sqrt(-2 * Math.log(1 - random())) * Math.cos(2 * Math.PI * random());
const GROUP_COLOURS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)'];

// ------------------------------------------------------------------ UMAP demo
// Two round groups and one curved strand, 42 points in 2-D, reduced to 1-D by (1) a weighted k-nearest-neighbour
// graph and (2) a force layout that pulls linked points together and pushes a random sample of others apart.
function makeUmapData() {
  const random = seeded(7);
  const points = [];
  for (let i = 0; i < 14; i += 1) points.push({ x: 0.22 + 0.07 * gaussian(random), y: 0.3 + 0.07 * gaussian(random), group: 0 });
  for (let i = 0; i < 14; i += 1) points.push({ x: 0.84 + 0.05 * gaussian(random), y: 0.84 + 0.05 * gaussian(random), group: 1 });
  for (let i = 0; i < 14; i += 1) {
    const t = i / 13;
    points.push({ x: 0.2 + 0.6 * t + 0.02 * gaussian(random), y: 0.85 - 0.75 * t * t + 0.02 * gaussian(random), group: 2 });
  }
  return points;
}
function knnGraph(points, k) {
  const n = points.length;
  const distances = points.map((p) => points.map((q) => Math.hypot(p.x - q.x, p.y - q.y)));
  const edges = new Map(); // "i,j" with i<j -> weight
  for (let i = 0; i < n; i += 1) {
    const order = [...distances[i].keys()].filter((j) => j !== i).sort((a, b) => distances[i][a] - distances[i][b]).slice(0, k);
    const nearest = distances[i][order[0]];
    const scale = Math.max(1e-6, (distances[i][order[order.length - 1]] - nearest) || nearest);
    for (const j of order) {
      const w = Math.exp(-(distances[i][j] - nearest) / scale); // 1 for the nearest, fading for the farthest
      const key = i < j ? `${i},${j}` : `${j},${i}`;
      const prev = edges.get(key) || 0;
      edges.set(key, prev + w - prev * w); // fuzzy union of the two directed weights
    }
  }
  return [...edges.entries()].map(([key, w]) => { const [i, j] = key.split(',').map(Number); return { i, j, w }; });
}
const umap = { points: makeUmapData(), edges: [], positions: [], timer: null, epoch: 0 };
function umapResetLayout() {
  // UMAP starts from a spectral layout of the graph rather than random positions; here we start from the
  // points' main axis (their first principal component), which plays the same role for this small set.
  const random = seeded(11);
  const stats = pcaStats(umap.points);
  const along = umap.points.map((p) => (p.x - stats.mx) * Math.cos(stats.pc1) + (p.y - stats.my) * Math.sin(stats.pc1));
  const lo = Math.min(...along), hi = Math.max(...along);
  umap.positions = along.map((t) => 0.08 + 0.84 * ((t - lo) / (hi - lo || 1)) + 0.01 * (random() - 0.5));
  umap.epoch = 0;
  if (umap.timer) { cancelAnimationFrame(umap.timer); umap.timer = null; }
}
function umapStep(minDist, random) {
  const pos = umap.positions, n = pos.length, lr = 0.045;
  const moves = new Array(n).fill(0);
  for (const { i, j, w } of umap.edges) {
    const d = pos[j] - pos[i];
    const gap = Math.abs(d) - minDist * 0.12; // attraction stops once neighbours are min_dist apart
    if (gap > 0) { const pull = lr * w * gap * Math.sign(d); moves[i] += pull; moves[j] -= pull; }
  }
  for (let i = 0; i < n; i += 1) {
    for (let s = 0; s < 3; s += 1) { // negative sampling: push away a few random points
      const j = Math.floor(random() * n);
      if (j === i) continue;
      const d = pos[i] - pos[j];
      const push = (lr * 0.35) / (0.02 + d * d) * Math.sign(d || 1) * 0.01;
      moves[i] += push;
    }
  }
  for (let i = 0; i < n; i += 1) pos[i] = Math.min(1, Math.max(0, pos[i] + moves[i]));
}
function drawUmapGraph() {
  const host = $('umap-demo-graph');
  host.replaceChildren();
  const svg = svgEl('svg', { viewBox: '0 0 320 300', class: 'demo-svg', role: 'img', 'aria-label': 'Mock points with their nearest-neighbour links' });
  const px = (p) => [20 + p.x * 280, 280 - p.y * 260];
  for (const { i, j, w } of umap.edges) {
    const [x1, y1] = px(umap.points[i]), [x2, y2] = px(umap.points[j]);
    svg.append(svgEl('line', { x1, y1, x2, y2, class: 'demo-edge', style: `opacity:${(0.15 + 0.75 * w).toFixed(2)}` }));
  }
  umap.points.forEach((p, i) => {
    const [cx, cy] = px(p);
    const dot = svgEl('circle', { cx, cy, r: 5, class: 'demo-dot' });
    dot.style.fill = GROUP_COLOURS[p.group];
    dot.append(svgEl('title')); dot.firstChild.textContent = `point ${i}, group ${p.group + 1}`;
    svg.append(dot);
  });
  host.append(svg);
}
function drawUmapLayout() {
  const host = $('umap-demo-layout');
  host.replaceChildren();
  const svg = svgEl('svg', { viewBox: '0 0 320 120', class: 'demo-svg', role: 'img', 'aria-label': 'One-dimensional layout of the mock points' });
  svg.append(svgEl('line', { x1: 16, y1: 70, x2: 304, y2: 70, class: 'demo-axis' }));
  const random = seeded(3);
  umap.points.forEach((p, i) => {
    const x = 16 + umap.positions[i] * 288;
    const dot = svgEl('circle', { cx: x.toFixed(1), cy: (70 + (random() - 0.5) * 34).toFixed(1), r: 5, class: 'demo-dot' });
    dot.style.fill = GROUP_COLOURS[p.group];
    svg.append(dot);
  });
  const label = svgEl('text', { x: 16, y: 108, class: 'demo-label' }); label.textContent = `epoch ${umap.epoch}`;
  svg.append(label);
  host.append(svg);
}
function umapNote() {
  const k = Number($('umap-k').value);
  const groups = [0, 1, 2].map((g) => umap.points.map((p, i) => [p.group, umap.positions[i]]).filter(([gg]) => gg === g).map(([, x]) => x));
  const trimmed = (xs) => { const sorted = [...xs].sort((a, b) => a - b); return [sorted[1], sorted[sorted.length - 2]]; }; // ignore one straggler per side
  const spans = groups.map(trimmed).sort((a, b) => a[0] - b[0]);
  let overlaps = 0;
  for (let i = 1; i < spans.length; i += 1) if (spans[i][0] < spans[i - 1][1]) overlaps += 1;
  const verdict = umap.epoch === 0 ? 'Positions start from the points\u2019 main axis (UMAP uses a spectral layout of the graph). Press Run layout.'
    : overlaps === 0 ? 'The three structures occupy three separate bands: neighbourhoods survived the squeeze to one dimension.'
      : `${overlaps} band${overlaps > 1 ? 's' : ''} still overlap; run longer, or change n_neighbors (small values fragment groups, large values merge them).`;
  $('umap-demo-note').textContent = `${umap.edges.length} links with n_neighbors = ${k}. ${verdict}`;
}
function mountUMAP() {
  if (umap.mounted) { drawUmapGraph(); drawUmapLayout(); umapNote(); return; }
  umap.mounted = true;
  const rebuild = () => {
    $('umap-k-value').value = $('umap-k').value;
    $('umap-mindist-value').value = Number($('umap-mindist').value).toFixed(2);
    umap.edges = knnGraph(umap.points, Number($('umap-k').value));
    umapResetLayout();
    drawUmapGraph(); drawUmapLayout(); umapNote();
  };
  $('umap-k').addEventListener('input', rebuild);
  $('umap-mindist').addEventListener('input', () => { $('umap-mindist-value').value = Number($('umap-mindist').value).toFixed(2); });
  $('umap-reset').addEventListener('click', rebuild);
  $('umap-run').addEventListener('click', () => {
    if (umap.timer) return;
    const random = seeded(23 + umap.epoch);
    const tick = () => {
      for (let s = 0; s < 4; s += 1) { umapStep(Number($('umap-mindist').value), random); umap.epoch += 1; }
      drawUmapLayout(); umapNote();
      if (umap.epoch < 320) umap.timer = requestAnimationFrame(tick); else umap.timer = null;
    };
    umap.timer = requestAnimationFrame(tick);
  });
  rebuild();
}

// ------------------------------------------------------------------- PCA demo
// 80 mock points from a tilted, elongated cloud; the slider turns a line through the centre and the strip shows
// the points projected onto it. The variance kept along the line is compared with what the first principal
// component keeps.
const pca = { points: [], angle: 20 };
function makePcaData(shape) {
  const random = seeded(5);
  const points = [];
  for (let i = 0; i < 80; i += 1) {
    const t = gaussian(random), n = gaussian(random);
    const u = 0.32 * t, v = 0.32 * (1 - shape) * n + 0.05 * n; // shape 0: round cloud, 1: thin line
    const theta = 0.6; // fixed tilt of the cloud, so PC1 is never axis-aligned
    points.push({ x: 0.5 + u * Math.cos(theta) - v * Math.sin(theta), y: 0.5 + u * Math.sin(theta) + v * Math.cos(theta) });
  }
  return points;
}
function pcaStats(points) {
  const n = points.length;
  const mx = points.reduce((s, p) => s + p.x, 0) / n, my = points.reduce((s, p) => s + p.y, 0) / n;
  let cxx = 0, cyy = 0, cxy = 0;
  for (const p of points) { cxx += (p.x - mx) ** 2; cyy += (p.y - my) ** 2; cxy += (p.x - mx) * (p.y - my); }
  cxx /= n; cyy /= n; cxy /= n;
  const pc1 = 0.5 * Math.atan2(2 * cxy, cxx - cyy); // direction of the largest eigenvector
  const varianceAlong = (angle) => cxx * Math.cos(angle) ** 2 + cyy * Math.sin(angle) ** 2 + 2 * cxy * Math.sin(angle) * Math.cos(angle);
  return { mx, my, total: cxx + cyy, pc1, varianceAlong };
}
function drawPca() {
  const { points, angle } = pca;
  const stats = pcaStats(points);
  const rad = (angle * Math.PI) / 180;
  const ux = Math.cos(rad), uy = Math.sin(rad);
  const plane = $('pca-demo-plane'); plane.replaceChildren();
  const svg = svgEl('svg', { viewBox: '0 0 320 300', class: 'demo-svg', role: 'img', 'aria-label': 'Mock points and the projection line' });
  const px = (x, y) => [20 + x * 280, 280 - y * 260];
  const [cx, cy] = px(stats.mx, stats.my);
  const [lx1, ly1] = px(stats.mx - ux * 0.7, stats.my - uy * 0.7), [lx2, ly2] = px(stats.mx + ux * 0.7, stats.my + uy * 0.7);
  svg.append(svgEl('line', { x1: lx1, y1: ly1, x2: lx2, y2: ly2, class: 'demo-line' }));
  const pc1 = stats.pc1; const [px1, py1] = px(stats.mx - Math.cos(pc1) * 0.7, stats.my - Math.sin(pc1) * 0.7), [px2, py2] = px(stats.mx + Math.cos(pc1) * 0.7, stats.my + Math.sin(pc1) * 0.7);
  svg.append(svgEl('line', { x1: px1, y1: py1, x2: px2, y2: py2, class: 'demo-pc1' }));
  const projected = [];
  for (const p of points) {
    const t = (p.x - stats.mx) * ux + (p.y - stats.my) * uy; // coordinate along the line
    projected.push(t);
    const [fx, fy] = px(stats.mx + t * ux, stats.my + t * uy), [ox, oy] = px(p.x, p.y);
    svg.append(svgEl('line', { x1: ox, y1: oy, x2: fx, y2: fy, class: 'demo-drop' }));
    svg.append(svgEl('circle', { cx: fx, cy: fy, r: 2.5, class: 'demo-foot' }));
    svg.append(svgEl('circle', { cx: ox, cy: oy, r: 4, class: 'demo-dot demo-dot-plain' }));
  }
  svg.append(svgEl('circle', { cx, cy, r: 3, class: 'demo-centre' }));
  const legend = svgEl('text', { x: 20, y: 18, class: 'demo-label' }); legend.textContent = 'solid: your line · dashed: PC1';
  svg.append(legend);
  plane.append(svg);

  const strip = $('pca-demo-strip'); strip.replaceChildren();
  const s2 = svgEl('svg', { viewBox: '0 0 320 120', class: 'demo-svg', role: 'img', 'aria-label': 'Projected positions along the line' });
  s2.append(svgEl('line', { x1: 16, y1: 70, x2: 304, y2: 70, class: 'demo-axis' }));
  const random = seeded(9);
  for (const t of projected) {
    s2.append(svgEl('circle', { cx: (160 + t * 280).toFixed(1), cy: (70 + (random() - 0.5) * 34).toFixed(1), r: 4, class: 'demo-dot demo-dot-plain' }));
  }
  strip.append(s2);
  const kept = stats.varianceAlong(rad) / stats.total, best = stats.varianceAlong(pc1) / stats.total;
  $('pca-demo-note').textContent = `This line keeps ${(100 * kept).toFixed(0)}% of the spread; PC1 (at ${((pc1 * 180) / Math.PI + 180) % 180 | 0}°) keeps ${(100 * best).toFixed(0)}%. The other ${(100 * (1 - best)).toFixed(0)}% is what the reduction throws away.`;
}
function mountPCA() {
  if (pca.mounted) { drawPca(); return; }
  pca.mounted = true;
  const rebuild = () => {
    $('pca-shape-value').value = Number($('pca-shape').value).toFixed(2);
    pca.points = makePcaData(Number($('pca-shape').value));
    drawPca();
  };
  $('pca-angle').addEventListener('input', () => { pca.angle = Number($('pca-angle').value); $('pca-angle-value').value = `${pca.angle}°`; drawPca(); });
  $('pca-shape').addEventListener('input', rebuild);
  $('pca-snap').addEventListener('click', () => {
    const deg = Math.round(((pcaStats(pca.points).pc1 * 180) / Math.PI + 180) % 180);
    pca.angle = deg; $('pca-angle').value = String(deg); $('pca-angle-value').value = `${deg}°`; drawPca();
  });
  $('pca-angle-value').value = `${pca.angle}°`;
  rebuild();
}

window.VOCReductionDemos = { mountUMAP, mountPCA };
