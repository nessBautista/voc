// Nine pages: Overview (projected from the notebook's `overview_section`), Dataset, Embeddings, Reduction, Clusters, Topics, Naming, Sentiment and Dashboard.
// The notebook's `studio_payload` is the only data source for the Dataset page: counts, dates and column names.
// No review text reaches this page, and nothing here recomputes the dataset.

/** Subscribe an explicit `mo-value` host to current values and later updates. */
const observeMarimoValue = (host, { onValue, onError = () => {} }) => {
  const sync = (event) => onValue(event.detail.value);
  const fail = (event) => onError(event.detail);
  host.addEventListener('marimo-value-updated', sync);
  host.addEventListener('marimo-value-error', fail);
  if (host.dataset.marimoError !== undefined) {
    onError({
      selector: host.getAttribute('mo-value')?.trim() ?? '',
      code: host.dataset.marimoErrorCode,
      message: host.dataset.marimoError,
      hint: host.dataset.marimoDiagnosticHint,
    });
  } else if (host.marimoValue !== undefined) {
    onValue(host.marimoValue);
  }
  return () => {
    host.removeEventListener('marimo-value-updated', sync);
    host.removeEventListener('marimo-value-error', fail);
  };
};

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
};
const svg = (tag, attributes = {}) => {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
};

// Fixed platform order and colour slots. Colour follows the entity, never its rank or size.
const PLATFORMS = [
  { key: 'google_play', label: 'Google Play', slot: 1 },
  { key: 'app_store', label: 'App Store', slot: 2 },
  { key: 'x', label: 'X', slot: 3 },
  { key: 'youtube', label: 'YouTube', slot: 4 },
];
const PROVIDERS = { play: 'Play API', appbot: 'Appbot', apple_rss: 'Apple RSS', x: 'X API', youtube: 'YouTube' };
const meta = (key) => PLATFORMS.find((p) => p.key === key) || { key, label: key, slot: 'other' };
const colour = (key) => `var(--series-${meta(key).slot})`;
const orderedKeys = (keys) => [
  ...PLATFORMS.map((p) => p.key).filter((k) => keys.includes(k)),
  ...keys.filter((k) => !PLATFORMS.some((p) => p.key === k)).sort(),
];

const number = (n) => Number(n).toLocaleString('en-US');
const percent = (share, digits = 1) => `${(100 * share).toFixed(digits)}%`;
const trimIso = (iso) => String(iso).replace(/(\.\d{3})\d+/, '$1');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (n) => String(n).padStart(2, '0');
// All dates are read and shown in UTC, matching the notebook's record_date parsing.
const day = (iso) => {
  if (!iso) return '\u2014';
  const d = new Date(trimIso(iso));
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
};
const minute = (iso) => (iso ? `${day(iso)}, ${pad(new Date(trimIso(iso)).getUTCHours())}:${pad(new Date(trimIso(iso)).getUTCMinutes())} UTC` : '\u2014');
const monthLabel = (ym) => {
  const [year, month] = ym.split('-').map(Number);
  return `${MONTHS[month - 1]} ${String(year).slice(-2)}`;
};
const megabytes = (bytes) => `${(bytes / 1_000_000).toFixed(1)} MB`;

// One shared tooltip for every mark; it follows the pointer and also opens on keyboard focus.
const tooltip = node('div', undefined, 'tooltip');
tooltip.setAttribute('role', 'tooltip');
tooltip.hidden = true;
document.body.append(tooltip);
function showTooltip(title, rows, x, y) {
  tooltip.replaceChildren(node('strong', title));
  for (const [label, value, key] of rows) {
    const row = node('div', undefined, 'row');
    const name = node('span');
    if (key) { const dot = node('i', undefined, 'swatch'); dot.style.background = colour(key); name.append(dot); }
    name.append(document.createTextNode(label));
    row.append(name, node('span', value));
    tooltip.append(row);
  }
  tooltip.hidden = false;
  const width = tooltip.offsetWidth, height = tooltip.offsetHeight;
  const left = Math.min(x + 14, window.scrollX + document.documentElement.clientWidth - width - 8);
  const top = y - height - 12 < window.scrollY ? y + 18 : y - height - 12;
  tooltip.style.left = `${Math.max(window.scrollX + 8, left)}px`;
  tooltip.style.top = `${top}px`;
}
const hideTooltip = () => { tooltip.hidden = true; };
function attachTooltip(target, build) {
  const fromPointer = (event) => { const [title, rows] = build(); showTooltip(title, rows, event.pageX, event.pageY); };
  const fromFocus = () => {
    const box = target.getBoundingClientRect();
    const [title, rows] = build();
    showTooltip(title, rows, box.left + box.width / 2 + window.scrollX, box.top + window.scrollY);
  };
  target.addEventListener('pointerenter', fromPointer);
  target.addEventListener('pointermove', fromPointer);
  target.addEventListener('pointerleave', hideTooltip);
  target.addEventListener('focus', fromFocus);
  target.addEventListener('blur', hideTooltip);
}

function showError(message) {
  $('report').hidden = true;
  $('status').hidden = false;
  $('status').textContent = message;
  $('lede').textContent = 'No workable release is loaded.';
  $('identity').replaceChildren();
  $('page-dataset').setAttribute('aria-busy', 'false');
}

function render(data) {
  $('page-dataset').setAttribute('aria-busy', 'true');
  try {
    renderLede(data);
    renderMetrics(data);
    renderDates(data);
    renderSources(data);
    renderMonths(data);
    renderSchema(data);
    renderIdentity(data);
    $('status').hidden = true;
    $('report').hidden = false;
  } finally {
    $('page-dataset').setAttribute('aria-busy', 'false');
  }
}

function renderLede(data) {
  const { counts, dates, release } = data;
  $('lede').textContent = `${number(counts.rows)} records from ${counts.platforms} platforms, ${day(dates.first_record)} to ${day(dates.last_record)}. `
    + `Published ${minute(release.created_at)}${release.publisher_id ? ` by ${release.publisher_id}` : ''}.`;
}

function renderMetrics(data) {
  const { counts, months, providers } = data;
  const span = months.length ? `${monthLabel(months[0].month)} – ${monthLabel(months[months.length - 1].month)}` : 'no dated records';
  const tiles = [
    [number(counts.rows), 'Records', `${data.release.schema_version || 'workable'} rows loaded`],
    [number(counts.columns), 'Columns', 'every column required by the schema'],
    [number(counts.platforms), 'Platforms', `${providers.length} upstream providers`],
    [number(months.length), 'Months covered', span],
  ];
  $('metrics').replaceChildren();
  for (const [value, label, context] of tiles) {
    const tile = node('div', undefined, 'metric');
    tile.append(node('strong', value), node('span', label), node('small', context));
    $('metrics').append(tile);
  }
}

function renderDates(data) {
  const { release, dates, counts } = data;
  const items = [
    ['Release published', minute(release.created_at), 'manifest created_at'],
    ['First record', day(dates.first_record), 'earliest record_date'],
    ['Last record', day(dates.last_record), 'latest record_date'],
    ['Loaded into the notebook', minute(data.loaded_at), 'when the notebook loaded the pinned release'],
    ['Records without a date', number(counts.unknown_dates), 'record_date could not be parsed'],
  ];
  $('dates').replaceChildren();
  for (const [term, value, note] of items) {
    const box = node('div');
    const dd = node('dd', value);
    dd.append(node('small', note));
    box.append(node('dt', term), dd);
    $('dates').append(box);
  }
}

