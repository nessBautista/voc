# HTML starter instructions

Follow the `marimo-studio` skill for notebook ownership, projection selection,
view lifecycle, and validation. This file covers the browser-native project
supplied by this starter.

## Project intent

Overview of the pinned **workable** VOC release for Workflow 01
(`notebooks/demos/workflow01.py`). Audience: the team confirming which
dataset a workflow run will use. The notebook loads the release through
`voc_demo.reader.get_dataset()` and publishes a compact, JSON-compatible
`studio_payload` (counts, dates, column names); the page only formats it.
`DESIGN.md` records the reading order, evidence rules and visual language.

Decisions to preserve:

- Self-contained page: no CDN scripts or external fonts. The UnoCSS and Iconify
  tags from the starter were removed; styles live in `style.css`.
- Fixed platform colour slots (Google Play 1, App Store 2, X 3, YouTube 4) from
  the validated reference palette, light and dark steps; never cycle colours.
- Deck layout: pages are `section.page[data-page]` inside `#deck`, listed in
  `#page-tabs`; `view.js` routes by hash (`#/overview`, `#/dataset`) and arrow
  keys. Keep page titles for the Overview page in the notebook cell.
- Value projections: `studio_payload` (Dataset page), `embedding_payload`
  (Embeddings page, also the vectors for the Reduction page) and
  `reduction_payload` (Reduction page), `cluster_payload` (Clusters page) and
  `topic_payload` (Topics page; its `walk` entries carry the per-term counts
  behind the formula so the page never recomputes c-TF-IDF) and
  `naming_payload` (Naming page: every exchange with the model, the four
  keyword representations per topic, evidence and citations as row indices)
  and `sentiment_payload` (Sentiment page: label, probabilities and rating per
  row, the per-topic mix joined with features, the rating crosstab) and
  `dashboard_payload` (store catalogue, month per row). The dashboard renders
  only when all of `embedding`, `topic`, `naming`, `sentiment` and `dashboard`
  payloads are present (`tryRenderDashboard` in `view.js` builds the joined
  table and calls `window.VOCDashboard.mount`).
  The reduction and clusters pages render only once all of their payloads have
  arrived (`tryRenderReduction`, `tryRenderClusters`). The code block on the Embeddings page is literal copy of
  the notebook's prepare/encode cells; update it when those cells change.
- Native cells projected: `overview_section` on the Overview page;
  `dataset_refresh` and `load_dataset` inside the provenance disclosure of the
  Dataset page. Everything else renders from `studio_payload`.
- Keep review text, author identifiers and storage URIs out of the payload.
- The Overview page's pipeline diagram and dashboard card are authored HTML/CSS
  (`.pipeline`, `.results-flow`, `.topic-list` in `style.css`). Stage names are
  mirrored in the notebook's `overview_pipeline` mermaid cell: change both.
  The dashboard card is mock data by design; keep its badge until it is
  replaced by projected results.

## Files

- `index.html` declares the deck navigation, the pages, the `#payload` host
  (`mo-value="studio_payload"`), the three `<marimo-cell>` hosts and the Lens
  attributes on every authored region.
- `style.css` holds the tokens (light and dark) and all component styles.
- `view.js` defines `observeMarimoValue`, formatting helpers, the shared tooltip,
  one `render*` function per Dataset section, the Embeddings page (`renderEmbeddings`,
  `showReview`, `renderNeighbors`, int8 vector decoding), the Reduction page
  (`renderProblem`, `scatterDims`, `renderUmap`, `selectUmap`, modal wiring) and
  the hash router.
- `reduction.js`, `clusters.js`, `topics.js`, `naming.js` and `sentiment.js`
  are leaf modules with the panel demos (`window.VOCReductionDemos`,
  `window.VOCClusterDemos`, `window.VOCTopicDemos`, `window.VOCNamingDemos`,
  `window.VOCSentimentDemos`), mounted when a modal opens. `dashboard.js` is
  the whole last page (`window.VOCDashboard.mount(data)`), self-contained
  apart from the joined table it receives. They must stay
  self-contained (no imports); `topics.js` receives the stop-word list and
  review texts as arguments rather than reading notebook data itself.
- `view.js` also holds the Clusters page (`groupTable`, `renderClusterMap`,
  `renderGroupList`, `renderGroupPanel`, `setClusterView`) and the Topics page
  (`renderWalk`, `renderTopicList`, `renderTopicPanel`, `selectTopic`) and the
  Naming page (`renderAnatomy`, `renderNamingList`, `tryRenderNaming`) and the
  Sentiment page (`renderSentimentBars`, `renderSentimentPanel`,
  `renderRatingCheck`, `tryRenderSentiment`).
- Modals are `dialog.modal` elements with a `.dialog-close[data-close]` button;
  while any dialog is open the arrow keys do not change pages.
- Run `node --check view.js` after editing, then build and verify in a browser.

## Use the supplied Studio integration

`index.html` starts with one `<marimo-cell>` host for each enabled notebook cell
that may display output, including literal Markdown. Keep, reorder, group, or
replace those hosts as the page design develops. Their generated names remain
stable Studio targets for the notebook cells.

The starter defines `observeMarimoValue` inside the `index.html` module script.
Keep it inline or move it to a JavaScript file referenced directly from the
entry document. Use it when page JavaScript consumes a notebook value or eager
dataframe. Keep the corresponding `mo-value` host in authored HTML so Studio
can inspect and authorize its selector.

```html
<span id="rows-data" hidden mo-value="rows"></span>
```

```js
const source = document.querySelector("#rows-data");
if (source) {
  const stop = observeMarimoValue(source, {
    onValue: (rows) => renderRows(rows),
    onError: (error) => renderError(error.message),
  });
  window.addEventListener("pagehide", stop, { once: true });
}
```

