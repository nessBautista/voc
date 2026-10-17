// The classifier panel of the Sentiment page: a review's real probabilities (handed in by view.js) and a
// hand-driven softmax. Exposed as window.VOCSentimentDemos = { mountClassifier(options) }.

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
};
const LABELS = ['negative', 'neutral', 'positive'];
const CLASSES = { negative: 'neg', neutral: 'neu', positive: 'pos' };
const state = { mounted: false, reviews: [], cursor: 0 };

function probBars(host, probs, winner) {
  host.replaceChildren();
  LABELS.forEach((label, i) => {
    const row = node('div', undefined, `prob-row${label === winner ? ' is-winner' : ''}`);
    const track = node('span', undefined, 'walk-bar'); const fill = node('span', undefined, `walk-fill ${CLASSES[label]}`); fill.style.width = `${(100 * probs[i]).toFixed(1)}%`; track.append(fill);
    row.append(node('span', label, 'prob-label'), track, node('strong', `${(100 * probs[i]).toFixed(1)}%`));
    host.append(row);
  });
}
function drawReview() {
  const review = state.reviews[state.cursor];
  if (!review) { $('classifier-text').textContent = 'No reviews to show.'; return; }
  $('classifier-position').textContent = `${state.cursor + 1} of ${state.reviews.length} · ${review.context}`;
  $('classifier-text').textContent = review.text;
  const winner = LABELS[review.probs.indexOf(Math.max(...review.probs))];
  probBars($('classifier-probs'), review.probs, winner);
  const sorted = [...review.probs].sort((a, b) => b - a);
  $('classifier-note').textContent = `Label: ${winner}. Margin over the runner-up: ${(100 * (sorted[0] - sorted[1])).toFixed(0)} points.${review.rating ? ` The reviewer gave ${review.rating} star${review.rating === 1 ? '' : 's'}.` : ''}`;
  $('classifier-prev').disabled = state.cursor === 0; $('classifier-next').disabled = state.cursor >= state.reviews.length - 1;
}
function drawSoftmax() {
  const scores = ['neg', 'neu', 'pos'].map((k) => Number($(`logit-${k}`).value));
  ['neg', 'neu', 'pos'].forEach((k, i) => { $(`logit-${k}-value`).value = scores[i].toFixed(1); });
  const max = Math.max(...scores);
  const exps = scores.map((s) => Math.exp(s - max));
  const sum = exps.reduce((a, b) => a + b, 0);
  const probs = exps.map((e) => e / sum);
  const winner = LABELS[probs.indexOf(Math.max(...probs))];
  probBars($('softmax-bars'), probs, winner);
  $('softmax-note').textContent = `p(class) = e^score / Σ e^scores. Raising one score lowers the other two; a 1-point gap is roughly 73 : 27, a 3-point gap is 95 : 5. The label is ${winner} whatever the margin.`;
}
function mountClassifier({ reviews }) {
  state.reviews = reviews; state.cursor = 0;
  if (!state.mounted) {
    state.mounted = true;
    $('classifier-prev').addEventListener('click', () => { state.cursor = Math.max(0, state.cursor - 1); drawReview(); });
    $('classifier-next').addEventListener('click', () => { state.cursor = Math.min(state.reviews.length - 1, state.cursor + 1); drawReview(); });
    for (const k of ['neg', 'neu', 'pos']) $(`logit-${k}`).addEventListener('input', drawSoftmax);
  }
  drawReview(); drawSoftmax();
}

window.VOCSentimentDemos = { mountClassifier };
