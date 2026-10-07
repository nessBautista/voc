# Workable dataset overview

Audience: the VOC team opening Workflow 01 and asking "which dataset am I about to
work with?". The view answers in one screen: how many records, from which
sources, over which dates, which release, and what a row holds. It is the
smoke-test view for the prototype notebook and the first page of every later
workflow view.

## Pages

The view is a deck: one page visible at a time, addressed by the URL hash and
moved with the tab list, the ← → buttons or the arrow keys.

1. **Overview** (`#/overview`): the heading and intro prose are projected from
   the notebook's `overview_section` cell. Below it the view draws its own
   pipeline diagram: six numbered stages (Dataset, Embeddings, Dimensionality
   reduction, Clustering, Topic modelling, Sentiment analysis), a database
   cylinder the results flow into, and a dashboard card that reads from it.
   The dashboard card holds **invented topics and shares**, badged
   "Illustrative · mock values"; it shows the shape of the result, never a
   finding. The notebook keeps a mermaid sketch of the same stages
   (`overview_pipeline`), which the view does not project.
2. **Dataset** (`#/dataset`): the pinned workable release, rendered from
   `studio_payload` in the reading order below.
3. **Embeddings** (`#/embeddings`): rendered from `embedding_payload`. A model
   card (id, pinned revision, 384 dimensions, 128-token limit, unit length) with
   four sample statistics; a condensed code block of the notebook's prepare and
   encode cells (literal copy, kept in step with the notebook); and an
   input → output explorer: prev / next / number / random pick one of the 1,000
   reviews, the left card shows the exact text with token count, the right card
   draws the 384 coordinates as a 32 × 12 heat grid (red negative, blue positive,
   intensity by magnitude) with the first eight exact values. "Find its nearest
   neighbours" opens a modal: the selected review, the five closest reviews with
   their exact cosine similarity as bars, a jump-on-click, arrow-key stepping,
   and a short explanation of cosine similarity ending with a live dot product
   recomputed in the page from the rounded vectors next to the notebook's exact
   value.
4. **Reduction** (`#/reduction`): rendered from `reduction_payload` plus the
   vectors already decoded from `embedding_payload`. Part one states the
   problem (we can measure closeness but not see it) and proves it with two
   real reviews that are close in 384-D: three scatter panels of all 1,000
   vectors in two coordinates each, "looks close", "looks far" and a pair of
   sliders for any other choice, with the pair highlighted and its distance in
   that plane. Part two is the UMAP result: the 2-D map coloured by platform
   with hover text, click-to-select showing the review's five 384-D neighbours
   on the map and how many are also map-neighbours, the parameters, and the
   neighbourhood-preservation tiles. Two buttons open modals, "How UMAP works"
   and "And PCA", each with a short explanation and an interactive mock-data
   demo (`reduction.js`): UMAP builds the k-nearest-neighbour graph of 42
   points and lays them out in one dimension with adjustable `n_neighbors` and
   `min_dist`; PCA projects 80 points onto a turnable line and reports the
   variance kept against PC1. Both demos are labelled as simplified
   illustrations on mock data.
5. **Clusters** (`#/clusters`): rendered from `cluster_payload` plus the texts
   and 2-D coordinates of the earlier payloads. Part one explains density
   clustering and why HDBSCAN rather than k-means, with two modals
   (`clusters.js`): HDBSCAN on 70 mock points with `min_samples` and `reach`
   sliders, core / border / noise marks and a strip of group counts across all
   reaches (the persistence HDBSCAN reads); k-means on the same points with a k
   slider, Step / Run / New start and a total-distance trace. Part two is the
   result: the 2-D map coloured by group, where the eight largest groups take
   the categorical slots, the rest share the muted colour and unassigned
   reviews are crosses; a ranked group list (size, share, bar); clicking a group
   in the list or on the map draws it in ink, fades the rest and opens a panel
   with its reviews (firmest members first, search, eight per page); a toggle
   redraws the map with k-means at the same k; the parameters and the
   HDBSCAN-versus-k-means comparison table close the page.
6. **Topics** (`#/topics`): rendered from `topic_payload` plus texts and
   cluster strengths. Part one: keywords are evidence, BERTopic's two halves,
   the vocabulary choices, with two modals (`topics.js`): "What the vectorizer
   sees" tokenises a real review from the chosen topic (or typed text) the way
   scikit-learn does, striking stop words and listing bigrams; "c-TF-IDF versus
   the classic TF-IDF" is a three-group mock corpus where words can be dropped
   and both weights are recomputed. Part two is the formula with real numbers:
   a topic picker and a square-root toggle drive a table of the topic's top
   terms through count → share → class weight → c-TF-IDF, with rank movement
   arrows and a formula strip that lights the factor under the cursor; every
   number comes from the notebook. Part three is the result: the ranked topic
   list with a keyword search, and a reader panel with the ten keyword bars,
   BERTopic's representative reviews and all members (firmest first, search,
   paging). The page ends by naming the limits that motivate the next page.
7. **Naming** (`#/naming`): rendered from `naming_payload` plus texts and topic
   counts. Part one explains the text generation block (one call per topic,
   keywords plus representative reviews, the Spanish prompt that treats reviews
   as untrusted data and returns JSON with an assessment and citations, caching)
   with two modals (`naming.js`) that show our real keyword lists before and
   after the KeyBERT-inspired and MMR blocks for any topic. Part two is one
   exchange in full: the keywords and evidence cards (aliases `e1`…, cited ones
   marked), the raw request, the model pill and system prompt, and the answer
   read back (assessment badge, feature headline or "no single feature", the
   description, citation count, raw response). Part three is the result: four
   tiles by assessment, a filterable list where a feature headline appears only
   for `single_feature` and the description otherwise, and the finding that
   praise groups come back unclear because they carry sentiment, not features.