Eager dataframes arrive as a shared Flechette `Table`. Use
[https://github.com/uwdata/flechette](https://github.com/uwdata/flechette) as
the table API reference. Treat the table as immutable. Keep data columnar with
`getChild()`, `select()`, and `toColumns()`. Call `toArray()` when browser code
needs row objects.

## Add dependencies

Add a browser dependency by referencing its URL from `index.html`, with a
`<script src>` tag or an `import` statement in a module script. Studio publishes
the page as written, so the next build picks up the new URL.

`index.html` loads two pinned browser dependencies from jsDelivr:

- [UnoCSS runtime](https://unocss.dev/integrations/runtime) with its default
  Wind3 preset. Use utility classes directly in authored HTML. The
  runtime observes DOM changes and generates matching styles in the browser.
- [Iconify Icon web component](https://iconify.design/docs/iconify-icon/).
  Add named icons with the registered `iconify-icon` element:

```html
<button type="button" class="inline-flex items-center gap-2">
  <iconify-icon inline icon="lucide:download" aria-hidden="true"></iconify-icon>
  Download
</button>
```

The scripts and Iconify API requests require browser network access. Configure
the hosting content security policy with `script-src` access to jsDelivr,
`connect-src` access to the configured Iconify API, and `style-src` permission
for the inline `<style>` element generated by UnoCSS. The style rule typically
requires `'unsafe-inline'`. Use precompiled project CSS when the hosting policy
permits only nonce or hash styles. Update the pinned URL and its integrity hash
together when changing either dependency.

Use these small defaults before adding another styling or icon dependency:

- Compose ordinary layout, spacing, responsive behavior, typography, borders,
  and states with UnoCSS utilities in the HTML.
- Keep authored CSS for the view's tokens, projection variables, complex
  selectors, data visualizations, keyframes, and print behavior.
- Use Iconify for interface icons. Keep repeated SVG markup and decorative
  Unicode characters out of controls. Keep a visible label or an accessible
  name on interactive controls.

Aim for a short stylesheet whose remaining rules express the view's own visual
system. Avoid copying utility-equivalent declarations into large selector
blocks.

Import browser-ready ESM modules at the top of the module script. Prefer a
versioned URL for maintained project source:

```js
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
```

Choose the URL form that matches the dependency source:

- Latest npm release for deliberate experiments:
  `import * as d3 from "https://cdn.jsdelivr.net/npm/d3/+esm";`
- Versioned npm package:
  `import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";`
- Concise statistical charts:
  `import * as Plot from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/+esm";`
- Tabular transformation:
  `import * as aq from "https://cdn.jsdelivr.net/npm/arquero@8/+esm";`
- Modular charting from an exported package subpath:
  `import * as echarts from "https://cdn.jsdelivr.net/npm/echarts@6/core/+esm";`
- CSV parsing from JSR through esm.sh:
  `import { parse as parseCsv } from "https://esm.sh/jsr/@std/csv";`

Remote modules require browser network access and a hosting content security
policy that allows the selected CDN. Keep all dependency origins explicit and
prefer versioned imports when the same source must rebuild consistently.

## Work within the HTML project

- Keep the document structure and projection hosts in `index.html`.
- Keep styles and browser behavior inline for a compact page, or reference CSS
  through `<link rel="stylesheet">` and JavaScript through `<script src>`
  directly from `index.html`. Studio copies these exact `.css`, `.js`, and
  `.mjs` source files into the browser artifact.
- Keep each direct source as a leaf file. Bundle or inline local CSS `url()` and
  `@import` dependencies and local JavaScript imports or re-exports. Explicit
  HTTPS, data, and fragment references remain available.
- Leave `<base href>` out of the entry document. HTTP and HTTPS dependency URLs
  need `//` and a host.
- Choose a provider that builds the JavaScript module graph for import maps,
  computed imports, and source-phase imports.
- Keep direct JavaScript within the pinned parser's accepted grammar. Inspection
  fails closed when it cannot establish the dependency boundary.
- End `break` and `continue` with `;` when another statement follows. The pinned
  grammar rejects a following regex statement when automatic semicolon
  insertion separates it from the restricted statement.
- The pinned grammar rejects import attributes on re-export statements. Import
  the remote module with its attributes, then use
  `export { value as default }` to preserve a default re-export. Enumerate named
  exports or choose a graph-building provider for a wildcard re-export.
- Keep projection hosts inside `#app-shell`.
- Inline project-owned images and fonts with the document. External HTTP URLs
  and `data:` URLs remain available.
- Use browser APIs for focused interaction. Choose the React or Svelte starter
  when the page needs component compilation, a local import graph, or separate
  browser assets.

Studio publishes the entry document and its declared local sources after
validating the HTML and projection hosts. Treat the built artifact as the
acceptance boundary for the page.

## Link custom results to notebook inputs

Keep projection hosts explicit in authored source. Custom regions need every
kernel input, a readable label, and a rendering-source reference such as
`{"path":"index.html"}`. Keep these attributes on authored elements outside
native output subtrees. Follow the installed Studio skill's
`references/projections.md` for the shared contract:

```python
import marimo_studio

print(marimo_studio.agent.skill().file("references/projections.md").read_text())
```

## Maintain project ignore rules

You own this view project's `.gitignore`. When adding libraries, extensions, or
build tools, ignore their generated files, caches, local configuration, and
secrets. Keep authored source, dependency manifests, and lockfiles tracked.
Studio supplies workspace rules for its own artifacts and locks. Check
`git status --short --ignored` after running new tooling and update the view's
ignore rules before committing.
