// Published stores-v3 evidence comes only from the notebook's transfer_learning_payload.
const host = document.getElementById('transfer-learning-payload');
const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => { const item = document.createElement(tag); if (text !== undefined) item.textContent = String(text); if (cls) item.className = cls; return item; };
const count = (value) => Number(value).toLocaleString('en-US');
const pct = (value) => `${(value * 100).toFixed(2)}%`;
let report;
let metricKey = 'macro_f1';

function drawMetric() {
  const metric = report.comparison.find((item) => item.key === metricKey);
  const bars = $('transfer-bars'); bars.replaceChildren();
  for (const [name, value, cls] of [['Original RoBERTuito', metric.original, 'original'], ['Stores v3', metric.v3, 'adapted']]) {
    const row = el('div', undefined, 'transfer-bar-row');
    const track = el('span', undefined, 'transfer-track');
    const fill = el('span', undefined, `transfer-fill ${cls}`); fill.style.width = `${value * 100}%`; track.append(fill);
    row.append(el('span', name), track, el('strong', value.toFixed(4))); bars.append(row);
  }
  bars.setAttribute('aria-label', `${metric.label}: original ${metric.original.toFixed(4)}, stores v3 ${metric.v3.toFixed(4)}. Scale 0 to 1.`);
  const delta = metric.v3 - metric.original;
  $('transfer-delta').textContent = `${metric.label}: ${delta >= 0 ? '+' : ''}${delta.toFixed(4)} (${delta >= 0 ? '+' : ''}${(delta * 100).toFixed(2)} percentage points). Both models were evaluated on the same ${count(report.data.partitions.test.rows)} held-out store reviews. Bars start at zero; full width is 1.00.`;
  for (const button of $('transfer-metric-controls').querySelectorAll('button')) {
    const active = button.dataset.metric === metricKey; button.classList.toggle('is-active', active); button.setAttribute('aria-pressed', String(active));
  }
}
function matrixTable() {
  const table = el('table', undefined, 'data-table transfer-confusion');
  table.append(el('caption', 'v3 confusion matrix · rows actual, columns predicted'));
  const head = el('thead'); const headings = el('tr'); headings.append(el('th', 'Actual / predicted'));
  for (const label of report.labels) { const th = el('th', label, 'num'); th.scope = 'col'; headings.append(th); }
  head.append(headings); table.append(head);
  const body = el('tbody');
  report.confusion_matrix.forEach((values, i) => {
    const tr = el('tr'); const th = el('th', report.labels[i]); th.scope = 'row'; tr.append(th);
    values.forEach((value, j) => { const td = el('td', count(value), 'num'); if (i === j) td.classList.add('is-correct'); tr.append(td); }); body.append(tr);
  });
  table.append(body); $('transfer-matrix').replaceChildren(table);
}
function render(value) {
  report = value;
  if (!report?.comparison?.length) throw new Error('Published stores v3 evidence is unavailable.');
  $('transfer-title').textContent = report.method.title;
  for (const key of ['intro', 'frozen', 'trainable', 'selection', 'interpretation']) {
    const id = key === 'trainable' ? 'transfer-trained' : `transfer-${key}`; $(id).textContent = report.method[key];
  }
  $('transfer-head').textContent = report.method.head;
  $('transfer-neutral-title').textContent = report.method.neutral_title;
  $('transfer-frozen-title').textContent = `Embeddings + first ${report.architecture.frozen_blocks} blocks`;
  $('transfer-trained-title').textContent = `Final ${report.architecture.trained_blocks} encoder block`;
  $('transfer-scope').textContent = report.scope;
  const settings = $('transfer-settings'); settings.replaceChildren();
  const train = report.training;
  for (const [label, valueText] of [['Train / validation / test', ['train', 'validation', 'test'].map((key) => count(report.data.partitions[key].rows)).join(' / ')], ['Best epoch', `${train.best_epoch} of ${train.epochs}`], ['Batch / learning rate', `${train.batch_size} / ${train.learning_rate}`], ['Device / seed', `${train.device.toUpperCase()} / ${train.seed}`]]) {
    const item = el('div'); item.append(el('dt', label), el('dd', valueText)); settings.append(item);
  }
  const audit = report.data.language_audit;
  $('transfer-language').textContent = `Language review: ${count(audit.audited_rows)} of ${count(audit.source_rows)} rows audited; ${count(audit.excluded_rows)} excluded. Policy: ${audit.policy}.`;
  const macro = report.comparison.find((item) => item.key === 'macro_f1');
  const kpis = $('transfer-kpis'); kpis.replaceChildren();
  for (const [valueText, label] of [[count(train.trainable_parameters), 'Trainable parameters'], [`${train.trainable_percent.toFixed(2)}%`, 'Of all parameters'], [count(report.data.partitions.test.rows), 'Held-out test reviews'], [`${macro.v3 - macro.original >= 0 ? '+' : ''}${(macro.v3 - macro.original).toFixed(4)}`, 'Macro F1 change']]) {
    const tile = el('div', undefined, 'stat'); tile.append(el('strong', valueText), el('span', label)); kpis.append(tile);
  }
  const controls = $('transfer-metric-controls'); controls.replaceChildren();
  for (const metric of report.comparison) { const button = el('button', metric.label); button.type = 'button'; button.dataset.metric = metric.key; button.addEventListener('click', () => { metricKey = metric.key; drawMetric(); }); controls.append(button); }
  const table = $('transfer-metrics-table'); table.replaceChildren();
  for (const metric of report.comparison) { const tr = el('tr'); const difference = metric.v3 - metric.original; tr.append(el('th', metric.label), el('td', metric.original.toFixed(4), 'num'), el('td', metric.v3.toFixed(4), 'num'), el('td', `${difference >= 0 ? '+' : ''}${difference.toFixed(4)}`, 'num')); tr.firstChild.scope = 'row'; table.append(tr); }
  const classes = $('transfer-classes-table'); classes.replaceChildren();
  for (const item of report.classes) { const tr = el('tr'); tr.append(el('th', item.label), el('td', item.original_f1.toFixed(4), 'num'), el('td', item.v3_f1.toFixed(4), 'num'), el('td', count(item.support), 'num')); tr.firstChild.scope = 'row'; classes.append(tr); }
  matrixTable();
  const neutral = report.classes.find((item) => item.label === 'neutral');
  const index = report.labels.indexOf('neutral');
  $('transfer-neutral').textContent = `v3 identifies ${count(report.confusion_matrix[index][index])} of ${count(neutral.support)} neutral reviews: ${pct(neutral.v3_recall)} recall, with F1 ${neutral.v3_f1.toFixed(4)}. Read class-level results alongside the overall accuracy.`;
  $('transfer-limits').textContent = report.limits;
  const provenance = $('transfer-provenance'); provenance.replaceChildren();
  const link = el('a', 'Published stores model · v3'); link.href = `https://huggingface.co/${report.model.repo}/tree/${report.model.version}`; link.target = '_blank'; link.rel = 'noopener noreferrer';
  provenance.append(link, el('span', ` · commit ${report.model.commit.slice(0, 12)} · prepared in Workflow 04. This historical comparison does not change the classifier selected for the dashboard above.`));
  drawMetric();
  $('transfer-report').hidden = false; $('transfer-report').setAttribute('aria-busy', 'false'); $('transfer-status').hidden = true;
}
function fail(error) { $('transfer-report').hidden = true; $('transfer-report').setAttribute('aria-busy', 'false'); $('transfer-status').hidden = false; $('transfer-status').textContent = error.message || String(error); }
function update(value) { try { render(value); } catch (error) { fail(error); } }
if (host) {
  host.addEventListener('marimo-value-updated', (event) => update(event.detail.value));
  host.addEventListener('marimo-value-error', (event) => fail(event.detail));
  if (host.marimoValue !== undefined) update(host.marimoValue);
  else if (host.dataset.marimoError) fail({ message: host.dataset.marimoError });
}