8. **Sentiment** (`#/sentiment`): rendered from `sentiment_payload` plus texts
   and topic keywords. Part one: whole-review sentiment with RoBERTuito, the
   topic mix as counts of reviews, uncalibrated probabilities, not aspect-based;
   a modal (`sentiment.js`) shows real reviews of the current topic with their
   three probabilities and a hand-driven softmax. Part two is the result: four
   tiles, then every topic as a stacked bar (length = size, segments = predicted
   sentiment) sortable by negative share, size or positive share; clicking a bar
   opens a reader with the topic's mix, a label filter, search, paging, and per
   review its badge, star rating when known, a three-segment probability strip
   and the winning probability. Part three is the rating check: a stacked chart
   of predicted label by star rating, the counts, the agreement rates computed
   in the page from the crosstab, and the five-star-neutral examples that show
   the classifier keeping one-word praise neutral.
9. **Dashboard** (`#/dashboard`): the database and dashboard stage, in a
   product form factor rather than a slide. `dashboard_payload` adds only the
   store catalogue and each review's month; `dashboard.js` joins every earlier
   payload by row (review, platform, month, rating, topic, label, assessment,
   sentiment) and does all filtering and counting in the browser. Header with
   the release and a "reads from the store" badge (cylinder) opening a modal
   that lists the twelve cached artifacts with their manifests and a schema
   strip (review → vector → group → topic, + sentiment). Sticky filter bar:
   platform, sentiment and assessment chips, a month range, text search,
   reset. Five KPI tiles for the selection; the "most negative feature" tile
   considers feature topics only (single feature or mixed, never unclear or
   unassigned) with at least five reviews in view and ranks them by the 95 %
   Wilson lower bound of their negative share, so a small unanimous group does
   not outrank a large, mostly negative one; the share, the count and the bound
   are printed under the name. Main card: topics by volume and
   sentiment (sortable; unassigned always last), click to drill down. Side
   cards: sentiment over time (stacked months), by platform, overall mood
   ring. Drill-down: the topic's label, assessment, description, keywords,
   mix, its own monthly trend, and its reviews under the current filters. The
   footer restates that counts describe the sample and that labels and
   sentiment are model outputs.

New pages: add a `<section class="page" data-page="…">` inside `#deck` and a
matching tab in `#page-tabs`; the router reads the page list from the DOM.

## Reading order (Dataset page)

1. Masthead lede: records, platforms, record date span, publication date.
2. KPI strip: records, columns, platforms, months covered.
3. Release and record dates (manifest `created_at`, first/last `record_date`,
   time the notebook loaded the pinned release, undated records).
4. Records by source: a ring (the requested pie) with direct labels and a table
   beside it carrying the exact counts, providers, dates and mean rating.
5. Records per month: stacked bars by platform with a legend, hover/focus
   tooltips, totals labelled only on the peak and the latest month, and a table
   view behind a disclosure.
6. Columns: name, dtype, non-null count and a filled bar.
7. Provenance: release, artifact and raw revision IDs, schema, publisher,
   manifest row count versus loaded rows, Parquet size, plus the notebook's
   reload button and status line projected as native cells.

## Evidence rules

- `studio_payload` is the only data source. The notebook computes every count
  and date; the page formats them. Never copy a number into source.
- The Dataset page is counts and dates only. The Embeddings page shows review
  text verbatim because that is the encoder's input; it is always rendered as
  `textContent`, never HTML, and no storage URIs or credentials are projected.
- The system prompt is project text and may be shown; API keys, request ids
  and anything from the OpenRouter response beyond the model name, token count
  and content never enter a payload.
- `embedding_payload` stays under Studio's 1 MB value limit by carrying an
  int8-quantised, base64 copy of the vectors (exact first eight coordinates and
  exact neighbour similarities travel separately). The page labels displayed
  vector values as rounded; the notebook keeps full precision.
- Platform colours follow the entity in a fixed order (Google Play, App Store,
  X, YouTube) and never change with rank or filter. Cluster colours are the
  exception that keeps the eight-slot rule: slots go to the eight largest groups
  of the clustering being drawn, every other group shares one muted colour, and
  the selected group is always ink, so colour never has to identify 37 groups. Identity is never colour
  alone: every chart has direct labels, a legend or a table.
- Percentages state their denominator (all workable records, or the month).
- Mean rating is shown only where the platform carries ratings; social sources
  show a dash rather than zero.
- Mock content is allowed only on the Overview page's dashboard card and in the
  algorithm demos (UMAP, PCA, HDBSCAN, k-means, the c-TF-IDF mini corpus),
  always labelled as illustrative, and must be replaced by projected results before the
  deck shows real topics. Sentiment colours are the diverging trio red / gray /
  blue (never red / green); the dark neutral is stepped so it separates from the
  red under colour-vision deficiency.

## Visual language

Warm paper, ink text, a lime KPI strip, large sans headings; the same editorial
family as the `results` view of `topic-modelling-v0`. Dark theme follows the
viewer's preference through the same tokens, with the dark palette steps from
the validated reference palette. Marks are thin, slices and stacked segments are
separated by 2px paper gaps, grid lines are recessive, and text uses text
tokens rather than series colours.

## Verification

Build through Studio's Python API, run the notebook cells, open the standalone
preview with a real browser, wait for `data-marimo-studio-state="ready"`, then
check: the lede and KPI numbers agree with the notebook output, the ring labels
do not collide, the monthly table matches the bars, the tooltip opens on hover
and on keyboard focus, and the layout holds at 700 px.
