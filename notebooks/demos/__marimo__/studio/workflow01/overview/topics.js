// Illustrations for the Topics page panels. Self-contained: no imports. The vectorizer panel receives the
// notebook's stop-word list and review texts from view.js; the c-TF-IDF panel uses a mock corpus.
// Exposed as window.VOCTopicDemos = { mountVectorizer(options), mountCtfidf() }.

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
};

// ------------------------------------------------------------ vectorizer demo
// Mirrors scikit-learn's CountVectorizer defaults: lowercase, tokens of 2+ word characters, stop words removed
// before n-grams are built, then unigrams and bigrams.
const TOKEN = /[\p{L}\p{N}_]{2,}/gu;
function tokenise(text, stopWords) {
  const raw = (text.toLowerCase().match(TOKEN) || []);
  const stop = new Set(stopWords);
  const kept = raw.filter((t) => !stop.has(t));
  const bigrams = kept.slice(0, -1).map((t, i) => `${t} ${kept[i + 1]}`);
  return { raw, kept, bigrams, stop };
}
const vec = { mounted: false, texts: [], cursor: 0, stopWords: [] };
function drawTokens() {
  const { raw, kept, bigrams, stop } = tokenise($('vectorizer-input').value, vec.stopWords);
  const tokens = $('vectorizer-tokens'); tokens.replaceChildren();
  for (const t of raw) tokens.append(node('span', t, stop.has(t) ? 'chip chip-stop' : 'chip'));
  if (!raw.length) tokens.append(node('span', 'no tokens', 'subtle'));
  const grams = $('vectorizer-bigrams'); grams.replaceChildren();
  for (const b of bigrams) grams.append(node('span', b, 'chip chip-bigram'));
  if (!bigrams.length) grams.append(node('span', 'no bigrams', 'subtle'));
  const counted = new Set([...kept, ...bigrams]).size;
  $('vectorizer-note').textContent = `${raw.length} tokens → ${kept.length} after stop words → ${kept.length} unigrams + ${bigrams.length} bigrams (${counted} distinct terms counted for this review).`;
}
function mountVectorizer({ texts, stopWords, note }) {
  vec.texts = texts; vec.stopWords = stopWords; vec.cursor = 0;
  if (!vec.mounted) {
    vec.mounted = true;
    $('vectorizer-input').addEventListener('input', drawTokens);
    $('vectorizer-next').addEventListener('click', () => {
      if (!vec.texts.length) return;
      vec.cursor = (vec.cursor + 1) % vec.texts.length;
      $('vectorizer-input').value = vec.texts[vec.cursor]; drawTokens();
    });
  }
  $('vectorizer-input').value = texts[0] || '';
  $('vectorizer-next').hidden = texts.length < 2;
  drawTokens();
  if (note) $('vectorizer-note').textContent += ` ${note}`;
}