// Ring: arcs from 12 o'clock clockwise, 2px paper gaps between slices, labels outside the ring.
function arcPath(cx, cy, outer, inner, a0, a1) {
  const end = a1 - a0 >= 2 * Math.PI - 1e-6 ? a0 + 2 * Math.PI - 1e-4 : a1;
  const point = (r, a) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  const [x0, y0] = point(outer, a0), [x1, y1] = point(outer, end);
  const [x2, y2] = point(inner, end), [x3, y3] = point(inner, a0);
  const large = end - a0 > Math.PI ? 1 : 0;
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${outer} ${outer} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`
    + `L${x2.toFixed(2)} ${y2.toFixed(2)}A${inner} ${inner} 0 ${large} 0 ${x3.toFixed(2)} ${y3.toFixed(2)}Z`;
}

function renderSources(data) {
  const platforms = orderedKeys(data.platforms.map((p) => p.platform)).map((key) => data.platforms.find((p) => p.platform === key));
  const total = platforms.reduce((sum, p) => sum + p.records, 0);
  const chart = $('donut');
  for (const child of [...chart.children]) if (!['title', 'desc'].includes(child.tagName)) child.remove();
  $('donut-desc').textContent = platforms.map((p) => `${meta(p.platform).label}: ${number(p.records)} records, ${percent(p.records / total)}`).join('; ');
  $('donut-center').querySelector('strong').textContent = number(total);

  const cx = 180, cy = 120, outer = 92, inner = 58, labelRadius = 106;
  let angle = 0;
  const labels = [];
  for (const p of platforms) {
    const sweep = total ? (2 * Math.PI * p.records) / total : 0;
    const slice = svg('path', { d: arcPath(cx, cy, outer, inner, angle, angle + sweep), tabindex: 0, role: 'listitem' });
    slice.style.fill = colour(p.platform);
    slice.setAttribute('aria-label', `${meta(p.platform).label}: ${number(p.records)} records, ${percent(p.records / total)}`);
    attachTooltip(slice, () => [meta(p.platform).label, [
      ['Records', number(p.records)], ['Share', percent(p.records / total)],
      ['Providers', p.providers.map((k) => PROVIDERS[k] || k).join(', ')],
      ['Dates', `${day(p.first_record)} – ${day(p.last_record)}`],
    ]]);
    chart.append(slice);
    const mid = angle + sweep / 2;
    labels.push({ p, mid, side: Math.sin(mid) >= 0 ? 1 : -1, y: cy - labelRadius * Math.cos(mid) });
    angle += sweep;
  }
  // Direct labels, pushed apart per side so neighbouring small slices stay legible.
  for (const side of [1, -1]) {
    const group = labels.filter((l) => l.side === side).sort((a, b) => a.y - b.y);
    for (let i = 1; i < group.length; i += 1) group[i].y = Math.max(group[i].y, group[i - 1].y + 15);
    for (let i = group.length - 2; i >= 0; i -= 1) group[i].y = Math.min(group[i].y, group[i + 1].y - 15);
  }
  for (const { p, mid, side, y } of labels) {
    const share = p.records / total;
    const [ax, ay] = [cx + (outer + 3) * Math.sin(mid), cy - (outer + 3) * Math.cos(mid)];
    const tx = cx + side * (labelRadius + 10);
    const leader = svg('path', { d: `M${ax.toFixed(1)} ${ay.toFixed(1)}L${(cx + side * labelRadius).toFixed(1)} ${y.toFixed(1)}L${tx.toFixed(1)} ${y.toFixed(1)}`, class: 'leader' });
    const text = svg('text', { x: tx + side * 4, y: y + 4, 'text-anchor': side > 0 ? 'start' : 'end' });
    text.append(document.createTextNode(`${meta(p.platform).label} `));
    const pct = svg('tspan', { class: 'pct' });
    pct.textContent = percent(share, share < 0.1 ? 1 : 0);
    text.append(pct);
    chart.append(leader, text);
  }

  const body = $('source-table').querySelector('tbody');
  body.replaceChildren();
  for (const p of platforms) {
    const row = node('tr');
    const name = node('td');
    const dot = node('i', undefined, 'swatch'); dot.style.background = colour(p.platform);
    name.append(dot, document.createTextNode(meta(p.platform).label));
    row.append(
      name,
      node('td', number(p.records), 'num'),
      node('td', percent(p.records / total), 'num'),
      node('td', p.providers.map((k) => PROVIDERS[k] || k).join(', ')),
      node('td', day(p.first_record)),
      node('td', day(p.last_record)),
      node('td', p.mean_rating === null ? '—' : `${p.mean_rating.toFixed(2)} ★`, 'num'),
    );
    body.append(row);
  }
  const foot = node('tr');
  foot.append(node('td', 'All platforms'), node('td', number(total), 'num'), node('td', '100%', 'num'), node('td', `${data.providers.length} providers`), node('td', day(data.dates.first_record)), node('td', day(data.dates.last_record)), node('td', '', 'num'));
  const tfoot = node('tfoot'); tfoot.append(foot);
  $('source-table').querySelector('tfoot')?.remove();
  $('source-table').append(tfoot);
}

function renderMonths(data) {
  const months = data.months;
  const keys = orderedKeys([...new Set(months.flatMap((m) => Object.keys(m).filter((k) => k !== 'month')))]);
  const totals = months.map((m) => keys.reduce((sum, k) => sum + (m[k] || 0), 0));
  const max = Math.max(1, ...totals);
  const peak = totals.indexOf(Math.max(...totals));

  const legend = $('months-legend');
  legend.replaceChildren();
  for (const key of keys) {
    const item = node('span');
    const dot = node('i', undefined, 'swatch'); dot.style.background = colour(key);
    item.append(dot, document.createTextNode(meta(key).label));
    legend.append(item);
  }

  const chart = $('months-chart');
  chart.replaceChildren();
  const grid = node('div', undefined, 'grid');
  for (const fraction of [0.25, 0.5, 0.75, 1]) {
    const line = node('div', undefined, 'gridline');
    line.style.bottom = `${fraction * 100}%`;
    line.append(node('span', number(Math.round(max * fraction))));
    grid.append(line);
  }
  chart.append(grid);
  const bars = node('div', undefined, 'bars');
  months.forEach((m, i) => {
    const col = node('div', undefined, 'col');
    const stack = node('button', undefined, 'stack');
    stack.type = 'button';
    stack.style.height = `${(100 * totals[i]) / max}%`;
    stack.setAttribute('aria-label', `${monthLabel(m.month)}: ${number(totals[i])} records`);
    for (const key of [...keys].reverse()) {
      const segment = node('span', undefined, 'seg');
      segment.style.flexBasis = totals[i] ? `${(100 * (m[key] || 0)) / totals[i]}%` : '0%';
      segment.style.background = colour(key);
      stack.append(segment);
    }
    attachTooltip(stack, () => [`${monthLabel(m.month)} · ${number(totals[i])} records`, keys.map((k) => [meta(k).label, `${number(m[k] || 0)} · ${percent((m[k] || 0) / Math.max(1, totals[i]), 0)}`, k])]);
    if (i === peak || i === months.length - 1) {
      const label = node('span', number(totals[i]), 'bar-label');
      label.style.bottom = `calc(${(100 * totals[i]) / max}% + 4px)`;
      col.append(label);
    }
    col.append(stack, node('span', monthLabel(m.month), 'x-label'));
    bars.append(col);
  });
  chart.append(bars);
  if (!months.length) chart.append(node('p', 'No dated records to chart.', 'subtle'));

  const head = $('months-table').querySelector('thead'), body = $('months-table').querySelector('tbody');
  const headRow = node('tr');
  headRow.append(node('th', 'Month'));
  for (const key of keys) { const th = node('th', meta(key).label, 'num'); th.scope = 'col'; headRow.append(th); }
  headRow.append(node('th', 'Total', 'num'));
  head.replaceChildren(headRow);
  body.replaceChildren();
  months.forEach((m, i) => {
    const row = node('tr');
    const th = node('th', monthLabel(m.month)); th.scope = 'row';
    row.append(th);
    for (const key of keys) row.append(node('td', number(m[key] || 0), 'num'));
    row.append(node('td', number(totals[i]), 'num'));
    body.append(row);
  });
}

function renderSchema(data) {
  const rows = data.counts.rows;
  $('schema-note').textContent = `${data.columns.length} columns in ${data.release.schema_version || 'this schema'}. Filled shows how many rows carry a value; `
    + 'ratings, titles and app identifiers only exist for store reviews.';
  const body = $('schema-table').querySelector('tbody');
  body.replaceChildren();
  for (const column of data.columns) {
    const share = rows ? column.non_null / rows : 0;
    const row = node('tr');
    const name = node('td'); name.append(node('code', column.name));
    const filled = node('td');
    const track = node('span', undefined, 'fill-track'); const bar = node('span', undefined, 'fill-bar'); bar.style.width = `${100 * share}%`; track.append(bar);
    filled.append(track, node('span', percent(share, share < 0.999 && share > 0.99 ? 1 : 0), 'fill-pct'));
    row.append(name, node('td', column.dtype), node('td', number(column.non_null), 'num'), filled);
    body.append(row);
  }
}

function renderIdentity(data) {
  const { release, counts } = data;
  const consistent = release.row_count === counts.rows;
  const items = [
    ['Release ID', release.release_id, true],
    ['Workable artifact', release.artifact_id, true],
    ['Raw revision', release.raw_revision_id, true],
    ['Schema', release.schema_version, true],
    ['Stage · source', `${release.stage} · ${release.source}`],
    ['Publisher', release.publisher_id || '—'],
    ['Rows', `${number(counts.rows)} loaded${release.row_count === undefined || release.row_count === null ? '' : consistent ? ' · matches the manifest' : ` · manifest says ${number(release.row_count)}`}`],
    ['Parquet size', release.size_bytes ? megabytes(release.size_bytes) : '—'],
  ];
  $('identity').replaceChildren();
  for (const [term, value, mono] of items) {
    const dd = node('dd');
    if (mono) dd.append(node('code', value ?? '—')); else dd.textContent = value ?? '—';
    $('identity').append(node('dt', term), dd);
  }
}

// ---------------------------------------------------------------- Embeddings page
// `embedding_payload` carries the model facts, the sampled texts, exact top-5 neighbours per row,
// and an int8-quantised copy of the vectors. The page decodes the vectors for display and for a
// live dot product; exact similarities always come from the notebook.
const emb = { data: null, q: null, dims: 0, scale: 0, index: 0 };
// Reduction page state is declared here because the embeddings observer above triggers its render.
const red = { data: null, rendered: false, selected: null };
const clu = { data: null, view: 'hdbscan', selected: 'all', page: 0 };
const top = { data: null, walkTopic: 0, selected: null, page: 0 };
const nam = { data: null, topic: 0, filter: 'all' };
const sen = { data: null, topic: null, sort: 'negative', filter: 'all', page: 0 };
const dsh = { data: null };
const decodeInt8 = (base64) => {
  const binary = atob(base64);
  const out = new Int8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = (binary.charCodeAt(i) << 24) >> 24;
  return out;
};
const vectorAt = (i) => {
  const out = new Float32Array(emb.dims);
  const offset = i * emb.dims;
  for (let k = 0; k < emb.dims; k += 1) out[k] = emb.q[offset + k] * emb.scale;
  return out;
};
const dot = (a, b) => { let sum = 0; for (let k = 0; k < a.length; k += 1) sum += a[k] * b[k]; return sum; };
const signed = (x, digits = 4) => (x < 0 ? '−' : '+') + Math.abs(x).toFixed(digits);
const PLATFORM_LABEL = (key) => meta(key).label;

function platformTag(target, key) {
  target.replaceChildren();
  const dotEl = node('i', undefined, 'swatch'); dotEl.style.background = colour(key);
  target.append(dotEl, document.createTextNode(PLATFORM_LABEL(key)));
}

function showEmbeddingsError(message) {
  $('embeddings-report').hidden = true;
  $('embeddings-status').hidden = false;
  $('embeddings-status').textContent = message;
  $('embeddings-lede').textContent = 'No embeddings are loaded.';
  $('page-embeddings').setAttribute('aria-busy', 'false');
}

function renderEmbeddings(data) {
  $('page-embeddings').setAttribute('aria-busy', 'true');
  try {
    emb.data = data;
    emb.dims = data.vectors.dims;
    emb.scale = data.vectors.scale;
    emb.q = decodeInt8(data.vectors.int8_base64);
    if (emb.q.length !== data.rows.length * emb.dims) throw new Error('vector payload does not match the row count');
    const { model, stats } = data;
    $('embeddings-lede').textContent = `${number(stats.rows)} reviews encoded with ${model.id.split('/').pop()} into ${number(model.dimensions)}-dimensional unit vectors. `
      + `Median input ${stats.median_tokens} tokens; ${stats.truncated} review${stats.truncated === 1 ? '' : 's'} longer than the ${model.max_tokens}-token limit.`;
    const facts = [
      ['Model', model.id, true],
      ['Revision', model.revision.slice(0, 12), true],
      ['Output', `${number(model.dimensions)} float32 coordinates per review`],
      ['Length', model.normalized ? 'normalised to 1, so cosine similarity is a dot product' : 'not normalised'],
      ['Input limit', `${model.max_tokens} tokens; longer texts are truncated`],
      ['Runs on', 'CPU, inside the notebook; nothing leaves the machine'],
    ];
    $('model-facts').replaceChildren();
    for (const [term, value, mono] of facts) {
      const dd = node('dd');
      if (mono) dd.append(node('code', value)); else dd.textContent = value;
      $('model-facts').append(node('dt', term), dd);
    }
    const tiles = [
      [number(stats.rows), 'reviews encoded', `seed ${stats.sample_seed} sample`],
      [number(stats.median_tokens), 'median tokens', `longest ${number(stats.max_tokens_seen)}`],
      [number(stats.truncated), 'truncated', `over ${model.max_tokens} tokens`],
      [number(stats.duplicate_texts), 'duplicate texts', 'kept for row alignment'],
    ];
    $('encoder-stats').replaceChildren();
    for (const [value, label, context] of tiles) {
      const tile = node('div', undefined, 'stat');
      tile.append(node('strong', value), node('span', label), node('small', context));
      $('encoder-stats').append(tile);
    }
    $('review-total').textContent = `of ${number(data.rows.length)}`;
    $('review-index').max = String(data.rows.length);
    emb.index = Math.min(emb.index, data.rows.length - 1);
    showReview(emb.index);
    $('embeddings-status').hidden = true;
    $('embeddings-report').hidden = false;
  } finally {
    $('page-embeddings').setAttribute('aria-busy', 'false');
  }
}

function showReview(i) {
  const { data } = emb;
  if (!data) return;
  emb.index = Math.max(0, Math.min(data.rows.length - 1, i));
  const row = data.rows[emb.index];
  $('review-index').value = String(emb.index + 1);
  $('review-prev').disabled = emb.index === 0;
  $('review-next').disabled = emb.index === data.rows.length - 1;
  platformTag($('io-platform'), row.platform);
  $('io-record').textContent = row.record_id;
  $('io-text').textContent = row.text;
  $('io-text-foot').textContent = `${number(row.text.length)} characters · ${number(row.tokens)} tokens${row.tokens > data.model.max_tokens ? ` · truncated to ${data.model.max_tokens}` : ''}`;

  const vector = vectorAt(emb.index);
  let largest = 0, largestAt = 0;
  for (let k = 0; k < vector.length; k += 1) if (Math.abs(vector[k]) > largest) { largest = Math.abs(vector[k]); largestAt = k; }
  const limit = emb.scale * 127;
  const grid = $('vector-grid');
  grid.replaceChildren();
  const cells = document.createDocumentFragment();
  for (let k = 0; k < vector.length; k += 1) {
    const cell = node('i');
    const strength = Math.round((Math.abs(vector[k]) / limit) * 100);
    cell.style.background = `color-mix(in srgb, var(${vector[k] < 0 ? '--sent-neg' : '--sent-pos'}) ${strength}%, var(--surface-1))`;
    cell.title = `dim ${k}: ${signed(vector[k])}`;
    cells.append(cell);
  }
  grid.append(cells);
  grid.setAttribute('aria-label', `${vector.length} coordinates between ${signed(-limit, 3)} and ${signed(limit, 3)}; largest magnitude ${signed(vector[largestAt], 3)} at dimension ${largestAt}`);
  $('vector-head').replaceChildren();
  data.heads[emb.index].forEach((value, k) => {
    const item = node('li');
    item.append(node('span', `dim ${k}`), node('code', signed(value)));
    $('vector-head').append(item);
  });
  $('io-vector-foot').textContent = `${number(vector.length)} float32 values · length 1.000 · largest magnitude at dimension ${largestAt}. Values shown here are rounded for display; the notebook keeps full precision.`;
  if ($('neighbors-dialog').open) renderNeighbors();
}

function renderNeighbors() {
  const { data } = emb;
  const i = emb.index, row = data.rows[i];
  $('dialog-position').textContent = `Review ${number(i + 1)} of ${number(data.rows.length)}`;
  $('dialog-prev').disabled = i === 0;
  $('dialog-next').disabled = i === data.rows.length - 1;
  platformTag($('dialog-platform'), row.platform);
  $('dialog-record').textContent = row.record_id;
  $('dialog-text').textContent = row.text;
  const list = $('neighbor-list');
  list.replaceChildren();
  const self = vectorAt(i);
  data.neighbors[i].forEach(([j, similarity], rank) => {
    const other = data.rows[j];
    const item = node('li', undefined, 'neighbor');
    const button = node('button', undefined, 'neighbor-pick'); button.type = 'button';
    button.setAttribute('aria-label', `Jump to review ${j + 1}, similarity ${similarity.toFixed(3)}`);
    const head = node('div', undefined, 'neighbor-head');
    head.append(node('span', `#${rank + 1}`, 'neighbor-rank'));
    const tag = node('span', undefined, 'platform-tag'); platformTag(tag, other.platform);
    head.append(tag, node('code', other.record_id));
    const score = node('div', undefined, 'neighbor-score');
    const track = node('span', undefined, 'score-track'); const bar = node('span', undefined, 'score-bar');
    bar.style.width = `${Math.max(0, Math.min(100, similarity * 100))}%`; track.append(bar);
    score.append(track, node('strong', similarity.toFixed(3)));
    const text = node('blockquote', other.text, 'io-text'); text.setAttribute('lang', 'es');
    button.append(head, score, text);
    button.addEventListener('click', () => { showReview(j); $('neighbors-dialog').scrollTop = 0; });
    item.append(button);
    list.append(item);
  });
  const [top, exact] = data.neighbors[i][0];
  const here = dot(self, vectorAt(top));
  $('cosine-live').textContent = `For the top neighbour (#1): the dot product recomputed in this page from the rounded vectors is ${here.toFixed(3)}; the exact value from the notebook is ${exact.toFixed(4)}.`;
}

