// Mock-data illustrations for the HDBSCAN and k-means panels. Self-contained: no notebook data, no imports.
// Exposed as window.VOCClusterDemos = { mountHDBSCAN(), mountKMeans() }; view.js calls them when a panel opens.

const SVG_NS = 'http://www.w3.org/2000/svg';
const $ = (id) => document.getElementById(id);
const svgEl = (tag, attributes = {}) => {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
};
const seeded = (seed) => () => {
  seed = (seed + 0x6D2B79F5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const gaussian = (random) => Math.sqrt(-2 * Math.log(1 - random())) * Math.cos(2 * Math.PI * random());
const GROUP_COLOURS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)', 'var(--series-6)', 'var(--series-7)', 'var(--series-8)'];
const px = (p) => [20 + p.x * 280, 280 - p.y * 260];

// 70 mock points: a dense blob, a looser blob, a small tight blob, and scattered noise.
function makePoints() {
  const random = seeded(17);
  const points = [];
  for (let i = 0; i < 20; i += 1) points.push({ x: 0.28 + 0.05 * gaussian(random), y: 0.68 + 0.05 * gaussian(random), blob: 0 });
  for (let i = 0; i < 18; i += 1) points.push({ x: 0.7 + 0.09 * gaussian(random), y: 0.62 + 0.08 * gaussian(random), blob: 1 });
  for (let i = 0; i < 12; i += 1) points.push({ x: 0.5 + 0.035 * gaussian(random), y: 0.22 + 0.035 * gaussian(random), blob: 2 });
  for (let i = 0; i < 20; i += 1) points.push({ x: 0.05 + 0.9 * random(), y: 0.05 + 0.9 * random(), blob: -1 });
  return points;
}
const POINTS = makePoints();
const DIST = POINTS.map((p) => POINTS.map((q) => Math.hypot(p.x - q.x, p.y - q.y)));

// ------------------------------------------------------------- HDBSCAN demo
// One reach at a time: core = at least min_samples points (itself included) within reach; groups = connected cores;
// border = non-core within reach of a core; the rest is noise. The strip shows the group count across all reaches.
function densityClusters(k, reach) {
  const n = POINTS.length;
  const core = POINTS.map((_, i) => DIST[i].filter((d) => d <= reach).length >= k);
  const label = new Array(n).fill(-1);
  let next = 0;
  for (let i = 0; i < n; i += 1) {
    if (!core[i] || label[i] !== -1) continue;
    const stack = [i]; label[i] = next;
    while (stack.length) {
      const a = stack.pop();
      for (let b = 0; b < n; b += 1) if (core[b] && label[b] === -1 && DIST[a][b] <= reach) { label[b] = next; stack.push(b); }
    }
    next += 1;
  }
  const kind = POINTS.map((_, i) => {
    if (core[i]) return 'core';
    let best = -1, bestD = Infinity;
    for (let b = 0; b < n; b += 1) if (core[b] && DIST[i][b] <= reach && DIST[i][b] < bestD) { best = b; bestD = DIST[i][b]; }
    if (best >= 0) { label[i] = label[best]; return 'border'; }
    return 'noise';
  });
  // Order group ids by size so colours are stable-ish across reaches.
  const sizes = {}; label.forEach((l) => { if (l >= 0) sizes[l] = (sizes[l] || 0) + 1; });
  const order = Object.keys(sizes).map(Number).sort((a, b) => sizes[b] - sizes[a]);
  const remap = new Map(order.map((id, rank) => [id, rank]));
  return { label: label.map((l) => (l >= 0 ? remap.get(l) : -1)), kind, groups: order.length, noise: kind.filter((t) => t === 'noise').length };
}
const hdb = { mounted: false, timer: null, history: [] };
function drawDensity() {
  const k = Number($('hdbscan-k').value), reach = Number($('hdbscan-reach').value);
  $('hdbscan-k-value').value = String(k); $('hdbscan-reach-value').value = reach.toFixed(3);
  const result = densityClusters(k, reach);
  const plane = $('hdbscan-demo-plane'); plane.replaceChildren();
  const svg = svgEl('svg', { viewBox: '0 0 320 300', class: 'demo-svg', role: 'img', 'aria-label': `Mock points at reach ${reach.toFixed(3)}: ${result.groups} groups, ${result.noise} noise points` });
  svg.append(svgEl('line', { x1: 20, y1: 292, x2: (20 + reach * 280).toFixed(1), y2: 292, class: 'demo-line' }));
  const scale = svgEl('text', { x: 20, y: 286, class: 'demo-label' }); scale.textContent = 'reach'; svg.append(scale);
  POINTS.forEach((p, i) => {
    const [cx, cy] = px(p);
    const colourOf = result.label[i] >= 0 ? GROUP_COLOURS[result.label[i] % GROUP_COLOURS.length] : 'var(--series-other)';
    if (result.kind[i] === 'noise') {
      const cross = svgEl('path', { d: `M${cx - 4} ${cy - 4}L${cx + 4} ${cy + 4}M${cx + 4} ${cy - 4}L${cx - 4} ${cy + 4}`, class: 'demo-noise' });
      svg.append(cross);
    } else {
      const dot = svgEl('circle', { cx, cy, r: 5, class: result.kind[i] === 'core' ? 'demo-dot' : 'demo-border' });
      if (result.kind[i] === 'core') dot.style.fill = colourOf; else dot.style.stroke = colourOf;
      svg.append(dot);
    }
  });
  plane.append(svg);
  $('hdbscan-demo-note').textContent = `min_samples ${k}, reach ${reach.toFixed(3)}: ${result.groups} group${result.groups === 1 ? '' : 's'}, ${result.noise} noise points. ${result.groups === 3 ? 'The three blobs are separate groups.' : result.groups > 3 ? 'Small reach fragments blobs into pieces.' : result.groups === 0 ? 'Nothing is dense enough yet.' : 'Large reach merges blobs into one group.'}`;
  return result;
}
function drawStrip(current) {
  const strip = $('hdbscan-demo-strip'); strip.replaceChildren();
  const svg = svgEl('svg', { viewBox: '0 0 320 120', class: 'demo-svg', role: 'img', 'aria-label': 'Number of groups at each reach' });
  const k = Number($('hdbscan-k').value);
  const reaches = []; for (let r = 0.03; r <= 0.3001; r += 0.005) reaches.push(Number(r.toFixed(3)));
  const counts = reaches.map((r) => densityClusters(k, r).groups);
  const maxCount = Math.max(1, ...counts);
  const sx = (r) => 16 + ((r - 0.03) / 0.27) * 288, sy = (c) => 100 - (c / maxCount) * 80;
  for (const c of [0, Math.ceil(maxCount / 2), maxCount]) {
    svg.append(svgEl('line', { x1: 16, y1: sy(c), x2: 304, y2: sy(c), class: 'demo-axis' }));
    const t = svgEl('text', { x: 2, y: sy(c) + 3, class: 'demo-label' }); t.textContent = String(c); svg.append(t);
  }
  const path = reaches.map((r, i) => `${i ? 'L' : 'M'}${sx(r).toFixed(1)} ${sy(counts[i]).toFixed(1)}`).join('');
  svg.append(svgEl('path', { d: path, class: 'demo-curve' }));
  // Persistence: the count that holds over the longest stretch of reach (what HDBSCAN would keep).
  let bestCount = 0, bestLen = 0, runCount = counts[0], runLen = 0;
  counts.forEach((c, i) => { if (c === runCount) runLen += 1; else { runCount = c; runLen = 1; } if (runCount > 0 && runLen > bestLen) { bestLen = runLen; bestCount = runCount; } });
  svg.append(svgEl('line', { x1: sx(current).toFixed(1), y1: 12, x2: sx(current).toFixed(1), y2: 100, class: 'demo-marker' }));
  const label = svgEl('text', { x: 16, y: 114, class: 'demo-label' }); label.textContent = `reach 0.03 → 0.30 · most persistent count: ${bestCount} group${bestCount === 1 ? '' : 's'}`; svg.append(label);
  strip.append(svg);
}
function mountHDBSCAN() {
  if (hdb.mounted) { drawDensity(); drawStrip(Number($('hdbscan-reach').value)); return; }
  hdb.mounted = true;
  const refresh = () => { drawDensity(); drawStrip(Number($('hdbscan-reach').value)); };
  $('hdbscan-k').addEventListener('input', refresh);
  $('hdbscan-reach').addEventListener('input', refresh);
  $('hdbscan-sweep').addEventListener('click', () => {
    if (hdb.timer) return;
    let reach = 0.03;
    const tick = () => {
      $('hdbscan-reach').value = reach.toFixed(3); refresh();
      reach += 0.005;
      if (reach <= 0.3) hdb.timer = requestAnimationFrame(tick); else hdb.timer = null;
    };
    hdb.timer = requestAnimationFrame(tick);
  });
  refresh();
}

// -------------------------------------------------------------- k-means demo
const km = { mounted: false, centres: [], assign: [], iteration: 0, history: [], timer: null };
function kmeansSeed() {
  const k = Number($('kmeans-k').value);
  const random = seeded(31 + km.reseeds);
  km.centres = Array.from({ length: k }, () => ({ x: 0.1 + 0.8 * random(), y: 0.1 + 0.8 * random() }));
  km.iteration = 0; km.history = [];
  kmeansAssign();
}
function kmeansAssign() {
  let total = 0;
  km.assign = POINTS.map((p) => {
    let best = 0, bestD = Infinity;
    km.centres.forEach((c, j) => { const d = Math.hypot(p.x - c.x, p.y - c.y); if (d < bestD) { bestD = d; best = j; } });
    total += bestD * bestD;
    return best;
  });
  km.history.push(total);
  return total;
}
function kmeansStep() {
  const sums = km.centres.map(() => ({ x: 0, y: 0, n: 0 }));
  POINTS.forEach((p, i) => { const s = sums[km.assign[i]]; s.x += p.x; s.y += p.y; s.n += 1; });
  let moved = 0;
  km.centres = km.centres.map((c, j) => {
    if (!sums[j].n) return c;
    const next = { x: sums[j].x / sums[j].n, y: sums[j].y / sums[j].n };
    moved += Math.hypot(next.x - c.x, next.y - c.y);
    return next;
  });
  km.iteration += 1;
  kmeansAssign();
  return moved;
}
function drawKmeans() {
  const plane = $('kmeans-demo-plane'); plane.replaceChildren();
  const svg = svgEl('svg', { viewBox: '0 0 320 300', class: 'demo-svg', role: 'img', 'aria-label': `Mock points assigned to ${km.centres.length} centres after ${km.iteration} iterations` });
  POINTS.forEach((p, i) => {
    const [cx, cy] = px(p);
    const dot = svgEl('circle', { cx, cy, r: 4.5, class: p.blob === -1 ? 'demo-dot demo-was-noise' : 'demo-dot' });
    dot.style.fill = GROUP_COLOURS[km.assign[i] % GROUP_COLOURS.length];
    svg.append(dot);
  });
  km.centres.forEach((c, j) => {
    const [cx, cy] = px(c);
    const diamond = svgEl('path', { d: `M${cx} ${cy - 9}L${cx + 9} ${cy}L${cx} ${cy + 9}L${cx - 9} ${cy}Z`, class: 'demo-centre-mark' });
    diamond.style.fill = GROUP_COLOURS[j % GROUP_COLOURS.length];
    svg.append(diamond);
  });
  const legend = svgEl('text', { x: 20, y: 18, class: 'demo-label' }); legend.textContent = 'ringed points were scattered noise in the data'; svg.append(legend);
  plane.append(svg);
  const strip = $('kmeans-demo-strip'); strip.replaceChildren();
  const s2 = svgEl('svg', { viewBox: '0 0 320 120', class: 'demo-svg', role: 'img', 'aria-label': 'Total squared distance to the centres per iteration' });
  const h = km.history, maxH = Math.max(...h, 1e-9);
  const sx = (i) => 16 + (h.length > 1 ? (i / (h.length - 1)) * 288 : 0), sy = (v) => 100 - (v / maxH) * 80;
  s2.append(svgEl('line', { x1: 16, y1: 100, x2: 304, y2: 100, class: 'demo-axis' }));
  s2.append(svgEl('path', { d: h.map((v, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)} ${sy(v).toFixed(1)}`).join(''), class: 'demo-curve' }));
  h.forEach((v, i) => s2.append(svgEl('circle', { cx: sx(i).toFixed(1), cy: sy(v).toFixed(1), r: 3, class: 'demo-foot' })));
  const label = svgEl('text', { x: 16, y: 114, class: 'demo-label' }); label.textContent = `iteration ${km.iteration} · total squared distance ${h[h.length - 1].toFixed(3)}`; s2.append(label);
  strip.append(s2);
  const noiseInGroups = POINTS.filter((p) => p.blob === -1).length;
  $('kmeans-demo-note').textContent = `k = ${km.centres.length}, iteration ${km.iteration}. Every point is assigned, including the ${noiseInGroups} scattered ones. ${km.centres.length === 3 ? 'With k = 3 the centres usually settle on the blobs, dragging the noise along.' : km.centres.length < 3 ? 'With fewer centres than blobs, blobs are merged.' : 'With more centres than blobs, blobs are split along straight lines.'}`;
}
function mountKMeans() {
  if (km.mounted) { drawKmeans(); return; }
  km.mounted = true; km.reseeds = 0;
  $('kmeans-k').addEventListener('input', () => { $('kmeans-k-value').value = $('kmeans-k').value; kmeansSeed(); drawKmeans(); });
  $('kmeans-step').addEventListener('click', () => { kmeansStep(); drawKmeans(); });
  $('kmeans-run').addEventListener('click', () => {
    if (km.timer) return;
    let steps = 0;
    const tick = () => { const moved = kmeansStep(); drawKmeans(); steps += 1; if (moved > 1e-4 && steps < 30) km.timer = setTimeout(tick, 220); else km.timer = null; };
    km.timer = setTimeout(tick, 0);
  });
  $('kmeans-reseed').addEventListener('click', () => { km.reseeds += 1; kmeansSeed(); drawKmeans(); });
  $('kmeans-k-value').value = $('kmeans-k').value;
  kmeansSeed(); drawKmeans();
}

window.VOCClusterDemos = { mountHDBSCAN, mountKMeans };
