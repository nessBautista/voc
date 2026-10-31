import { expect, test, type Page } from "@playwright/test";
import a from "../fixtures/example_a.json" with { type: "json" };
import b from "../fixtures/example_b.json" with { type: "json" };

// "/" is the conversation. These specs cover what the page itself shows (the
// active publication: loading, deadline, missing, failure, retry, refresh) and
// the dataset panels, which now appear inside answers. Local-mode session.
test.beforeEach(async ({ page }) => {
  await page.route("**/auth/me", (route) =>
    route.fulfill({ json: { email: "developer@localhost", mode: "local" } }),
  );
});

let served: typeof a = a;
async function serve(
  page: Page,
  options: {
    missing?: boolean;
    failSources?: boolean;
    mismatch?: boolean;
    zero?: boolean;
  } = {},
) {
  const fixture = structuredClone(a);
  served = fixture;
  if (options.zero) {
    Object.assign(fixture.results[0].data, {
      row_count: 0,
      unknown_dates: 0,
      record_start: null,
      record_end: null,
    });
    fixture.results[1].data = [] as never;
    Object.assign(fixture.results[2].data, { months: [], unknown_dates: 0 });
    Object.assign(fixture.results[3].data, { sources: [], evidence: [] });
  }
  await page.route("**/api/v1/publications/**", async (route) => {
    const url = route.request().url();
    if (options.missing)
      return route.fulfill({
        status: 503,
        json: { error: { code: "no_active_publication" } },
      });
    if (url.endsWith("/current"))
      return route.fulfill({ json: fixture.selection });
    if (options.failSources && url.endsWith("/sources"))
      return route.fulfill({
        status: 503,
        json: { error: { code: "publication_unavailable" } },
      });
    const data = options.mismatch ? b : fixture;
    return route.fulfill({
      json: data.results.find((result) => url.endsWith(result.kind)),
    });
  });
}

// A saved conversation whose four turns ask about the dataset, so each panel
// is drawn in an answer exactly as the agent would draw it.
const plans = [
  ["overview", ["overview"], "dataset_summary"],
  ["sources", ["sources"], "sources"],
  ["coverage", ["coverage"], "coverage"],
  ["freshness", ["freshness", "embeddings"], "preparation"],
] as const;
const conversationId = "7d9f0c1e-3b2a-4c5d-8e6f-0a1b2c3d4e5f";
function datasetConversation(fixture: typeof a) {
  const id = fixture.selection.publication_id;
  return {
    schema_version: "voc-conversation-v1",
    conversation_id: conversationId,
    publication_id: id,
    created_at: "2026-01-01T00:00:00Z",
    turns: plans.map(([intent, kinds, component], index) => {
      const results = kinds.map((kind) =>
        fixture.results.find((result) => result.kind === kind)!,
      );
      return {
        request_id: `00000000-0000-4000-8000-00000000000${index}`,
        question: `Pregunta ${index + 1} sobre los datos`,
        status: "completed",
        planner_mode: "fixture",
        answer: {
          schema_version: "voc-answer-v1",
          publication_id: id,
          plan: { intent, platform: null },
          text: `Respuesta ${index + 1}.`,
          results,
          views: [{ component, result_ids: results.map((r) => r.result_id) }],
          warnings: [],
          trace: results.map((result) => ({
            operation: result.kind,
            publication_id: id,
            result_id: result.result_id,
            duration_ms: 1,
          })),
          usage: {
            mode: "fixture",
            model: null,
            requests: 0,
            prompt_tokens: null,
            completion_tokens: null,
            duration_ms: 0,
          },
        },
        error: null,
        created_at: "2026-01-01T00:00:00Z",
        finished_at: "2026-01-01T00:00:01Z",
      };
    }),
  };
}
async function answers(page: Page, fixture: typeof a = a) {
  const conversation = datasetConversation(fixture);
  await page.route("**/api/v1/conversations/*", (route) =>
    route.fulfill({ json: conversation }),
  );
}

test("draws the dataset panels in answers with exact counts, unknown dates and evidence", async ({
  page,
}) => {
  await serve(page);
  await answers(page);
  await page.goto(`/?conversation=${conversationId}`);
  await expect(
    page.getByRole("heading", { name: "Conversa con los datos" }),
  ).toBeVisible();
  await expect(page.getByText(/Ejemplo sintético ·/)).toBeVisible();
  const summary = page.getByRole("region", { name: "Una vista del dataset" });
  await expect(summary.getByText("12", { exact: true })).toBeVisible();
  const sources = page.getByRole("region", { name: "De dónde vienen" });
  await expect(
    sources.getByRole("row", { name: "Google Play 8 1" }),
  ).toBeVisible();
  await expect(
    sources.getByRole("row", { name: "App Store 4 0" }),
  ).toBeVisible();
  await sources.getByText("Ver evidencia", { exact: true }).click();
  await expect(sources.getByText(a.results[1].result_id)).toBeVisible();
  await page.getByText("Ver valores por mes", { exact: true }).click();
  await expect(
    page.getByRole("table", { name: "Cantidad exacta de registros por mes" }),
  ).toBeVisible();
  await expect(page.getByText("No informados", { exact: true })).toBeVisible();
  await expect(page.getByText("No disponible", { exact: true })).toHaveCount(2);
});