function openNeighbors() {
  const dialog = $('neighbors-dialog');
  if (!emb.data) return;
  renderNeighbors();
  if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
  $('close-neighbors').focus();
}
function closeNeighbors() {
  const dialog = $('neighbors-dialog');
  if (typeof dialog.close === 'function' && dialog.open) dialog.close(); else dialog.removeAttribute('open');
  $('open-neighbors').focus();
}

$('review-prev').addEventListener('click', () => showReview(emb.index - 1));
$('review-next').addEventListener('click', () => showReview(emb.index + 1));
$('review-random').addEventListener('click', () => { if (emb.data) showReview(Math.floor(Math.random() * emb.data.rows.length)); });
$('review-index').addEventListener('change', (event) => { const n = Number.parseInt(event.target.value, 10); if (Number.isFinite(n)) showReview(n - 1); });
$('open-neighbors').addEventListener('click', openNeighbors);
$('close-neighbors').addEventListener('click', closeNeighbors);
$('dialog-prev').addEventListener('click', () => showReview(emb.index - 1));
$('dialog-next').addEventListener('click', () => showReview(emb.index + 1));
$('neighbors-dialog').addEventListener('click', (event) => { if (event.target === event.currentTarget) closeNeighbors(); });

const embeddingHost = $('embedding-payload');
if (embeddingHost) {
  const stopEmbeddings = observeMarimoValue(embeddingHost, {
    onValue: (value) => { try { renderEmbeddings(value); } catch (error) { showEmbeddingsError(`The embeddings page could not render: ${error.message}`); throw error; } tryRenderReduction(); tryRenderClusters(); tryRenderTopics(); tryRenderNaming(); tryRenderSentiment(); tryRenderDashboard(); },
    onError: (error) => showEmbeddingsError(error.message || `Projection ${error.selector || 'embedding_payload'} is unavailable. Run the embedding cells in the notebook.`),
  });
  window.addEventListener('pagehide', stopEmbeddings, { once: true });
}

// ---------------------------------------------------------- Dimensionality reduction page
// Needs both payloads: the vectors (from `embedding_payload`, decoded in `emb`) and `reduction_payload`.

function showReductionError(message) {
  $('reduction-report').hidden = true;
  $('reduction-status').hidden = false;
  $('reduction-status').textContent = message;
  $('reduction-lede').textContent = 'No reduction is loaded.';
  $('page-reduction').setAttribute('aria-busy', 'false');
}

// Scatter of all vectors in two chosen coordinates, with the demo pair highlighted.
function scatterDims(host, dx, dy, caption) {
  const limit = red.data.axis_limit, size = 300, pad = 18;
  const px = (v) => pad + ((v + limit) / (2 * limit)) * (size - 2 * pad);
  const chart = svg('svg', { viewBox: `0 0 ${size} ${size}`, class: 'dims-svg', role: 'img' });
  chart.setAttribute('aria-label', `All reviews in dimensions ${dx} and ${dy}; ${caption}`);
  chart.append(svg('line', { x1: pad, y1: px(0), x2: size - pad, y2: px(0), class: 'dims-axis' }));
  chart.append(svg('line', { x1: px(0), y1: pad, x2: px(0), y2: size - pad, class: 'dims-axis' }));
  const cloud = svg('g', { class: 'dims-cloud' });
  const n = emb.data.rows.length, dims = emb.dims, scale = emb.scale;
  for (let i = 0; i < n; i += 1) {
    const x = emb.q[i * dims + dx] * scale, y = emb.q[i * dims + dy] * scale;
    cloud.append(svg('circle', { cx: px(x).toFixed(1), cy: (size - px(y)).toFixed(1), r: 2.2 }));
  }
  chart.append(cloud);
  const { a, b } = red.data.pair;
  const ax = emb.q[a * dims + dx] * scale, ay = emb.q[a * dims + dy] * scale;
  const bx = emb.q[b * dims + dx] * scale, by = emb.q[b * dims + dy] * scale;
  chart.append(svg('line', { x1: px(ax), y1: size - px(ay), x2: px(bx), y2: size - px(by), class: 'dims-link' }));
  for (const [x, y, label, cls] of [[ax, ay, 'A', 'pair-a'], [bx, by, 'B', 'pair-b']]) {
    chart.append(svg('circle', { cx: px(x), cy: size - px(y), r: 7, class: `dims-pair ${cls}` }));
    const text = svg('text', { x: px(x), y: size - px(y) - 11, class: 'dims-label', 'text-anchor': 'middle' });
    text.textContent = label; chart.append(text);
  }
  const axisX = svg('text', { x: size - pad, y: size - 4, class: 'dims-axis-label', 'text-anchor': 'end' }); axisX.textContent = `dim ${dx}`;
  const axisY = svg('text', { x: 4, y: pad - 6, class: 'dims-axis-label' }); axisY.textContent = `dim ${dy}`;
  chart.append(axisX, axisY);
  host.replaceChildren(chart);
  return Math.hypot(ax - bx, ay - by);
}
const dimsCaption = (dx, dy, distance) => `dims ${dx} × ${dy} · distance ${distance.toFixed(3)}`;

function renderProblem() {
  const { pair } = red.data, rows = emb.data.rows;
  for (const [key, index] of [['a', pair.a], ['b', pair.b]]) {
    platformTag($(`pair-${key}-platform`), rows[index].platform);
    $(`pair-${key}-record`).textContent = rows[index].record_id;
    $(`pair-${key}-text`).textContent = rows[index].text;
  }
  $('pair-similarity').textContent = pair.similarity.toFixed(3);
  const dClose = scatterDims($('panel-close'), pair.close_dims[0], pair.close_dims[1], 'the pair coincides');
  $('panel-close-caption').textContent = dimsCaption(pair.close_dims[0], pair.close_dims[1], dClose);
  const dFar = scatterDims($('panel-far'), pair.far_dims[0], pair.far_dims[1], 'the pair sits far apart');
  $('panel-far-caption').textContent = dimsCaption(pair.far_dims[0], pair.far_dims[1], dFar);
  $('dim-x').max = String(emb.dims - 1); $('dim-y').max = String(emb.dims - 1);
  if (!red.rendered) { $('dim-x').value = String(pair.far_dims[0]); $('dim-y').value = String(pair.far_dims[1]); }
  renderChoice();
}
function renderChoice() {
  const dx = Number($('dim-x').value), dy = Number($('dim-y').value);
  $('dim-x-value').value = String(dx); $('dim-y-value').value = String(dy);
  const d = scatterDims($('panel-choice'), dx, dy, 'your choice of dimensions');
  $('panel-choice-caption').textContent = dimsCaption(dx, dy, d);
}
function setDims(dx, dy) { $('dim-x').value = String(dx); $('dim-y').value = String(dy); renderChoice(); }

