// The dashboard of the last page. view.js merges every stage's payload into one table of reviews plus the
// topic records and calls mount(); everything here is filtering, counting and drawing in the browser.
// Exposed as window.VOCDashboard = { mount(data) }.

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
};
const svgEl = (tag, attributes = {}) => {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
};
const number = (n) => Number(n).toLocaleString('en-US');
const percent = (share, digits = 0) => `${(100 * share).toFixed(digits)}%`;
const SENT = ['negative', 'neutral', 'positive'];
const SENT_CLASS = { negative: 'neg', neutral: 'neu', positive: 'pos' };
const PLATFORMS = [['google_play', 'Google Play', 1], ['app_store', 'App Store', 2], ['x', 'X', 3], ['youtube', 'YouTube', 4]];
const platformLabel = (key) => (PLATFORMS.find((p) => p[0] === key) || [key, key])[1];
const platformColour = (key) => { const p = PLATFORMS.find((x) => x[0] === key); return p ? `var(--series-${p[2]})` : 'var(--series-other)'; };
const ASSESS = { single_feature: 'Single feature', mixed: 'Mixed', unclear: 'Unclear', none: 'Unassigned' };
const MONTH = (ym) => { const [y, m] = ym.split('-').map(Number); return `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][m - 1]} ${String(y).slice(-2)}`; };
const PAGE_SIZE = 6;
/** Lower bound of the 95 % Wilson interval for k successes in n trials: a share that small samples cannot inflate. */
const wilsonLow = (k, n) => {
  if (!n) return 0;
  const z = 1.96, p = k / n;
  return (p + (z * z) / (2 * n) - z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / (1 + (z * z) / n);
};

const dash = { data: null, mounted: false, platforms: new Set(), sentiments: new Set(SENT), assessments: new Set(Object.keys(ASSESS)), from: null, to: null, query: '', sort: 'volume', topic: null, page: 0 };

function filteredRows() {
  const q = dash.query.toLowerCase();
  return dash.data.rows.filter((r) => dash.platforms.has(r.platform) && dash.sentiments.has(r.sentiment) && dash.assessments.has(r.assessment)
    && r.month >= dash.from && r.month <= dash.to && (!q || r.text.toLowerCase().includes(q)));
}
const mixOf = (rows) => rows.reduce((acc, r) => { acc[r.sentiment] += 1; acc.total += 1; return acc; }, { negative: 0, neutral: 0, positive: 0, total: 0 });

function chip(label, active, onClick, colour) {
  const button = node('button', undefined, `dash-chip${active ? ' is-active' : ''}`); button.type = 'button'; button.setAttribute('aria-pressed', String(active));
  if (colour) { const dot = node('i', undefined, 'swatch'); dot.style.background = colour; button.append(dot); }
  button.append(document.createTextNode(label)); button.addEventListener('click', onClick); return button;
}
function renderFilters() {
  const platforms = $('filter-platform'); platforms.replaceChildren();
  for (const key of dash.data.platforms) platforms.append(chip(platformLabel(key), dash.platforms.has(key), () => { toggle(dash.platforms, key); renderAll(); }, platformColour(key)));
  const sentiments = $('filter-sentiment'); sentiments.replaceChildren();
  for (const key of SENT) sentiments.append(chip(key, dash.sentiments.has(key), () => { toggle(dash.sentiments, key); renderAll(); }, `var(--sent-${SENT_CLASS[key]})`));
  const assessments = $('filter-assessment'); assessments.replaceChildren();
  for (const key of Object.keys(ASSESS)) assessments.append(chip(ASSESS[key], dash.assessments.has(key), () => { toggle(dash.assessments, key); renderAll(); }));
  for (const [id, value] of [['filter-from', dash.from], ['filter-to', dash.to]]) {
    const select = $(id); select.replaceChildren();
    for (const m of dash.data.months) { const o = node('option', MONTH(m)); o.value = m; select.append(o); }
    select.value = value;
  }
  $('filter-search').value = dash.query;
}
function toggle(set, key) { if (set.has(key)) { if (set.size > 1) set.delete(key); } else set.add(key); }

function renderKpis(rows) {
  const mix = mixOf(rows);
  const topics = new Set(rows.filter((r) => r.topic >= 0).map((r) => r.topic)).size;
  // "Most negative feature": feature topics only (single feature or mixed, never unclear or unassigned), ranked by
  // the Wilson lower bound of their negative share, so 14 unanimous reviews do not outrank 96 reviews at 91 %.
  const byTopic = topicStats(rows).filter((t) => t.topic >= 0 && t.assessment !== 'unclear' && t.assessment !== 'none' && t.total >= 5)
    .map((t) => ({ ...t, bound: wilsonLow(t.negative, t.total) })).sort((a, b) => b.bound - a.bound || b.total - a.total);
  const worst = byTopic[0];
  const host = $('dash-kpis'); host.replaceChildren();
  for (const [value, label, context] of [
    [number(mix.total), 'reviews in view', `of ${number(dash.data.rows.length)} in the sample`],
    [number(topics), 'topics represented', `of ${number(dash.data.topics.filter((t) => t.topic >= 0).length)} found`],
    [mix.total ? percent(mix.negative / mix.total) : '—', 'negative', `${number(mix.negative)} reviews`],
    [mix.total ? percent(mix.positive / mix.total) : '—', 'positive', `${number(mix.positive)} reviews`],
    [worst ? worst.title.slice(0, 42) : '—', 'most negative feature', worst ? `${percent(worst.negative / worst.total)} of ${number(worst.total)} reviews · at least ${percent(worst.bound)} (95 % bound)` : 'no feature topic with 5+ reviews in view'],
  ]) {
    const tile = node('div', undefined, 'dash-kpi'); tile.append(node('strong', value), node('span', label), node('small', context)); host.append(tile);
  }
}
function topicStats(rows) {
  const stats = new Map();
  for (const r of rows) {
    if (!stats.has(r.topic)) stats.set(r.topic, { ...dash.data.topics.find((t) => t.topic === r.topic), negative: 0, neutral: 0, positive: 0, total: 0 });
    const s = stats.get(r.topic); s[r.sentiment] += 1; s.total += 1;
  }
  return [...stats.values()];
}
function sentimentBar(stats, max) {
  const track = node('span', undefined, 'sentiment-track');
  const bar = node('span', undefined, 'sentiment-bar'); bar.style.width = `${(100 * stats.total) / max}%`;
  for (const k of SENT) { const seg = node('i', undefined, SENT_CLASS[k]); seg.style.width = `${stats.total ? (100 * stats[k]) / stats.total : 0}%`; bar.append(seg); }
  track.append(bar); return track;
}
function renderTopics(rows) {
  const list = $('dash-topics'); list.replaceChildren();
  const stats = topicStats(rows);
  const share = (t, k) => (t.total ? t[k] / t.total : 0);
  if (dash.sort === 'negative') stats.sort((a, b) => share(b, 'negative') - share(a, 'negative') || b.total - a.total);
  else if (dash.sort === 'positive') stats.sort((a, b) => share(b, 'positive') - share(a, 'positive') || b.total - a.total);
  else stats.sort((a, b) => b.total - a.total);
  stats.sort((a, b) => (a.topic < 0) - (b.topic < 0)); // unassigned reviews always last; they are not a subject
  const max = Math.max(1, ...stats.map((t) => t.total));
  for (const t of stats) {
    const item = node('li');
    const button = node('button', undefined, 'sentiment-row'); button.type = 'button'; button.setAttribute('aria-pressed', String(dash.topic === t.topic));
    const head = node('span', undefined, 'sentiment-row-head');
    head.append(node('strong', `${t.topic < 0 ? 'Unassigned' : `Topic ${t.topic}`} · ${ASSESS[t.assessment]}`), node('span', t.title, `sentiment-row-title${t.feature ? '' : ' is-keywords'}`));
    const count = node('span', undefined, 'sentiment-row-count'); count.append(node('strong', number(t.total)), node('small', `${percent(share(t, 'negative'))} negative`));
    button.setAttribute('aria-label', `${t.title}: ${t.total} reviews, ${t.negative} negative, ${t.neutral} neutral, ${t.positive} positive`);
    button.title = SENT.map((k) => `${k} ${t[k]}`).join(' · ');
    button.append(head, sentimentBar(t, max), count);
    button.addEventListener('click', () => { dash.topic = dash.topic === t.topic ? null : t.topic; dash.page = 0; renderTopics(filteredRows()); renderDetail(); });
    item.append(button); list.append(item);
  }
  if (!stats.length) list.append(node('li', 'No reviews match the filters.', 'subtle'));
}
function trendChart(host, rows, note) {
  host.replaceChildren();
  const months = dash.data.months.filter((m) => m >= dash.from && m <= dash.to);
  const byMonth = months.map((m) => mixOf(rows.filter((r) => r.month === m)));
  const max = Math.max(1, ...byMonth.map((b) => b.total));
  const W = 360, H = 160, pad = { l: 30, r: 6, t: 8, b: 26 };
  const chart = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'trend-svg' });
  const colW = (W - pad.l - pad.r) / Math.max(1, months.length);
  for (const f of [0.5, 1]) { const y = H - pad.b - (H - pad.t - pad.b) * f; chart.append(svgEl('line', { x1: pad.l, y1: y, x2: W - pad.r, y2: y, class: 'trend-grid' })); const t = svgEl('text', { x: 2, y: y + 3, class: 'trend-axis' }); t.textContent = number(Math.round(max * f)); chart.append(t); }
  months.forEach((m, i) => {
    let y = H - pad.b;
    const b = byMonth[i];
    for (const k of SENT) {
      const h = ((H - pad.t - pad.b) * b[k]) / max;
      if (h > 0) { const rect = svgEl('rect', { x: (pad.l + i * colW + 2).toFixed(1), y: (y - h).toFixed(1), width: Math.max(1, colW - 4).toFixed(1), height: h.toFixed(1), class: `rating-seg ${SENT_CLASS[k]}` }); const title = svgEl('title'); title.textContent = `${MONTH(m)} · ${k}: ${b[k]} of ${b.total}`; rect.append(title); chart.append(rect); y -= h; }
    }
    if (months.length <= 8 || i % 2 === 0) { const t = svgEl('text', { x: (pad.l + i * colW + colW / 2).toFixed(1), y: H - 8, class: 'trend-axis', 'text-anchor': 'middle' }); t.textContent = MONTH(m); chart.append(t); }
  });
  host.append(chart);
  if (note) { const total = byMonth.reduce((s, b) => s + b.total, 0); const peak = byMonth.reduce((best, b, i) => (b.total > byMonth[best].total ? i : best), 0); note.textContent = total ? `${number(total)} reviews across ${months.length} months; busiest ${MONTH(months[peak])} with ${number(byMonth[peak].total)}.` : 'No reviews in this range.'; }
}
function renderPlatforms(rows) {
  const list = $('dash-platforms'); list.replaceChildren();
  const stats = dash.data.platforms.map((key) => ({ key, ...mixOf(rows.filter((r) => r.platform === key)) })).filter((s) => s.total);
  const max = Math.max(1, ...stats.map((s) => s.total));
  for (const s of stats) {
    const item = node('li', undefined, 'dash-platform-row');
    const name = node('span', undefined, 'platform-tag'); const dot = node('i', undefined, 'swatch'); dot.style.background = platformColour(s.key); name.append(dot, document.createTextNode(platformLabel(s.key)));
    const count = node('span', undefined, 'sentiment-row-count'); count.append(node('strong', number(s.total)), node('small', `${percent(s.negative / s.total)} negative`));
    item.append(name, sentimentBar(s, max), count); item.title = SENT.map((k) => `${k} ${s[k]}`).join(' · ');
    list.append(item);
  }
  if (!stats.length) list.append(node('li', 'No reviews match.', 'subtle'));
}
function renderOverall(rows) {
  const host = $('dash-overall'); host.replaceChildren();
  const mix = mixOf(rows);
  if (!mix.total) { host.append(node('p', 'No reviews match.', 'subtle')); return; }
  const ring = node('div', undefined, 'mood-ring'); let at = 0;
  const stops = SENT.map((k) => { const start = at; at += (100 * mix[k]) / mix.total; return `var(--sent-${SENT_CLASS[k]}) ${start}% ${at}%`; });
  ring.style.background = `conic-gradient(${stops.join(',')})`;
  ring.setAttribute('role', 'img'); ring.setAttribute('aria-label', SENT.map((k) => `${k} ${percent(mix[k] / mix.total)}`).join(', '));
  const centre = node('div'); centre.append(node('strong', percent(mix.positive / mix.total)), node('span', 'positive')); ring.append(centre);
  const legend = node('div', undefined, 'mood-legend');
  for (const k of SENT) { const row = node('div'); const sw = node('i', undefined, `swatch ${SENT_CLASS[k]}`); row.append(sw, node('span', k), node('strong', `${number(mix[k])} · ${percent(mix[k] / mix.total)}`)); legend.append(row); }
  host.append(ring, legend);
}
function renderDetail() {
  const panel = $('dash-detail');
  if (dash.topic === null) { panel.hidden = true; return; }
  panel.hidden = false;
  const meta = dash.data.topics.find((t) => t.topic === dash.topic);
  const rows = filteredRows().filter((r) => r.topic === dash.topic);
  const all = dash.data.rows.filter((r) => r.topic === dash.topic);
  const mix = mixOf(rows);
  $('dash-detail-eyebrow').textContent = `${dash.topic < 0 ? 'Unassigned' : `Topic ${dash.topic}`} · ${ASSESS[meta.assessment]}`;
  $('dash-detail-title').textContent = meta.title;
  $('dash-detail-meta').textContent = `${number(rows.length)} of ${number(all.length)} reviews match the filters · ` + SENT.map((k) => `${k} ${number(mix[k])}`).join(' · ');
  $('dash-detail-description').textContent = meta.description || (dash.topic < 0 ? 'Reviews HDBSCAN placed in no dense group; not one subject.' : 'No description: the label block did not run for this topic.');
  const kw = $('dash-detail-keywords'); kw.replaceChildren(); for (const w of meta.keywords) kw.append(node('span', w, 'chip'));
  const mixHost = $('dash-detail-mix'); mixHost.replaceChildren();
  for (const k of SENT) { const row = node('div', undefined, 'prob-row'); const track = node('span', undefined, 'walk-bar'); const fill = node('span', undefined, `walk-fill ${SENT_CLASS[k]}`); fill.style.width = `${mix.total ? (100 * mix[k]) / mix.total : 0}%`; track.append(fill); row.append(node('span', k, 'prob-label'), track, node('strong', mix.total ? percent(mix[k] / mix.total) : '—')); mixHost.append(row); }
  trendChart($('dash-detail-trend'), rows, null);
  const sorted = [...rows].sort((a, b) => SENT.indexOf(a.sentiment) - SENT.indexOf(b.sentiment) || b.confidence - a.confidence);
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE)); dash.page = Math.min(dash.page, pages - 1);
  const list = $('dash-detail-reviews'); list.replaceChildren();
  for (const r of sorted.slice(dash.page * PAGE_SIZE, (dash.page + 1) * PAGE_SIZE)) {
    const item = node('li', undefined, 'group-review');
    const head = node('div', undefined, 'neighbor-head');
    const tag = node('span', undefined, 'platform-tag'); const dot = node('i', undefined, 'swatch'); dot.style.background = platformColour(r.platform); tag.append(dot, document.createTextNode(platformLabel(r.platform)));
    head.append(tag, node('span', r.sentiment, `sent-badge ${SENT_CLASS[r.sentiment]}`), node('span', MONTH(r.month), 'rating-tag'));
    if (r.rating) head.append(node('span', `${r.rating} ★`, 'rating-tag'));
    head.append(node('span', percent(r.confidence), 'strength'));
    const text = node('blockquote', r.text, 'io-text'); text.setAttribute('lang', 'es');
    item.append(head, text); list.append(item);
  }
  if (!sorted.length) list.append(node('li', 'No review in this topic matches the filters.', 'subtle'));
  $('dash-detail-page').textContent = `${number(sorted.length)} review${sorted.length === 1 ? '' : 's'} · page ${dash.page + 1} of ${pages}`;
  $('dash-detail-prev').disabled = dash.page === 0; $('dash-detail-next').disabled = dash.page >= pages - 1;
}
function renderStore() {
  const { store } = dash.data;
  $('dash-store-line').textContent = `${store.files.length} artifacts · ${(store.files.reduce((s, f) => s + f.bytes, 0) / 1e6).toFixed(1)} MB · ${store.root}`;
  const body = $('store-table').querySelector('tbody'); body.replaceChildren();
  for (const f of store.files) {
    const tr = node('tr');
    const manifest = f.manifest ? Object.entries(f.manifest).map(([k, v]) => `${k}: ${String(v).slice(0, 28)}`).join(' · ') : '';
    tr.append(node('td', f.name), node('td', f.role), node('td', f.bytes > 1e6 ? `${(f.bytes / 1e6).toFixed(1)} MB` : f.bytes >= 1e3 ? `${Math.round(f.bytes / 1e3)} kB` : `${f.bytes} B`, 'num'), node('td', manifest));
    body.append(tr);
  }
  $('store-note').textContent = `Release ${dash.data.release.release_id.slice(0, 8)}… (${dash.data.release.schema_version}); the dashboard joins review, topic, label and sentiment by row, exactly as a database would join them by record id.`;
}
function renderAll() {
  const rows = filteredRows();
  renderFilters(); renderKpis(rows); renderTopics(rows); trendChart($('dash-trend'), rows, $('dash-trend-note')); renderPlatforms(rows); renderOverall(rows); renderDetail();
}
function mount(data) {
  dash.data = data;
  if (!dash.mounted) {
    dash.mounted = true;
    dash.platforms = new Set(data.platforms); dash.from = data.months[0]; dash.to = data.months[data.months.length - 1];
    $('filter-from').addEventListener('change', (e) => { dash.from = e.target.value; if (dash.to < dash.from) dash.to = dash.from; renderAll(); });
    $('filter-to').addEventListener('change', (e) => { dash.to = e.target.value; if (dash.from > dash.to) dash.from = dash.to; renderAll(); });
    $('filter-search').addEventListener('input', (e) => { dash.query = e.target.value.trim(); dash.page = 0; renderAll(); });
    $('filter-reset').addEventListener('click', () => { dash.platforms = new Set(data.platforms); dash.sentiments = new Set(SENT); dash.assessments = new Set(Object.keys(ASSESS)); dash.from = data.months[0]; dash.to = data.months[data.months.length - 1]; dash.query = ''; dash.page = 0; renderAll(); });
    for (const button of document.querySelectorAll('#page-dashboard .map-toggle button[data-dsort]')) button.addEventListener('click', () => { dash.sort = button.dataset.dsort; for (const b of document.querySelectorAll('#page-dashboard .map-toggle button[data-dsort]')) { const active = b === button; b.classList.toggle('is-active', active); b.setAttribute('aria-pressed', String(active)); } renderTopics(filteredRows()); });
    $('dash-detail-close').addEventListener('click', () => { dash.topic = null; renderTopics(filteredRows()); renderDetail(); });
    $('dash-detail-prev').addEventListener('click', () => { dash.page -= 1; renderDetail(); });
    $('dash-detail-next').addEventListener('click', () => { dash.page += 1; renderDetail(); });
  }
  $('dash-subtitle').textContent = `${number(data.rows.length)} reviews from ${data.platforms.length} platforms, ${MONTH(data.months[0])} to ${MONTH(data.months[data.months.length - 1])} · ${data.topics.filter((t) => t.topic >= 0).length} topics · release ${data.release.release_id.slice(0, 8)}…`;
  renderStore(); renderAll();
}

window.VOCDashboard = { mount };