test("the page shows the active publication, not the dataset panels", async ({
  page,
}) => {
  await serve(page);
  await page.goto("/");
  await expect(
    page.getByText(a.selection.publication_id, { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".voc-suggestion")).toHaveCount(3);
  for (const name of [
    "Una vista del dataset",
    "De dónde vienen",
    "Cómo se distribuyen en el tiempo",
    "Qué sabemos de su preparación",
  ]) {
    await expect(page.getByRole("region", { name, exact: true })).toHaveCount(
      0,
    );
  }
});

test("refresh selects B without mixing the initial A responses", async ({
  page,
}) => {
  let selected = a;
  const queried: string[] = [];
  await page.route("**/api/v1/publications/**", async (route) => {
    const url = route.request().url();
    if (url.endsWith("/current")) {
      const current = selected;
      selected = b;
      return route.fulfill({ json: current.selection });
    }
    queried.push(url);
    const data = url.includes(a.selection.publication_id) ? a : b;
    return route.fulfill({
      json: data.results.find((result) => url.endsWith(result.kind)),
    });
  });
  await page.goto("/");
  await expect(
    page.getByText(a.selection.publication_id, { exact: true }),
  ).toBeVisible();
  await expect.poll(() => queried.length).toBe(5);
  expect(queried.every((url) => url.includes(a.selection.publication_id))).toBe(
    true,
  );
  await page.getByRole("button", { name: "Actualizar vista" }).click();
  await expect(
    page.getByText(b.selection.publication_id, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(a.selection.publication_id, { exact: true }),
  ).toHaveCount(0);
  await expect.poll(() => queried.length).toBe(10);
  expect(
    queried.slice(5).every((url) => url.includes(b.selection.publication_id)),
  ).toBe(true);
});

test("shows missing publication separately from query failure and supports retry", async ({
  page,
}) => {
  await serve(page, { missing: true });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Aún no hay datos publicados" }),
  ).toBeVisible();
  await page.unrouteAll();
  await serve(page, { failSources: true });
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(
    page.getByRole("heading", { name: "No pudimos mostrar los datos" }),
  ).toBeVisible();
  await expect(
    page.getByText(a.selection.publication_id, { exact: true }),
  ).toBeVisible();
  await page.unrouteAll();
  await serve(page);
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Actualizar vista" }),
  ).toBeEnabled();
});

test("rejects mixed publications with an alert", async ({ page }) => {
  await serve(page, { mismatch: true });
  await page.goto("/");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "No pudimos mostrar los datos" }),
  ).toBeVisible();
});

test("zero rows is a valid dataset, distinct from missing dates and errors", async ({
  page,
}) => {
  await serve(page, { zero: true });
  await answers(page, served);
  await page.goto(`/?conversation=${conversationId}`);
  await expect(
    page.getByText(
      "Esta publicación está disponible y contiene cero registros.",
    ),
  ).toBeVisible();
  await expect(
    page.getByText("No hay registros con fecha utilizable para mostrar meses."),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("loading has a deadline, retry works, and a completed load does not later time out", async ({
  page,
}) => {
  await page.clock.install();
  await page.route(
    "**/api/v1/publications/**",
    (route) => new Promise<void>(() => {}),
  );
  await page.goto("/");
  await expect(page.getByRole("status")).toBeVisible();
  await page.clock.fastForward(21000);
  await expect(
    page.getByText("La consulta tardó demasiado. Intenta de nuevo."),
  ).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await serve(page);
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(
    page.getByRole("button", { name: "Actualizar vista" }),
  ).toBeEnabled();
  await page.clock.fastForward(21000);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByText(a.selection.publication_id, { exact: true }),
  ).toBeVisible();
});

test("mobile layout keeps content in the viewport and supports keyboard disclosure", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await serve(page);
  await answers(page);
  await page.goto(`/?conversation=${conversationId}`);
  await expect(
    page.getByRole("region", { name: "Cómo se distribuyen en el tiempo" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await expect(page.getByLabel("Tu pregunta")).toBeInViewport();
  const disclosure = page.getByText("Ver valores por mes", { exact: true });
  await disclosure.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("table", { name: "Cantidad exacta de registros por mes" }),
  ).toBeVisible();
});