// UMAP map of the sample, coloured by platform; click a point to see its 384-D neighbours on the map.
function renderUmap() {
  const { coordinates_2d: coords, params, overlap } = red.data, rows = emb.data.rows;
  const legend = $('umap-legend'); legend.replaceChildren();
  for (const key of orderedKeys([...new Set(rows.map((r) => r.platform))])) {
    const item = node('span'); const dotEl = node('i', undefined, 'swatch'); dotEl.style.background = colour(key);
    item.append(dotEl, document.createTextNode(meta(key).label)); legend.append(item);
  }
  const xs = coords.map((c) => c[0]), ys = coords.map((c) => c[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const W = 640, H = 520, pad = 20;
  const px = (x) => pad + ((x - minX) / (maxX - minX || 1)) * (W - 2 * pad);
  const py = (y) => H - pad - ((y - minY) / (maxY - minY || 1)) * (H - 2 * pad);
  red.project = (i) => [px(coords[i][0]), py(coords[i][1])];
  const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'umap-svg' });
  const links = svg('g', { class: 'umap-links' }); chart.append(links);
  const dots = svg('g', { class: 'umap-dots' });
  rows.forEach((row, i) => {
    const [x, y] = red.project(i);
    const dot = svg('circle', { cx: x.toFixed(1), cy: y.toFixed(1), r: 3.2, tabindex: -1, 'data-index': i });
    dot.style.fill = colour(row.platform);
    dot.setAttribute('aria-label', `${meta(row.platform).label}: ${row.text.slice(0, 80)}`);
    attachTooltip(dot, () => [meta(row.platform).label, [['Review', `#${i + 1}`], ['Text', row.text.length > 140 ? `${row.text.slice(0, 140)}…` : row.text]]]);
    dot.addEventListener('click', () => selectUmap(i));
    dots.append(dot);
  });
  chart.append(dots);
  $('umap-map').replaceChildren(chart);
  red.links = links; red.dots = dots;

  const facts = [
    ['Input', `${number(red.data.input_dimensions)} dimensions per review`],
    ['For clustering', `${params.cluster_dimensions} dimensions · min_dist ${params.min_dist_cluster}`],
    ['For this picture', `2 dimensions · min_dist ${params.min_dist_display}`],
    ['n_neighbors', String(params.n_neighbors)],
    ['Metric', params.metric],
    ['Seed', `${params.seed} · umap-learn ${params.umap_learn}`],
  ];
  $('umap-params').replaceChildren();
  for (const [term, value] of facts) $('umap-params').append(node('dt', term), node('dd', value));
  $('umap-overlap').replaceChildren();
  for (const [value, label, context] of [[percent(overlap.cluster, 0), `kept in ${params.cluster_dimensions}-D`, 'of each review’s 5 nearest neighbours'], [percent(overlap.display, 0), 'kept in 2-D', 'the picture loses more']]) {
    const tile = node('div', undefined, 'stat'); tile.append(node('strong', value), node('span', label), node('small', context)); $('umap-overlap').append(tile);
  }
  if (red.selected !== null) selectUmap(red.selected);
}
function selectUmap(i) {
  red.selected = i;
  const rows = emb.data.rows, neighbors = emb.data.neighbors[i];
  red.links.replaceChildren();
  for (const dot of red.dots.children) dot.classList.toggle('is-selected', Number(dot.dataset.index) === i);
  const [x0, y0] = red.project(i);
  const nearestOnMap = rows.map((_, j) => j).filter((j) => j !== i)
    .sort((a, b) => { const [ax, ay] = red.project(a), [bx, by] = red.project(b); return Math.hypot(ax - x0, ay - y0) - Math.hypot(bx - x0, by - y0); }).slice(0, 5);
  let kept = 0;
  for (const [j] of neighbors) {
    const [x1, y1] = red.project(j);
    red.links.append(svg('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: 'umap-link' }));
    red.links.append(svg('circle', { cx: x1, cy: y1, r: 7, class: 'umap-ring' }));
    if (nearestOnMap.includes(j)) kept += 1;
  }
  red.links.append(svg('circle', { cx: x0, cy: y0, r: 9, class: 'umap-focus' }));
  $('umap-selected').hidden = false; $('umap-hint').hidden = true;
  $('umap-selected-text').textContent = rows[i].text;
  $('umap-selected-note').textContent = `${kept} of its 5 nearest neighbours in 384-D are also among its 5 nearest points on this map.`;
  const list = $('umap-neighbors'); list.replaceChildren();
  for (const [j, similarity] of neighbors) {
    const item = node('li'); const button = node('button', undefined, 'umap-neighbor'); button.type = 'button';
    button.append(node('strong', similarity.toFixed(3)), node('span', rows[j].text.length > 90 ? `${rows[j].text.slice(0, 90)}…` : rows[j].text));
    button.addEventListener('click', () => selectUmap(j)); item.append(button); list.append(item);
  }
}

function tryRenderReduction() {
  if (!red.data || !emb.data) return;
  $('page-reduction').setAttribute('aria-busy', 'true');
  try {
    const { params, overlap } = red.data;
    $('reduction-lede').textContent = `UMAP squeezes ${number(red.data.input_dimensions)} coordinates into ${params.cluster_dimensions} for clustering and 2 for looking, keeping `
      + `${percent(overlap.cluster, 0)} of each review’s nearest neighbours in ${params.cluster_dimensions}-D and ${percent(overlap.display, 0)} in 2-D.`;
    renderProblem();
    renderUmap();
    red.rendered = true;
    $('reduction-status').hidden = true;
    $('reduction-report').hidden = false;
  } catch (error) {
    showReductionError(`The reduction page could not render: ${error.message}`);
    throw error;
  } finally {
    $('page-reduction').setAttribute('aria-busy', 'false');
  }
}

// Modal panels for the two algorithms (demos live in reduction.js).
function openModal(id, mount) {
  const dialog = $(id);
  if (window.VOCReductionDemos && mount) window.VOCReductionDemos[mount]();
  if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
  dialog.querySelector('.dialog-close')?.focus();
}
function closeModal(dialog) {
  if (typeof dialog.close === 'function' && dialog.open) dialog.close(); else dialog.removeAttribute('open');
}
$('dim-x').addEventListener('input', renderChoice);
$('dim-y').addEventListener('input', renderChoice);
$('dims-coincide').addEventListener('click', () => setDims(red.data.pair.close_dims[0], red.data.pair.close_dims[1]));
$('dims-diverge').addEventListener('click', () => setDims(red.data.pair.far_dims[0], red.data.pair.far_dims[1]));
$('dims-random').addEventListener('click', () => setDims(Math.floor(Math.random() * emb.dims), Math.floor(Math.random() * emb.dims)));
$('open-umap').addEventListener('click', () => openModal('umap-dialog', 'mountUMAP'));
$('open-pca').addEventListener('click', () => openModal('pca-dialog', 'mountPCA'));
for (const button of document.querySelectorAll('.dialog-close[data-close]')) button.addEventListener('click', () => closeModal($(button.dataset.close)));
for (const dialog of document.querySelectorAll('dialog.modal')) dialog.addEventListener('click', (event) => { if (event.target === event.currentTarget) closeModal(dialog); });

const reductionHost = $('reduction-payload');
if (reductionHost) {
  const stopReduction = observeMarimoValue(reductionHost, {
    onValue: (value) => { red.data = value; tryRenderReduction(); tryRenderClusters(); tryRenderTopics(); tryRenderNaming(); tryRenderSentiment(); tryRenderDashboard(); },
    onError: (error) => showReductionError(error.message || `Projection ${error.selector || 'reduction_payload'} is unavailable. Run the reduction cells in the notebook.`),
  });
  window.addEventListener('pagehide', stopReduction, { once: true });
}

// ----------------------------------------------------------------- Clustering page
// Needs three payloads: cluster labels (`cluster_payload`), texts (`embedding_payload`) and the 2-D
// coordinates (`reduction_payload`). The eight largest groups get the categorical colour slots; the rest
// share one muted colour, and the selected group is always drawn in ink.
const SLOTS = 8;
const PAGE_SIZE = 8;

function showClustersError(message) {
  $('clusters-report').hidden = true;
  $('clusters-status').hidden = false;
  $('clusters-status').textContent = message;
  $('clusters-lede').textContent = 'No clustering is loaded.';
  $('page-clusters').setAttribute('aria-busy', 'false');
}

// Groups of the current view (HDBSCAN or k-means), largest first, with their colour slot.
function groupTable() {
  const labels = clu.view === 'kmeans' ? clu.data.kmeans_labels : clu.data.labels;
  const sizes = new Map();
  labels.forEach((l) => { if (l >= 0) sizes.set(l, (sizes.get(l) || 0) + 1); });
  const order = [...sizes.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0]);
  const slot = new Map(order.map(([id], rank) => [id, rank < SLOTS ? rank + 1 : 'other']));
  return { labels, order, slot, unassigned: labels.filter((l) => l < 0).length };
}
const groupColour = (slotValue) => (slotValue === 'other' ? 'var(--series-other)' : `var(--series-${slotValue})`);

function renderClusterLegend(table) {
  const legend = $('cluster-legend'); legend.replaceChildren();
  table.order.slice(0, SLOTS).forEach(([id, size], rank) => {
    const item = node('span'); const dotEl = node('i', undefined, 'swatch'); dotEl.style.background = groupColour(rank + 1);
    item.append(dotEl, document.createTextNode(`group ${id} (${number(size)})`)); legend.append(item);
  });
  if (table.order.length > SLOTS) {
    const item = node('span'); const dotEl = node('i', undefined, 'swatch'); dotEl.style.background = groupColour('other');
    item.append(dotEl, document.createTextNode(`${table.order.length - SLOTS} smaller groups`)); legend.append(item);
  }
  if (table.unassigned) {
    const item = node('span'); const cross = node('i', undefined, 'swatch swatch-cross'); item.append(cross, document.createTextNode(`unassigned (${number(table.unassigned)})`)); legend.append(item);
  }
}

function renderClusterMap(table) {
  const coords = red.data.coordinates_2d, rows = emb.data.rows;
  const xs = coords.map((c) => c[0]), ys = coords.map((c) => c[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const W = 640, H = 520, pad = 20;
  const px = (x) => pad + ((x - minX) / (maxX - minX || 1)) * (W - 2 * pad);
  const py = (y) => H - pad - ((y - minY) / (maxY - minY || 1)) * (H - 2 * pad);
  const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'umap-svg' });
  const dots = svg('g', { class: 'cluster-dots' });
  const selected = clu.selected;
  rows.forEach((row, i) => {
    const label = table.labels[i];
    const x = px(coords[i][0]), y = py(coords[i][1]);
    const isSelected = selected !== 'all' && label === selected;
    const faded = selected !== 'all' && !isSelected;
    let mark;
    if (label < 0) {
      mark = svg('path', { d: `M${x - 3} ${y - 3}L${x + 3} ${y + 3}M${x + 3} ${y - 3}L${x - 3} ${y + 3}`, class: `cluster-noise${isSelected ? ' is-selected' : ''}${faded ? ' is-faded' : ''}` });
    } else {
      mark = svg('circle', { cx: x.toFixed(1), cy: y.toFixed(1), r: isSelected ? 4.5 : 3.2, class: `cluster-dot${isSelected ? ' is-selected' : ''}${faded ? ' is-faded' : ''}` });
      mark.style.fill = isSelected ? 'var(--ink)' : groupColour(table.slot.get(label));
    }
    mark.setAttribute('aria-label', `${label < 0 ? 'unassigned' : `group ${label}`}: ${row.text.slice(0, 80)}`);
    attachTooltip(mark, () => [label < 0 ? 'Unassigned' : `Group ${label}`, [['Review', `#${i + 1}`], ['Platform', meta(row.platform).label], ['Text', row.text.length > 140 ? `${row.text.slice(0, 140)}…` : row.text]]]);
    mark.addEventListener('click', () => selectGroup(label));
    dots.append(mark);
  });
  chart.append(dots);
  $('cluster-map').replaceChildren(chart);
}

function renderGroupList(table) {
  const list = $('group-list'); list.replaceChildren();
  const entries = [['all', 'All groups', table.labels.length, null]];
  if (table.unassigned) entries.push([-1, 'Unassigned', table.unassigned, 'cross']);
  table.order.forEach(([id, size]) => entries.push([id, `Group ${id}`, size, table.slot.get(id)]));
  const total = table.labels.length;
  for (const [id, label, size, slotValue] of entries) {
    const item = node('li');
    const button = node('button', undefined, 'group-pick'); button.type = 'button';
    button.setAttribute('aria-pressed', String(clu.selected === id));
    const swatch = node('i', undefined, slotValue === 'cross' ? 'swatch swatch-cross' : 'swatch');
    if (slotValue && slotValue !== 'cross') swatch.style.background = groupColour(slotValue);
    if (slotValue === null) swatch.style.visibility = 'hidden';
    const name = node('span', label, 'group-name');
    const count = node('span', undefined, 'group-count'); count.append(node('strong', number(size)), node('small', percent(size / total, 0)));
    const bar = node('span', undefined, 'group-bar'); const fill = node('span'); fill.style.width = `${(100 * size) / total}%`; bar.append(fill);
    button.append(swatch, name, count, bar);
    button.addEventListener('click', () => selectGroup(id));
    item.append(button); list.append(item);
  }
}

function groupMembers(id) {
  const table = groupTable(); const rows = emb.data.rows, probs = clu.data.probabilities;
  const members = [];
  table.labels.forEach((l, i) => { if (l === id) members.push({ i, text: rows[i].text, platform: rows[i].platform, probability: clu.view === 'hdbscan' && l >= 0 ? probs[i] : null }); });
  members.sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0) || a.i - b.i);
  return members;
}

