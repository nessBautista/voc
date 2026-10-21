import { expect, test, type Page } from "@playwright/test";
import a from "../fixtures/example_a.json" with { type: "json" };
import b from "../fixtures/example_b.json" with { type: "json" };

// These specs cover the dashboard itself; the server session is local mode.
test.beforeEach(async ({ page }) => {
  await page.route("**/auth/me", (route) =>
    route.fulfill({ json: { email: "developer@localhost", mode: "local" } }),
  );
});

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

test("renders exact counts, unknown dates and expandable evidence", async ({
  page,
}) => {
  await serve(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Estado de los datos" }),
  ).toBeVisible();
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
  await expect(page.getByText(/Ejemplo sintético ·/)).toBeVisible();
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
    page
      .getByRole("region", { name: "Una vista del dataset" })
      .getByText("12", { exact: true }),
  ).toBeVisible();
  expect(queried).toHaveLength(5);
  expect(queried.every((url) => url.includes(a.selection.publication_id))).toBe(
    true,
  );
  await page.getByRole("button", { name: "Actualizar vista" }).click();
  await expect(
    page
      .getByRole("region", { name: "Una vista del dataset" })
      .getByText("13", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "De dónde vienen" })
      .getByRole("row", { name: "App Store 5 0" }),
  ).toBeVisible();
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
  await expect(
    page.getByRole("region", { name: "De dónde vienen" }),
  ).toHaveCount(0);
  await page.unrouteAll();
  await serve(page);
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(
    page.getByRole("region", { name: "De dónde vienen" }),
  ).toBeVisible();
});

test("rejects mixed publications without displaying cards", async ({
  page,
}) => {
  await serve(page, { mismatch: true });
  await page.goto("/");
  await expect(page.getByRole("alert")).toBeVisible();
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

test("zero rows is a valid dataset, distinct from missing dates and errors", async ({
  page,
}) => {
  await serve(page, { zero: true });
  await page.goto("/");
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
    page.getByRole("region", { name: "Una vista del dataset" }),
  ).toBeVisible();
  await page.clock.fastForward(21000);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Una vista del dataset" }),
  ).toBeVisible();
});

test("mobile layout keeps content in the viewport and supports keyboard disclosure", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await serve(page);
  await page.goto("/");
  await expect(
    page.getByRole("region", { name: "Cómo se distribuyen en el tiempo" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const disclosure = page.getByText("Ver valores por mes", { exact: true });
  await disclosure.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("table", { name: "Cantidad exacta de registros por mes" }),
  ).toBeVisible();
});
