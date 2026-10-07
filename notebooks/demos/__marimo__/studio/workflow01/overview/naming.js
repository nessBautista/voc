// The two reranker panels of the Naming page. They show the notebook's real keyword lists before and after
// each block; view.js hands them the payload. Exposed as window.VOCNamingDemos = { mountKeyBERT, mountMMR }.

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
};
const state = { data: null, keybertMounted: false, mmrMounted: false };

function fillTopics(select, data) {
  select.replaceChildren();
  for (const label of data.labels) {
    const option = node('option', `Topic ${label.topic} · ${(data.aspects.ctfidf[String(label.topic)] || []).slice(0, 2).map((w) => w[0]).join(', ')}`);
    option.value = String(label.topic); select.append(option);
  }
}
// Two keyword columns; words that appear in both are linked by colour so movement is visible.
function compare(host, before, after, beforeTitle, afterTitle) {
  host.replaceChildren();
  const beforeWords = before.map((w) => w[0]), afterWords = after.map((w) => w[0]);
  for (const [title, list, other] of [[beforeTitle, before, afterWords], [afterTitle, after, beforeWords]]) {
    const column = node('div', undefined, 'compare-column');
    column.append(node('p', title, 'eyebrow'));
    const ol = node('ol', undefined, 'compare-list');
    list.forEach(([word, score], i) => {
      const item = node('li');
      const shared = other.includes(word);
      const rankThere = other.indexOf(word);
      item.append(node('span', String(i + 1), 'compare-rank'), node('code', word, shared ? 'is-shared' : 'is-only'));
      item.append(node('small', shared ? (rankThere === i ? 'same rank' : rankThere > i ? `↑ from ${rankThere + 1}` : `↓ from ${rankThere + 1}`) : 'only here', 'compare-note'));
      if (typeof score === 'number') item.append(node('small', score.toFixed(3), 'compare-score'));
      ol.append(item);
    });
    column.append(ol); host.append(column);
  }
}
function drawKeyBERT() {
  const t = $('keybert-topic').value;
  const before = state.data.aspects.ctfidf[t] || [], after = state.data.aspects.keybert[t] || [];
  compare($('keybert-compare'), before, after, 'c-TF-IDF · by counts', 'KeyBERT-inspired · by similarity to the topic');
  const kept = after.filter((w) => before.some((b) => b[0] === w[0])).length;
  $('keybert-note').textContent = `${kept} of ${after.length} words were already in the c-TF-IDF ten; ${after.length - kept} came up from the candidate list. Scores are cosine similarities on the right, c-TF-IDF weights on the left.`;
}
function drawMMR() {
  const t = $('mmr-topic').value, d = $('mmr-diversity').value;
  const before = state.data.aspects.keybert[t] || [], after = state.data.aspects[`mmr_${d}`][t] || [];
  compare($('mmr-compare'), before, after, 'KeyBERT-inspired · ten by relevance', `KeyBERT → MMR · diversity ${d}`);
  const distinctBefore = new Set(before.flatMap((w) => w[0].split(' '))).size, distinctAfter = new Set(after.flatMap((w) => w[0].split(' '))).size;
  $('mmr-note').textContent = `Distinct words across the ten keywords: ${distinctBefore} before, ${distinctAfter} after. ${d === state.data.label_aspect.replace('mmr_', '') ? 'This is the list the label block received.' : 'Higher diversity trades relevance for coverage.'}`;
}
function mountKeyBERT(data) {
  state.data = data;
  if (!state.keybertMounted) { state.keybertMounted = true; fillTopics($('keybert-topic'), data); $('keybert-topic').addEventListener('change', drawKeyBERT); }
  drawKeyBERT();
}
function mountMMR(data) {
  state.data = data;
  if (!state.mmrMounted) {
    state.mmrMounted = true; fillTopics($('mmr-topic'), data);
    const div = $('mmr-diversity'); div.replaceChildren();
    for (const key of Object.keys(data.aspects).filter((k) => k.startsWith('mmr_'))) { const o = node('option', key.replace('mmr_', '')); o.value = key.replace('mmr_', ''); div.append(o); }
    div.value = data.label_aspect.replace('mmr_', '');
    $('mmr-topic').addEventListener('change', drawMMR); div.addEventListener('change', drawMMR);
  }
  drawMMR();
}
function setTopic(topic) { for (const id of ['keybert-topic', 'mmr-topic']) if ($(id).options.length) $(id).value = String(topic); }

window.VOCNamingDemos = { mountKeyBERT, mountMMR, setTopic };