function renderGroupPanel() {
  const panel = $('group-panel');
  if (clu.selected === 'all') { panel.hidden = true; return; }
  panel.hidden = false;
  const id = clu.selected, members = groupMembers(id);
  const record = clu.view === 'hdbscan' ? clu.data.clusters.find((c) => c.cluster === id) : null;
  $('group-panel-eyebrow').textContent = id < 0 ? 'Not in any group' : `${clu.view === 'kmeans' ? 'k-means group' : 'Group'} ${id}`;
  $('group-panel-title').textContent = id < 0 ? 'Unassigned reviews' : `${number(members.length)} reviews that say similar things`;
  const platforms = Object.entries(members.reduce((acc, m) => { acc[m.platform] = (acc[m.platform] || 0) + 1; return acc; }, {}))
    .sort((a, b) => b[1] - a[1]).map(([k, v]) => `${meta(k).label} ${v}`).join(', ');
  $('group-panel-meta').textContent = `${percent(members.length / clu.data.counts.rows, 1)} of the sample · ${platforms}`
    + (record ? ` · mean membership strength ${record.mean_probability.toFixed(2)}` : '')
    + (id < 0 ? ' · HDBSCAN found no dense region around these; they are not one subject' : '');
  const query = $('group-search').value.trim().toLowerCase();
  const filtered = query ? members.filter((m) => m.text.toLowerCase().includes(query)) : members;
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  clu.page = Math.min(clu.page, pages - 1);
  const list = $('group-reviews'); list.replaceChildren();
  for (const m of filtered.slice(clu.page * PAGE_SIZE, (clu.page + 1) * PAGE_SIZE)) {
    const item = node('li', undefined, 'group-review');
    const head = node('div', undefined, 'neighbor-head');
    const tag = node('span', undefined, 'platform-tag'); platformTag(tag, m.platform);
    head.append(tag, node('code', emb.data.rows[m.i].record_id));
    if (m.probability !== null) head.append(node('span', `strength ${m.probability.toFixed(2)}`, 'strength'));
    const text = node('blockquote', m.text, 'io-text'); text.setAttribute('lang', 'es');
    item.append(head, text); list.append(item);
  }
  if (!filtered.length) list.append(node('li', 'No review in this group matches the search.', 'subtle'));
  $('group-page').textContent = `${number(filtered.length)} review${filtered.length === 1 ? '' : 's'} · page ${clu.page + 1} of ${pages}`;
  $('group-prev').disabled = clu.page === 0; $('group-next').disabled = clu.page >= pages - 1;
}

function selectGroup(id) {
  clu.selected = id; clu.page = 0;
  if (id !== 'all') $('group-search').value = '';
  const table = groupTable();
  renderClusterMap(table); renderGroupList(table); renderGroupPanel();
  if (id !== 'all') $('group-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function setClusterView(view) {
  clu.view = view; clu.selected = 'all'; clu.page = 0;
  $('show-hdbscan').classList.toggle('is-active', view === 'hdbscan'); $('show-hdbscan').setAttribute('aria-pressed', String(view === 'hdbscan'));
  $('show-kmeans').classList.toggle('is-active', view === 'kmeans'); $('show-kmeans').setAttribute('aria-pressed', String(view === 'kmeans'));
  const table = groupTable();
  $('cluster-result-note').textContent = view === 'hdbscan'
    ? `HDBSCAN: ${number(clu.data.counts.groups)} groups and ${number(clu.data.counts.unassigned)} unassigned reviews (${percent(clu.data.counts.unassigned / clu.data.counts.rows, 0)}). Click a group in the list or on the map to read its reviews.`
    : `k-means with k = ${clu.data.kmeans.k}: every review assigned, no outliers. Click a group to read its reviews.`;
  renderClusterLegend(table); renderClusterMap(table); renderGroupList(table); renderGroupPanel();
}

function tryRenderClusters() {
  if (!clu.data || !emb.data || !red.data) return;
  $('page-clusters').setAttribute('aria-busy', 'true');
  try {
    const { counts, params, comparison } = clu.data;
    $('clusters-lede').textContent = `HDBSCAN found ${number(counts.groups)} groups among the ${number(counts.rows)} reviews and left ${number(counts.unassigned)} (${percent(counts.unassigned / counts.rows, 0)}) unassigned. `
      + `The largest group holds ${number(clu.data.clusters[0].size)} reviews.`;
    $('cluster-params').replaceChildren();
    for (const [term, value] of [['min_cluster_size', String(params.min_cluster_size)], ['min_samples', String(params.min_samples)], ['Metric', `${params.metric} on the 5-D UMAP output`], ['Selection', `${params.cluster_selection_method} (excess of mass)`], ['Library', `hdbscan ${params.hdbscan}`]]) {
      $('cluster-params').append(node('dt', term), node('dd', value));
    }
    const body = $('cluster-comparison').querySelector('tbody'); body.replaceChildren();
    for (const row of comparison) {
      const tr = node('tr');
      tr.append(node('td', row.method), node('td', number(row.groups), 'num'), node('td', number(row.unassigned), 'num'), node('td', row.silhouette.toFixed(3), 'num'), node('td', row.silhouette_over), node('td', number(row.smallest_group), 'num'), node('td', number(row.largest_group), 'num'));
      body.append(tr);
    }
    setClusterView(clu.view);
    $('clusters-status').hidden = true;
    $('clusters-report').hidden = false;
  } catch (error) {
    showClustersError(`The clustering page could not render: ${error.message}`);
    throw error;
  } finally {
    $('page-clusters').setAttribute('aria-busy', 'false');
  }
}

$('show-hdbscan').addEventListener('click', () => setClusterView('hdbscan'));
$('show-kmeans').addEventListener('click', () => setClusterView('kmeans'));
$('group-search').addEventListener('input', () => { clu.page = 0; renderGroupPanel(); });
$('group-prev').addEventListener('click', () => { clu.page -= 1; renderGroupPanel(); });
$('group-next').addEventListener('click', () => { clu.page += 1; renderGroupPanel(); });
$('open-hdbscan').addEventListener('click', () => { const d = $('hdbscan-dialog'); window.VOCClusterDemos?.mountHDBSCAN(); if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); d.querySelector('.dialog-close')?.focus(); });
$('open-kmeans').addEventListener('click', () => { const d = $('kmeans-dialog'); window.VOCClusterDemos?.mountKMeans(); if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); d.querySelector('.dialog-close')?.focus(); });

const clusterHost = $('cluster-payload');
if (clusterHost) {
  const stopClusters = observeMarimoValue(clusterHost, {
    onValue: (value) => { clu.data = value; tryRenderClusters(); tryRenderTopics(); },
    onError: (error) => showClustersError(error.message || `Projection ${error.selector || 'cluster_payload'} is unavailable. Run the clustering cells in the notebook.`),
  });
  window.addEventListener('pagehide', stopClusters, { once: true });
}

// --------------------------------------------------------------------- Topics page
// Needs `topic_payload` (keywords, formula numbers), `embedding_payload` (texts) and `cluster_payload`
// (membership strengths, to order a topic's reviews).
function showTopicsError(message) {
  $('topics-report').hidden = true;
  $('topics-status').hidden = false;
  $('topics-status').textContent = message;
  $('topics-lede').textContent = 'No topics are loaded.';
  $('page-topics').setAttribute('aria-busy', 'false');
}
const topicRecord = (id) => top.data.topics.find((t) => t.topic === id);
const topicLabel = (id) => (id < 0 ? 'Unassigned' : `Topic ${id}`);
const shortKeywords = (t, n = 4) => t.keywords.slice(0, n).map((k) => k.term).join(' · ');

// Step 2 of the formula in the browser, so the square-root toggle can be shown.
const tfPart = (term, words, useSqrt) => (useSqrt ? Math.sqrt(term.tf / words) : term.tf / words);

function renderWalk() {
  const walk = top.data.walk.find((w) => w.topic === top.walkTopic);
  const useSqrt = $('walk-sqrt').checked;
  const terms = walk.terms.map((t) => ({ ...t, tfp: tfPart(t, walk.words, useSqrt), score: tfPart(t, walk.words, useSqrt) * t.idf }));
  terms.sort((a, b) => b.score - a.score);
  const maxTf = Math.max(...terms.map((t) => t.tf)), maxTfp = Math.max(...terms.map((t) => t.tfp)), maxIdf = Math.max(...terms.map((t) => t.idf)), maxScore = Math.max(...terms.map((t) => t.score));
  const table = $('walk-table'); table.replaceChildren();
  const header = node('div', undefined, 'walk-row walk-head'); header.setAttribute('role', 'row');
  for (const [label, step] of [['term', ''], ['1 · count in this topic', 'count'], ['2 · share of its words' + (useSqrt ? ', square-rooted' : ''), 'tf'], ['3 · weight against other topics', 'idf'], ['c-TF-IDF', 'score'], ['moved', '']]) {
    const cell = node('span', label, 'walk-cell'); if (step) { cell.dataset.step = step; cell.addEventListener('mouseenter', () => highlightFormula(step)); cell.addEventListener('mouseleave', () => highlightFormula('')); }
    header.append(cell);
  }
  table.append(header);
  const bar = (value, max, cls, text) => { const wrap = node('span', undefined, 'walk-bar'); const fill = node('span', undefined, `walk-fill ${cls}`); fill.style.width = `${(100 * value) / (max || 1)}%`; wrap.append(fill, node('small', text)); return wrap; };
  terms.forEach((t, rank) => {
    const row = node('div', undefined, `walk-row${rank < 10 ? ' is-keyword' : ''}`); row.setAttribute('role', 'row');
    const name = node('span', undefined, 'walk-cell walk-term'); name.append(node('code', t.term)); if (rank < 10) name.append(node('small', `#${rank + 1}`));
    row.append(name);
    row.append(bar(t.tf, maxTf, 'f-count', `${number(t.tf)} · rank ${t.rank_count}`));
    row.append(bar(t.tfp, maxTfp, 'f-tf', `${t.tfp.toFixed(3)} of ${number(walk.words)} words`));
    row.append(bar(t.idf, maxIdf, 'f-idf', `${t.idf.toFixed(2)} · ${number(t.f_t)} everywhere`));
    row.append(bar(t.score, maxScore, 'f-score', t.score.toFixed(3)));
    const moved = t.rank_count - (rank + 1);
    const arrow = node('span', moved > 0 ? `↑ ${moved}` : moved < 0 ? `↓ ${-moved}` : '—', `walk-cell walk-move ${moved > 0 ? 'up' : moved < 0 ? 'down' : ''}`);
    arrow.title = `rank ${t.rank_count} by count → rank ${rank + 1} by weight`; row.append(arrow);
    table.append(row);
  });
  const record = topicRecord(top.walkTopic);
  const generic = terms.filter((t) => t.rank_count <= 3 && t.rank_weight > 20).map((t) => t.term);
  $('walk-note').textContent = `${topicLabel(top.walkTopic)} has ${number(walk.words)} words after stop words; the average topic has A = ${number(top.data.A)}. `
    + (generic.length ? `${generic.map((g) => `“${g}”`).join(', ')} lead by count but fall out of the keywords because they are common everywhere. ` : '')
    + (useSqrt ? '' : 'Without the square root, repeated words dominate: that is what reduce_frequent_words prevents. ')
    + (record ? `Keywords: ${shortKeywords(record, 5)}.` : '');
  $('formula-note').textContent = `A = ${number(top.data.A)} · words in this topic = ${number(walk.words)}`;
}
function highlightFormula(step) {
  for (const part of document.querySelectorAll('.formula-part')) part.classList.toggle('is-lit', part.dataset.step === step || (step === 'score' && part.dataset.step !== 'count'));
}

function topicMembers(id) {
  const rows = emb.data.rows, probs = clu.data ? clu.data.probabilities : null;
  const members = [];
  top.data.topic_of_row.forEach((t, i) => { if (t === id) members.push({ i, text: rows[i].text, platform: rows[i].platform, probability: probs && id >= 0 ? probs[i] : null }); });
  members.sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0) || a.i - b.i);
  return members;
}