// ---------------------------------------------------------- c-TF-IDF vs TF-IDF
const CORPUS = [
  { name: 'Group A · login', reviews: ['no abre la app', 'no puedo entrar', 'no abre nunca', 'la app no abre'] },
  { name: 'Group B · praise', reviews: ['excelente', 'excelente', 'excelente app', 'muy buena app'] },
  { name: 'Group C · transfers', reviews: ['transferencia lenta', 'la transferencia falla', 'no llega la transferencia', 'transferencia tardada'] },
];
const STOP = new Set(['la', 'no'].filter(() => false)); // the mock keeps every word so the two formulas can be compared cleanly
const ct = { mounted: false, dropped: new Set() };
const corpusTokens = () => CORPUS.map((g, gi) => g.reviews.map((r, ri) => r.split(' ').map((w, wi) => ({ w, on: !ct.dropped.has(`${gi}:${ri}:${wi}`), key: `${gi}:${ri}:${wi}` }))));
function drawCorpus() {
  const host = $('ctfidf-corpus'); host.replaceChildren();
  const toks = corpusTokens();
  const chosenGroup = Number($('ctfidf-group').value || 0), chosenWord = $('ctfidf-word').value;
  CORPUS.forEach((g, gi) => {
    const column = node('div', undefined, `mock-group${gi === chosenGroup ? ' is-chosen' : ''}`);
    column.append(node('p', g.name, 'eyebrow'));
    g.reviews.forEach((_, ri) => {
      const line = node('div', undefined, 'mock-review');
      toks[gi][ri].forEach(({ w, on, key }) => {
        const chip = node('button', w, `chip chip-word${on ? '' : ' is-off'}${w === chosenWord ? ' is-word' : ''}`);
        chip.type = 'button'; chip.setAttribute('aria-pressed', String(on));
        chip.addEventListener('click', () => { if (ct.dropped.has(key)) ct.dropped.delete(key); else ct.dropped.add(key); drawCorpus(); });
        line.append(chip);
      });
      column.append(line);
    });
    host.append(column);
  });
  drawReadout(toks);
}
function drawReadout(toks) {
  const word = $('ctfidf-word').value, gi = Number($('ctfidf-group').value || 0);
  const docs = toks.flat().map((r) => r.filter((t) => t.on).map((t) => t.w));
  const N = docs.length, df = docs.filter((d) => d.includes(word)).length;
  const groups = toks.map((g) => g.flatMap((r) => r.filter((t) => t.on).map((t) => t.w)));
  const f_t = groups.reduce((s, g) => s + g.filter((w) => w === word).length, 0);
  const A = groups.reduce((s, g) => s + g.length, 0) / groups.length;
  const tfDoc = docs.filter((d) => d.includes(word)).length ? Math.max(...docs.map((d) => d.filter((w) => w === word).length)) : 0;
  const tfClass = groups[gi].filter((w) => w === word).length;
  const classic = df ? Math.log(N / df) : 0;
  const classBased = f_t ? Math.log(1 + A / f_t) : 0;
  const host = $('ctfidf-readout'); host.replaceChildren();
  const rows = [
    ['Classic TF-IDF', `appears in ${df} of ${N} reviews → idf = log(${N} / ${df || '…'}) = ${classic.toFixed(2)}`, `highest tf in one review ${tfDoc} → tf·idf = ${(tfDoc * classic).toFixed(2)}`],
    ['c-TF-IDF', `${f_t} occurrences across all groups, A = ${A.toFixed(1)} words per group → log(1 + ${A.toFixed(1)} / ${f_t || '…'}) = ${classBased.toFixed(2)}`, `tf in ${CORPUS[gi].name.split(' ·')[0]} = ${tfClass} of ${groups[gi].length} words → √(${tfClass}/${groups[gi].length}) × ${classBased.toFixed(2)} = ${(Math.sqrt(tfClass / Math.max(1, groups[gi].length)) * classBased).toFixed(3)}`],
  ];
  for (const [title, a, b] of rows) {
    const card = node('div', undefined, 'readout-card');
    card.append(node('strong', title), node('span', a), node('span', b));
    host.append(card);
  }
  const verdict = node('p', undefined, 'subtle');
  verdict.textContent = word === 'excelente'
    ? 'Three one-word reviews are three documents for the classic formula, so its idf stays modest; for c-TF-IDF they are one dense group, and the word is clearly group B’s.'
    : df === N ? 'A word in every review has idf 0 for the classic formula; c-TF-IDF still gives it a small positive weight, which is why the stop-word list matters there.'
      : 'Drop or restore words above and watch the two factors move differently: the classic one counts documents, the class-based one counts how much the word belongs to other groups.';
  host.append(verdict);
}
function mountCtfidf() {
  if (ct.mounted) { drawCorpus(); return; }
  ct.mounted = true;
  const vocab = [...new Set(CORPUS.flatMap((g) => g.reviews.flatMap((r) => r.split(' '))))].sort();
  for (const w of vocab) { const o = node('option', w); o.value = w; $('ctfidf-word').append(o); }
  $('ctfidf-word').value = 'excelente';
  CORPUS.forEach((g, gi) => { const o = node('option', g.name); o.value = String(gi); $('ctfidf-group').append(o); });
  $('ctfidf-group').value = '1';
  $('ctfidf-word').addEventListener('change', drawCorpus);
  $('ctfidf-group').addEventListener('change', drawCorpus);
  drawCorpus();
}

window.VOCTopicDemos = { mountVectorizer, mountCtfidf, tokenise };