function renderTopicList() {
  const list = $('topic-list'); list.replaceChildren();
  const query = $('topic-search').value.trim().toLowerCase();
  const total = top.data.counts.rows;
  const ordered = [...top.data.topics.filter((t) => t.topic >= 0), ...top.data.topics.filter((t) => t.topic < 0)];
  let shown = 0;
  for (const t of ordered) {
    if (query && !t.keywords.some((k) => k.term.includes(query))) continue;
    shown += 1;
    const item = node('li');
    const button = node('button', undefined, 'topic-pick'); button.type = 'button'; button.setAttribute('aria-pressed', String(top.selected === t.topic));
    const head = node('span', undefined, 'topic-pick-head');
    head.append(node('strong', topicLabel(t.topic)), node('span', `${number(t.count)} · ${percent(t.count / total, 0)}`, 'topic-count'));
    const words = node('span', t.topic < 0 ? 'reviews in no dense group' : shortKeywords(t), 'topic-words');
    if (query && t.topic >= 0) { // show the matching keywords first, then fill up to four
      words.replaceChildren();
      const shown = [...t.keywords.filter((k) => k.term.includes(query)), ...t.keywords.filter((k) => !k.term.includes(query))].slice(0, 4);
      shown.forEach((k, i) => { words.append(node('span', k.term, k.term.includes(query) ? 'hit' : '')); if (i < shown.length - 1) words.append(document.createTextNode(' · ')); });
    }
    button.append(head, words);
    button.addEventListener('click', () => selectTopic(t.topic));
    item.append(button); list.append(item);
  }
  if (!shown) list.append(node('li', 'No topic has a keyword containing that text.', 'subtle'));
}

function renderTopicPanel() {
  const panel = $('topic-panel');
  if (top.selected === null) { panel.hidden = true; return; }
  panel.hidden = false;
  const id = top.selected, record = topicRecord(id), members = topicMembers(id);
  $('topic-panel-eyebrow').textContent = id < 0 ? 'Not in any topic' : `Topic ${id} · from group ${record.cluster}`;
  $('topic-panel-title').textContent = id < 0 ? 'Unassigned reviews' : shortKeywords(record, 3);
  const platforms = Object.entries(members.reduce((acc, m) => { acc[m.platform] = (acc[m.platform] || 0) + 1; return acc; }, {})).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${meta(k).label} ${v}`).join(', ');
  $('topic-panel-meta').textContent = `${number(members.length)} reviews · ${percent(members.length / top.data.counts.rows, 1)} of the sample · ${platforms}`;
  const kw = $('topic-keywords'); kw.replaceChildren();
  const maxW = Math.max(...record.keywords.map((k) => k.weight), 1e-9);
  for (const k of record.keywords) {
    const item = node('li'); const track = node('span', undefined, 'walk-bar'); const fill = node('span', undefined, 'walk-fill f-score'); fill.style.width = `${(100 * k.weight) / maxW}%`; track.append(fill);
    item.append(node('code', k.term), track, node('small', k.weight.toFixed(3))); kw.append(item);
  }
  const rep = $('topic-representative'); rep.replaceChildren();
  for (const i of record.representative) { const li = node('li'); const q = node('blockquote', emb.data.rows[i].text, 'io-text'); q.setAttribute('lang', 'es'); li.append(q); rep.append(li); }
  if (!record.representative.length) rep.append(node('li', 'none recorded', 'subtle'));
  const query = $('topic-review-search').value.trim().toLowerCase();
  const filtered = query ? members.filter((m) => m.text.toLowerCase().includes(query)) : members;
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  top.page = Math.min(top.page, pages - 1);
  const list = $('topic-reviews'); list.replaceChildren();
  for (const m of filtered.slice(top.page * PAGE_SIZE, (top.page + 1) * PAGE_SIZE)) {
    const item = node('li', undefined, 'group-review');
    const head = node('div', undefined, 'neighbor-head'); const tag = node('span', undefined, 'platform-tag'); platformTag(tag, m.platform);
    head.append(tag, node('code', emb.data.rows[m.i].record_id)); if (m.probability !== null) head.append(node('span', `strength ${m.probability.toFixed(2)}`, 'strength'));
    const text = node('blockquote', m.text, 'io-text'); text.setAttribute('lang', 'es');
    item.append(head, text); list.append(item);
  }
  if (!filtered.length) list.append(node('li', 'No review in this topic matches the search.', 'subtle'));
  $('topic-page').textContent = `${number(filtered.length)} review${filtered.length === 1 ? '' : 's'} · page ${top.page + 1} of ${pages}`;
  $('topic-prev').disabled = top.page === 0; $('topic-next').disabled = top.page >= pages - 1;
}

function selectTopic(id) {
  top.selected = id; top.page = 0; $('topic-review-search').value = '';
  renderTopicList(); renderTopicPanel();
  $('topic-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function tryRenderTopics() {
  if (!top.data || !emb.data) return;
  $('page-topics').setAttribute('aria-busy', 'true');
  try {
    const { counts, params } = top.data;
    $('topics-lede').textContent = `${number(counts.topics)} topics, one per group, each described by ten keywords weighted by class-based TF-IDF over ${number(params.vocabulary)} words and bigrams. `
      + `Topic 0 reads “${shortKeywords(topicRecord(0), 3)}”.`;
    $('stop-word-count').textContent = String(params.stop_words.length);
    const select = $('walk-topic'); select.replaceChildren();
    for (const t of top.data.topics) { const o = node('option', `${topicLabel(t.topic)} · ${number(t.count)} · ${t.topic < 0 ? 'unassigned' : shortKeywords(t, 2)}`); o.value = String(t.topic); select.append(o); }
    select.value = String(top.walkTopic);
    renderWalk();
    renderTopicList();
    renderTopicPanel();
    $('topics-status').hidden = true;
    $('topics-report').hidden = false;
  } catch (error) {
    showTopicsError(`The topics page could not render: ${error.message}`);
    throw error;
  } finally {
    $('page-topics').setAttribute('aria-busy', 'false');
  }
}

$('walk-topic').addEventListener('change', (event) => { top.walkTopic = Number(event.target.value); renderWalk(); });
$('walk-sqrt').addEventListener('change', renderWalk);
$('topic-search').addEventListener('input', renderTopicList);
$('topic-review-search').addEventListener('input', () => { top.page = 0; renderTopicPanel(); });
$('topic-prev').addEventListener('click', () => { top.page -= 1; renderTopicPanel(); });
$('topic-next').addEventListener('click', () => { top.page += 1; renderTopicPanel(); });
$('open-vectorizer').addEventListener('click', () => {
  const d = $('vectorizer-dialog');
  const id = top.selected === null ? top.walkTopic : top.selected;
  const record = topicRecord(id);
  const texts = (record && record.representative.length ? record.representative : topicMembers(id).slice(0, 5).map((m) => m.i)).map((i) => emb.data.rows[i].text);
  window.VOCTopicDemos?.mountVectorizer({ texts, stopWords: top.data.params.stop_words, note: `Reviews from ${topicLabel(id).toLowerCase()}.` });
  if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); d.querySelector('.dialog-close')?.focus();
});
$('open-ctfidf').addEventListener('click', () => { const d = $('ctfidf-dialog'); window.VOCTopicDemos?.mountCtfidf(); if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); d.querySelector('.dialog-close')?.focus(); });

const topicHost = $('topic-payload');
if (topicHost) {
  const stopTopics = observeMarimoValue(topicHost, {
    onValue: (value) => { top.data = value; tryRenderTopics(); tryRenderNaming(); tryRenderSentiment(); tryRenderDashboard(); },
    onError: (error) => showTopicsError(error.message || `Projection ${error.selector || 'topic_payload'} is unavailable. Run the topic cells in the notebook.`),
  });
  window.addEventListener('pagehide', stopTopics, { once: true });
}

// ---------------------------------------------------------------------- Naming page
// Needs `naming_payload` (every exchange with the model) and `embedding_payload` (texts for the evidence rows).
const ASSESSMENT = {
  single_feature: ['Single feature', 'one user-visible capability'],
  mixed: ['Mixed', 'several capabilities in the evidence'],
  unclear: ['Unclear', 'not enough to name a capability'],
};
function showNamingError(message) {
  $('naming-report').hidden = true;
  $('naming-status').hidden = false;
  $('naming-status').textContent = message;
  $('naming-lede').textContent = 'No labels are loaded.';
  $('page-naming').setAttribute('aria-busy', 'false');
}
const namingLabel = (id) => nam.data.labels.find((l) => l.topic === id);
const headline = (l) => (l.label ? l.label : (l.description.split(/(?<=[.!?])\s/)[0] || l.description));
const assessmentBadge = (key) => { const b = node('span', ASSESSMENT[key]?.[0] || key, `assessment assessment-${key}`); b.title = ASSESSMENT[key]?.[1] || ''; return b; };

function renderAnatomy() {
  const l = namingLabel(nam.topic); if (!l) return;
  const rows = emb.data.rows;
  $('anatomy-aspect').textContent = nam.data.label_aspect.replace('mmr_', 'KeyBERT → MMR ');
  const kw = $('anatomy-keywords'); kw.replaceChildren();
  for (const w of l.keywords) kw.append(node('span', w, 'chip'));
  $('anatomy-evidence-note').textContent = `${l.evidence.length} reviews, representative first, at most ${nam.data.evidence_rules.chars} characters each; cited ones are marked.`;
  const ev = $('anatomy-evidence'); ev.replaceChildren();
  l.evidence.forEach((rowIndex, n) => {
    const cited = l.supporting.includes(rowIndex);
    const item = node('li', undefined, `evidence-item${cited ? ' is-cited' : ''}`);
    const head = node('div', undefined, 'neighbor-head'); head.append(node('code', `e${n + 1}`), node('span', meta(rows[rowIndex].platform).label));
    if (cited) head.append(node('span', 'cited', 'cited-tag'));
    const text = node('blockquote', rows[rowIndex].text.slice(0, nam.data.evidence_rules.chars), 'io-text'); text.setAttribute('lang', 'es');
    item.append(head, text); ev.append(item);
  });
  $('anatomy-request').textContent = l.user_message;
  $('anatomy-model').textContent = l.returned_model || nam.data.model;
  $('anatomy-params').textContent = `temperature 0 · JSON schema enforced · prompt ${nam.data.prompt_version}${l.tokens ? ` · ${number(l.tokens)} tokens` : ''}`;
  $('anatomy-system').textContent = nam.data.system_prompt;
  const badgeHost = $('anatomy-assessment'); badgeHost.replaceChildren(assessmentBadge(l.assessment));
  $('anatomy-meta').textContent = `${ASSESSMENT[l.assessment]?.[1] || ''} · ${day(l.created_at)}`;
  $('anatomy-feature').textContent = l.label || 'No single feature proposed';
  $('anatomy-description').textContent = l.description;
  $('anatomy-cited').textContent = `${l.supporting.length} of ${l.evidence.length} evidence reviews cited in support.`;
  $('anatomy-response').textContent = l.raw_response || '';
}

function renderNamingList() {
  const list = $('naming-list'); list.replaceChildren();
  const total = nam.data.labels.reduce((s, l) => s + (topicCount(l.topic) || 0), 0);
  for (const l of nam.data.labels) {
    if (nam.filter !== 'all' && l.assessment !== nam.filter) continue;
    const item = node('li');
    const button = node('button', undefined, 'naming-pick'); button.type = 'button'; button.setAttribute('aria-pressed', String(nam.topic === l.topic));
    const head = node('div', undefined, 'naming-pick-head');
    head.append(node('strong', `Topic ${l.topic}`), assessmentBadge(l.assessment), node('span', `${number(topicCount(l.topic))} reviews`, 'topic-count'));
    const title = node('span', headline(l), l.label ? 'naming-title' : 'naming-title is-description'); title.setAttribute('lang', 'es');
    const words = node('span', l.keywords.slice(0, 4).join(' · '), 'topic-words');
    button.append(head, title, words);
    button.addEventListener('click', () => { nam.topic = l.topic; $('naming-topic').value = String(l.topic); renderAnatomy(); renderNamingList(); $('anatomy-title').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    item.append(button); list.append(item);
  }
  if (!list.children.length) list.append(node('li', 'No topic has this assessment.', 'subtle'));
}
const topicCount = (id) => (top.data ? (top.data.topics.find((t) => t.topic === id)?.count ?? 0) : 0);

function tryRenderNaming() {
  if (!nam.data || !emb.data || !top.data) return;
  $('page-naming').setAttribute('aria-busy', 'true');
  try {
    const { counts, labels } = nam.data;
    const byAssessment = labels.reduce((acc, l) => { acc[l.assessment] = (acc[l.assessment] || 0) + 1; return acc; }, {});
    $('naming-lede').textContent = `${number(counts.named)} of ${number(counts.topics)} topics named by ${nam.data.model}, one call each, answers cached. `
      + `${number(byAssessment.single_feature || 0)} point at a single feature, ${number(byAssessment.mixed || 0)} are mixed, ${number(byAssessment.unclear || 0)} unclear by the model's own assessment.`;
    const tiles = $('naming-tiles'); tiles.replaceChildren();
    for (const [value, label, context] of [[number(counts.named), 'topics named', `of ${number(counts.topics)}, ${counts.failed} failed`], [number(byAssessment.single_feature || 0), 'single feature', 'a label was proposed'], [number(byAssessment.mixed || 0), 'mixed', 'several capabilities'], [number(byAssessment.unclear || 0), 'unclear', 'mostly short praise']]) {
      const tile = node('div', undefined, 'stat'); tile.append(node('strong', value), node('span', label), node('small', context)); tiles.append(tile);
    }
    const select = $('naming-topic'); select.replaceChildren();
    for (const l of labels) { const o = node('option', `Topic ${l.topic} · ${headline(l).slice(0, 48)}`); o.value = String(l.topic); select.append(o); }
    if (!namingLabel(nam.topic)) nam.topic = labels[0].topic;
    select.value = String(nam.topic);
    renderAnatomy(); renderNamingList();
    $('naming-status').hidden = true;
    $('naming-report').hidden = false;
  } catch (error) {
    showNamingError(`The naming page could not render: ${error.message}`);
    throw error;
  } finally {
    $('page-naming').setAttribute('aria-busy', 'false');
  }
}

$('naming-topic').addEventListener('change', (event) => { nam.topic = Number(event.target.value); renderAnatomy(); renderNamingList(); });
for (const button of document.querySelectorAll('#page-naming .map-toggle button[data-filter]')) {
  button.addEventListener('click', () => {
    nam.filter = button.dataset.filter;
    for (const b of document.querySelectorAll('#page-naming .map-toggle button[data-filter]')) { const active = b === button; b.classList.toggle('is-active', active); b.setAttribute('aria-pressed', String(active)); }
    renderNamingList();
  });
}
$('open-keybert').addEventListener('click', () => { const d = $('keybert-dialog'); window.VOCNamingDemos?.mountKeyBERT(nam.data); window.VOCNamingDemos?.setTopic(nam.topic); window.VOCNamingDemos?.mountKeyBERT(nam.data); if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); d.querySelector('.dialog-close')?.focus(); });
$('open-mmr').addEventListener('click', () => { const d = $('mmr-dialog'); window.VOCNamingDemos?.mountMMR(nam.data); window.VOCNamingDemos?.setTopic(nam.topic); window.VOCNamingDemos?.mountMMR(nam.data); if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); d.querySelector('.dialog-close')?.focus(); });

const namingHost = $('naming-payload');
if (namingHost) {
  const stopNaming = observeMarimoValue(namingHost, {
    onValue: (value) => { nam.data = value; tryRenderNaming(); tryRenderDashboard(); },
    onError: (error) => showNamingError(error.message || `Projection ${error.selector || 'naming_payload'} is unavailable. Run the naming cells in the notebook.`),
  });
  window.addEventListener('pagehide', stopNaming, { once: true });
}

// ------------------------------------------------------------------- Sentiment page
// Needs `sentiment_payload` (label and probabilities per row, per-topic mix, rating check), `embedding_payload`
// (texts) and `topic_payload` (keywords for topics without a feature).
const SENT = ['negative', 'neutral', 'positive'];
const SENT_CLASS = { negative: 'neg', neutral: 'neu', positive: 'pos' };
function showSentimentError(message) {
  $('sentiment-report').hidden = true;
  $('sentiment-status').hidden = false;
  $('sentiment-status').textContent = message;
  $('sentiment-lede').textContent = 'No sentiment is loaded.';
  $('page-sentiment').setAttribute('aria-busy', 'false');
}
const topicTitle = (t) => {
  if (t.topic < 0) return 'Unassigned reviews';
  if (t.feature) return t.feature;
  const record = top.data ? top.data.topics.find((x) => x.topic === t.topic) : null;
  return record ? shortKeywords(record, 3) : `Topic ${t.topic}`;
};
function sortedTopics() {
  const topics = [...sen.data.topics];
  const share = (t, k) => (t.reviews ? t[k] / t.reviews : 0);
  if (sen.sort === 'size') topics.sort((a, b) => b.reviews - a.reviews);
  else if (sen.sort === 'positive') topics.sort((a, b) => share(b, 'positive') - share(a, 'positive') || b.reviews - a.reviews);
  else topics.sort((a, b) => share(b, 'negative') - share(a, 'negative') || b.reviews - a.reviews);
  return topics;
}
function renderSentimentBars() {
  const list = $('sentiment-bars'); list.replaceChildren();
  const topics = sortedTopics();
  const max = Math.max(...topics.map((t) => t.reviews), 1);
  for (const t of topics) {
    const item = node('li');
    const button = node('button', undefined, 'sentiment-row'); button.type = 'button'; button.setAttribute('aria-pressed', String(sen.topic === t.topic));
    const head = node('span', undefined, 'sentiment-row-head');
    head.append(node('strong', t.topic < 0 ? 'Unassigned' : `Topic ${t.topic}`), node('span', topicTitle(t), `sentiment-row-title${t.feature ? '' : ' is-keywords'}`));
    const track = node('span', undefined, 'sentiment-track');
    const bar = node('span', undefined, 'sentiment-bar'); bar.style.width = `${(100 * t.reviews) / max}%`;
    for (const k of SENT) { const seg = node('i', undefined, SENT_CLASS[k]); seg.style.width = `${t.reviews ? (100 * t[k]) / t.reviews : 0}%`; bar.append(seg); }
    track.append(bar);
    const count = node('span', undefined, 'sentiment-row-count');
    count.append(node('strong', number(t.reviews)), node('small', `${percent(t.reviews ? t.negative / t.reviews : 0, 0)} negative`));
    button.setAttribute('aria-label', `${topicTitle(t)}: ${t.reviews} reviews, ${t.negative} negative, ${t.neutral} neutral, ${t.positive} positive`);
    attachTooltip(button, () => [topicTitle(t), SENT.map((k) => [k, `${number(t[k])} · ${percent(t.reviews ? t[k] / t.reviews : 0, 0)}`])]);
    button.append(head, track, count);
    button.addEventListener('click', () => selectSentimentTopic(t.topic));
    item.append(button); list.append(item);
  }
}
function sentimentMembers(id) {
  const rows = emb.data.rows, labels = sen.data.label_of_row, probs = sen.data.probabilities, topicOf = top.data.topic_of_row;
  const members = [];
  topicOf.forEach((t, i) => { if (t === id) members.push({ i, text: rows[i].text, platform: rows[i].platform, label: labels[i], probs: probs[i], confidence: Math.max(...probs[i]), rating: sen.data.rating_of_row[i] }); });
  members.sort((a, b) => SENT.indexOf(a.label) - SENT.indexOf(b.label) || b.confidence - a.confidence);
  return members;
}
function renderSentimentPanel() {
  const panel = $('sentiment-panel');
  if (sen.topic === null) { panel.hidden = true; return; }
  panel.hidden = false;
  const t = sen.data.topics.find((x) => x.topic === sen.topic);
  const members = sentimentMembers(sen.topic);
  $('sentiment-panel-eyebrow').textContent = t.topic < 0 ? 'Not in any topic' : `Topic ${t.topic}${t.assessment ? ` · ${ASSESSMENT[t.assessment]?.[0] || t.assessment}` : ''}`;
  $('sentiment-panel-title').textContent = topicTitle(t);
  $('sentiment-panel-meta').textContent = `${number(t.reviews)} reviews · ` + SENT.map((k) => `${k} ${number(t[k])} (${percent(t.reviews ? t[k] / t.reviews : 0, 0)})`).join(' · ') + '. Sorted by label, most confident first.';
  const query = $('sentiment-search').value.trim().toLowerCase();
  const filtered = members.filter((m) => (sen.filter === 'all' || m.label === sen.filter) && (!query || m.text.toLowerCase().includes(query)));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  sen.page = Math.min(sen.page, pages - 1);
  const list = $('sentiment-reviews'); list.replaceChildren();
  for (const m of filtered.slice(sen.page * PAGE_SIZE, (sen.page + 1) * PAGE_SIZE)) {
    const item = node('li', undefined, 'group-review');
    const head = node('div', undefined, 'neighbor-head');
    const tag = node('span', undefined, 'platform-tag'); platformTag(tag, m.platform);
    head.append(tag, node('span', m.label, `sent-badge ${SENT_CLASS[m.label]}`));
    if (m.rating) head.append(node('span', `${m.rating} ★`, 'rating-tag'));
    const mini = node('span', undefined, 'prob-mini'); mini.title = SENT.map((k, i) => `${k} ${(100 * m.probs[i]).toFixed(0)}%`).join(' · ');
    SENT.forEach((k, i) => { const seg = node('i', undefined, SENT_CLASS[k]); seg.style.width = `${100 * m.probs[i]}%`; mini.append(seg); });
    head.append(mini, node('span', `${(100 * m.confidence).toFixed(0)}%`, 'strength'));
    const text = node('blockquote', m.text, 'io-text'); text.setAttribute('lang', 'es');
    item.append(head, text); list.append(item);
  }
  if (!filtered.length) list.append(node('li', 'No review matches.', 'subtle'));
  $('sentiment-page').textContent = `${number(filtered.length)} review${filtered.length === 1 ? '' : 's'} · page ${sen.page + 1} of ${pages}`;
  $('sentiment-prev').disabled = sen.page === 0; $('sentiment-next').disabled = sen.page >= pages - 1;
}
function selectSentimentTopic(id) {
  sen.topic = id; sen.page = 0; sen.filter = 'all'; $('sentiment-search').value = '';
  for (const b of document.querySelectorAll('#sentiment-panel .map-toggle button[data-label]')) { const active = b.dataset.label === 'all'; b.classList.toggle('is-active', active); b.setAttribute('aria-pressed', String(active)); }
  renderSentimentBars(); renderSentimentPanel();
  $('sentiment-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function renderRatingCheck() {
  const { rows, rated } = sen.data.rating_check;
  const host = $('rating-chart'); host.replaceChildren();
  const W = 420, H = 260, pad = { l: 44, r: 10, t: 10, b: 30 };
  const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'rating-svg' });
  const colW = (W - pad.l - pad.r) / rows.length;
  rows.forEach((r, i) => {
    const total = SENT.reduce((s, k) => s + r[k], 0) || 1;
    let y = pad.t;
    for (const k of SENT) {
      const h = ((H - pad.t - pad.b) * r[k]) / total;
      const rect = svg('rect', { x: (pad.l + i * colW + 6).toFixed(1), y: y.toFixed(1), width: (colW - 12).toFixed(1), height: Math.max(0, h - 2).toFixed(1), class: `rating-seg ${SENT_CLASS[k]}` });
      const title = svg('title'); title.textContent = `${r.rating} ★ · ${k}: ${number(r[k])} (${percent(r[k] / total, 0)})`; rect.append(title);
      chart.append(rect);
      if (h > 18) { const label = svg('text', { x: (pad.l + i * colW + colW / 2).toFixed(1), y: (y + h / 2 + 4).toFixed(1), class: 'rating-label', 'text-anchor': 'middle' }); label.textContent = percent(r[k] / total, 0); chart.append(label); }
      y += h;
    }
    const axis = svg('text', { x: (pad.l + i * colW + colW / 2).toFixed(1), y: H - 10, class: 'rating-axis', 'text-anchor': 'middle' }); axis.textContent = `${r.rating} ★ (${number(total)})`; chart.append(axis);
  });
  const yl = svg('text', { x: 10, y: pad.t + 12, class: 'rating-axis' }); yl.textContent = 'share'; chart.append(yl);
  host.append(chart);
  const body = $('rating-table').querySelector('tbody'); body.replaceChildren();
  for (const r of rows) { const tr = node('tr'); tr.append(node('td', `${r.rating} ★`)); for (const k of SENT) tr.append(node('td', number(r[k]), 'num')); body.append(tr); }
  const expected = (rating) => (rating <= 2 ? 'negative' : rating === 3 ? 'neutral' : 'positive');
  const agree = rows.reduce((s, r) => s + r[expected(r.rating)], 0);
  const polar = rows.filter((r) => r.rating !== 3).reduce((acc, r) => { const want = r.rating <= 2 ? 'negative' : 'positive'; acc.ok += r[want]; acc.n += r.negative + r.positive; return acc; }, { ok: 0, n: 0 });
  $('rating-note').textContent = `${number(rated)} store reviews carry a star rating. Mapping 1–2 ★ to negative, 3 ★ to neutral and 4–5 ★ to positive, the classifier agrees on ${percent(agree / rated, 0)}; `
    + `ignoring the neutral middle on both sides, polarity agrees on ${percent(polar.ok / Math.max(1, polar.n), 0)}.`;
  const fiveNeutral = sen.data.rating_of_row.map((r, i) => (r === 5 && sen.data.label_of_row[i] === 'neutral' ? i : -1)).filter((i) => i >= 0);
  const lengths = fiveNeutral.map((i) => emb.data.rows[i].text.length).sort((a, b) => a - b);
  const median = lengths.length ? lengths[Math.floor(lengths.length / 2)] : 0;
  $('rating-disagreement').textContent = `The main disagreement: ${number(fiveNeutral.length)} five-star reviews predicted neutral, median ${median} characters. The classifier, trained on tweets, keeps very short praise in neutral unless it carries an explicit cue. Examples:`;
  const ul = $('rating-examples'); ul.replaceChildren();
  for (const i of fiveNeutral.slice(0, 6)) { const li = node('li'); const q = node('span', emb.data.rows[i].text.slice(0, 60), 'io-text'); q.setAttribute('lang', 'es'); li.append(q, node('small', ` ${(100 * sen.data.probabilities[i][1]).toFixed(0)}% neutral · ${(100 * sen.data.probabilities[i][2]).toFixed(0)}% positive`)); ul.append(li); }
}
function tryRenderSentiment() {
  if (!sen.data || !emb.data || !top.data) return;
  $('page-sentiment').setAttribute('aria-busy', 'true');
  try {
    const { counts } = sen.data;
    $('sentiment-lede').textContent = `${number(counts.classified)} reviews classified: ${percent(counts.negative / counts.rows, 0)} negative, ${percent(counts.neutral / counts.rows, 0)} neutral, ${percent(counts.positive / counts.rows, 0)} positive. `
      + `The most negative large topic is ${topicTitle(sortedTopics().find((t) => t.reviews >= 30 && t.topic >= 0) || sen.data.topics[0])}.`;
    const tiles = $('sentiment-tiles'); tiles.replaceChildren();
    for (const [value, label, context] of [[number(counts.classified), 'reviews classified', `${counts.truncated} truncated at ${sen.data.model.max_length} tokens`], ...SENT.map((k) => [percent(counts[k] / counts.rows, 0), k, `${number(counts[k])} reviews`])]) {
      const tile = node('div', undefined, 'stat'); tile.append(node('strong', value), node('span', label), node('small', context)); tiles.append(tile);
    }
    renderSentimentBars(); renderSentimentPanel(); renderRatingCheck();
    $('sentiment-status').hidden = true;
    $('sentiment-report').hidden = false;
  } catch (error) {
    showSentimentError(`The sentiment page could not render: ${error.message}`);
    throw error;
  } finally {
    $('page-sentiment').setAttribute('aria-busy', 'false');
  }
}
for (const button of document.querySelectorAll('#page-sentiment .map-toggle button[data-sort]')) {
  button.addEventListener('click', () => { sen.sort = button.dataset.sort; for (const b of document.querySelectorAll('#page-sentiment .map-toggle button[data-sort]')) { const active = b === button; b.classList.toggle('is-active', active); b.setAttribute('aria-pressed', String(active)); } renderSentimentBars(); });
}
for (const button of document.querySelectorAll('#sentiment-panel .map-toggle button[data-label]')) {
  button.addEventListener('click', () => { sen.filter = button.dataset.label; sen.page = 0; for (const b of document.querySelectorAll('#sentiment-panel .map-toggle button[data-label]')) { const active = b === button; b.classList.toggle('is-active', active); b.setAttribute('aria-pressed', String(active)); } renderSentimentPanel(); });
}
$('sentiment-search').addEventListener('input', () => { sen.page = 0; renderSentimentPanel(); });
$('sentiment-prev').addEventListener('click', () => { sen.page -= 1; renderSentimentPanel(); });
$('sentiment-next').addEventListener('click', () => { sen.page += 1; renderSentimentPanel(); });
$('open-classifier').addEventListener('click', () => {
  const d = $('classifier-dialog');
  const id = sen.topic === null ? (sortedTopics().find((t) => t.topic >= 0) || sen.data.topics[0]).topic : sen.topic;
  const members = sentimentMembers(id).sort((a, b) => b.confidence - a.confidence);
  const picks = [...SENT.map((k) => members.find((m) => m.label === k)).filter(Boolean), ...members.filter((m) => m.rating === 5 && m.label === 'neutral').slice(0, 2)];
  window.VOCSentimentDemos?.mountClassifier({ reviews: picks.map((m) => ({ text: m.text, probs: m.probs, rating: m.rating, context: `${id < 0 ? 'unassigned' : `topic ${id}`} · predicted ${m.label}` })) });
  if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); d.querySelector('.dialog-close')?.focus();
});
const sentimentHost = $('sentiment-payload');
if (sentimentHost) {
  const stopSentiment = observeMarimoValue(sentimentHost, {
    onValue: (value) => { sen.data = value; tryRenderSentiment(); tryRenderDashboard(); },
    onError: (error) => showSentimentError(error.message || `Projection ${error.selector || 'sentiment_payload'} is unavailable. Run the sentiment cells in the notebook.`),
  });
  window.addEventListener('pagehide', stopSentiment, { once: true });
}

// ------------------------------------------------------------------ Dashboard page
// Joins every stage by row index (the prototype's record id) and hands one table to dashboard.js.
function showDashboardError(message) {
  $('dashboard-report').hidden = true;
  $('dashboard-status').hidden = false;
  $('dashboard-status').textContent = message;
  $('page-dashboard').setAttribute('aria-busy', 'false');
}
function tryRenderDashboard() {
  if (!dsh.data || !emb.data || !top.data || !nam.data || !sen.data || !window.VOCDashboard) return;
  $('page-dashboard').setAttribute('aria-busy', 'true');
  try {
    const labelOf = new Map(nam.data.labels.map((l) => [l.topic, l]));
    const topics = top.data.topics.map((t) => {
      const l = labelOf.get(t.topic);
      const keywords = l ? l.keywords.slice(0, 6) : t.keywords.slice(0, 6).map((k) => k.term);
      return {
        topic: t.topic, count: t.count, keywords,
        feature: l?.label || null, assessment: t.topic < 0 ? 'none' : (l?.assessment || 'unclear'), description: l?.description || null,
        title: t.topic < 0 ? 'Unassigned reviews' : (l?.label || keywords.slice(0, 3).join(' · ')),
      };
    });
    const assessmentOf = new Map(topics.map((t) => [t.topic, t.assessment]));
    const rows = emb.data.rows.map((r, i) => ({
      i, text: r.text, platform: r.platform, topic: top.data.topic_of_row[i], assessment: assessmentOf.get(top.data.topic_of_row[i]) || 'none',
      sentiment: sen.data.label_of_row[i], confidence: Math.max(...sen.data.probabilities[i]), rating: sen.data.rating_of_row[i], month: dsh.data.month_of_row[i],
    }));
    const platforms = orderedKeys([...new Set(rows.map((r) => r.platform))]);
    const months = [...new Set(rows.map((r) => r.month))].sort();
    window.VOCDashboard.mount({ rows, topics, platforms, months, store: dsh.data.store, release: dsh.data.release });
    $('dashboard-status').hidden = true;
    $('dashboard-report').hidden = false;
  } catch (error) {
    showDashboardError(`The dashboard could not render: ${error.message}`);
    throw error;
  } finally {
    $('page-dashboard').setAttribute('aria-busy', 'false');
  }
}
$('dash-open-store').addEventListener('click', () => { const d = $('store-dialog'); if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); d.querySelector('.dialog-close')?.focus(); });
const dashboardHost = $('dashboard-payload');
if (dashboardHost) {
  const stopDashboard = observeMarimoValue(dashboardHost, {
    onValue: (value) => { dsh.data = value; tryRenderDashboard(); },
    onError: (error) => showDashboardError(error.message || `Projection ${error.selector || 'dashboard_payload'} is unavailable. Run the dashboard cell in the notebook.`),
  });
  window.addEventListener('pagehide', stopDashboard, { once: true });
}

// Page navigation: one section per page, addressed by the URL hash (#/overview, #/dataset).
// Arrow keys move between pages; the tab list and counter reflect the current page.
const pages = [...document.querySelectorAll('#deck .page')].map((section) => section.dataset.page);
const pageFromHash = () => {
  const name = window.location.hash.replace(/^#\/?/, '').split('/')[0];
  return pages.includes(name) ? name : pages[0];
};
function showPage(name) {
  const index = pages.indexOf(name);
  for (const section of document.querySelectorAll('#deck .page')) section.hidden = section.dataset.page !== name;
  for (const tab of document.querySelectorAll('#page-tabs a')) {
    const current = tab.dataset.page === name;
    if (current) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');
  }
  $('page-counter').textContent = `${index + 1} / ${pages.length}`;
  $('prev-page').disabled = index === 0;
  $('next-page').disabled = index === pages.length - 1;
  hideTooltip();
  window.scrollTo({ top: 0, behavior: 'auto' });
}
function goTo(offset) {
  const next = pages[pages.indexOf(pageFromHash()) + offset];
  if (!next) return;
  window.location.hash = `#/${next}`;
  showPage(next); // do not wait for the asynchronous hashchange event
}
$('prev-page').addEventListener('click', () => goTo(-1));
$('next-page').addEventListener('click', () => goTo(1));
window.addEventListener('keydown', (event) => {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  const target = event.target instanceof Element ? event.target : null;
  if (target && target.closest('input, select, textarea, button, [contenteditable], marimo-cell')) return;
  const openDialog = document.querySelector('dialog[open]');
  const stepsReviews = openDialog && openDialog.id === 'neighbors-dialog';
  if (openDialog && !stepsReviews) return; // other panels keep the keys for their own controls
  if (event.key === 'ArrowRight' || event.key === 'PageDown') { if (stepsReviews) showReview(emb.index + 1); else goTo(1); event.preventDefault(); }
  if (event.key === 'ArrowLeft' || event.key === 'PageUp') { if (stepsReviews) showReview(emb.index - 1); else goTo(-1); event.preventDefault(); }
});
window.addEventListener('hashchange', () => showPage(pageFromHash()));
showPage(pageFromHash());

const host = $('payload');
if (host) {
  const stop = observeMarimoValue(host, {
    onValue: (value) => { try { render(value); } catch (error) { showError(`The overview could not render: ${error.message}`); throw error; } },
    onError: (error) => showError(error.message || `Projection ${error.selector || 'studio_payload'} is unavailable.`),
  });
  window.addEventListener('pagehide', stop, { once: true });
}
