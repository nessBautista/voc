import { expect, test, type Page } from "@playwright/test";
import v2 from "../fixtures/example_v2.json" with { type: "json" };

// The analysis dashboard on a version 2 package: every route is stubbed from the
// shared fixture, so the browser, the build and the stubs live in one container.
type Served = (typeof v2.results)[number];
const requested: string[] = [];

function pick(url: URL): Served | undefined {
  const kind = url.pathname.split("/").pop()!;
  const params = url.searchParams;
  const wants = {
    platforms: params.get("platforms")?.split(",") ?? null,
    sort: params.get("sort") ?? (kind === "topic_list" ? "volume" : null),
    topic_id: params.has("topic_id") ? Number(params.get("topic_id")) : null,
    page: params.has("page")
      ? Number(params.get("page"))
      : kind === "reviews"
        ? 1
        : null,
  };
  return v2.results.find((result) => {
    if (result.kind !== kind) return false;
    const selection = (result.selection ?? {}) as Record<string, unknown>;
    if (!("selection" in result)) return true;
    const platforms = (selection.platforms as string[] | null) ?? null;
    return (
      JSON.stringify(platforms) === JSON.stringify(wants.platforms) &&
      (selection.sort ?? null) === wants.sort &&
      (selection.topic_id ?? null) === wants.topic_id &&
      (selection.page ?? null) === wants.page
    );
  });
}

async function serve(page: Page) {
  requested.length = 0;
  await page.route("**/auth/me", (route) =>
    route.fulfill({ json: { email: "developer@localhost", mode: "local" } }),
  );
  await page.route("**/api/v1/publications/**", async (route) => {
    const url = new URL(route.request().url());
    requested.push(url.pathname + url.search);
    if (url.pathname.endsWith("/current"))
      return route.fulfill({ json: v2.selection });
    const found = pick(url);
    if (!found)
      return route.fulfill({
        status: 422,
        json: {
          error: {
            code: "invalid_selection",
            message: `no fixture for ${url.search}`,
          },
        },
      });
    return route.fulfill({ json: found });
  });
}

test.beforeEach(async ({ page }) => serve(page));

test("renders tiles, topics, timeline, platforms and mood from a v2 package", async ({
  page,
}) => {
  await page.goto("/dashboard/");
  await expect(
    page.getByRole("heading", { name: "Qué dicen las reseñas" }),
  ).toBeVisible();
  const tiles = page.getByRole("region", {
    name: "Indicadores de la selección",
  });
  await expect(tiles.getByText("60", { exact: true })).toBeVisible();
  await expect(tiles.getByText("de 60 publicadas")).toBeVisible();
  await expect(tiles.getByText("34%", { exact: true })).toBeVisible();
  await expect(
    tiles.getByRole("button", {
      name: "Lentitud al abrir y cargar la aplicación",
    }),
  ).toBeVisible();
  await expect(tiles.getByText(/al menos 49%/)).toBeVisible();
  const topics = page.getByRole("region", {
    name: "Temas por volumen y sentimiento",
  });
  await expect(
    topics.getByRole("button", { name: /Tema 0 · Una funcionalidad/ }),
  ).toBeVisible();
  await expect(
    topics.getByRole("button", { name: /Sin asignar/ }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Sentimiento en el tiempo" })
      .getByRole("img"),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Por plataforma" })
      .getByText("Google Play"),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Ánimo general" })
      .getByText("29%", { exact: true }),
  ).toBeVisible();
  // The dataset panels are still there, collapsed, and the conversation is on "/".
  await expect(
    page.getByRole("region", { name: "Una vista del dataset" }),
  ).toBeHidden();
  await expect(
    page.getByRole("heading", { name: "Conversa con los datos" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Conversar con los datos" }),
  ).toBeVisible();
  await page.getByText("Alcance y procedencia de la publicación").click();
  await expect(
    page.getByRole("region", { name: "Una vista del dataset" }),
  ).toBeVisible();
  expect(requested.filter((u) => u.includes("/summary"))).toHaveLength(1);
});

test("sorting, filtering and resetting send the canonical selection", async ({
  page,
}) => {
  await page.goto("/dashboard/");
  const topics = page.getByRole("region", {
    name: "Temas por volumen y sentimiento",
  });
  await topics.getByRole("button", { name: "Negativas", exact: true }).click();
  await expect(topics.getByRole("button", { name: /Tema 0/ })).toBeVisible();
  await expect
    .poll(() => requested.some((u) => u.endsWith("/topic_list?sort=negative")))
    .toBe(true);
  // The legend is a list too; the topic rows have their own class.
  const first = topics.locator(".topic-rows li").first();
  await expect(first).toContainText("Tema 0");
  await topics.getByRole("button", { name: "Volumen", exact: true }).click();
  // Turning off three platforms leaves Google Play; the query names it.
  const filters = page.getByRole("region", { name: "Filtros" });
  for (const name of ["App Store", "X", "YouTube"]) {
    await filters.getByRole("button", { name, exact: true }).click();
  }
  await expect
    .poll(() => requested.some((u) => u.includes("platforms=google_play")))
    .toBe(true);
  await expect(
    page
      .getByRole("region", { name: "Indicadores de la selección" })
      .getByText("36", { exact: true }),
  ).toBeVisible();
  // The last active chip cannot be turned off.
  await filters
    .getByRole("button", { name: "Google Play", exact: true })
    .click();
  await expect(
    filters.getByRole("button", { name: "Google Play", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Restablecer" }).click();
  await expect(
    page
      .getByRole("region", { name: "Indicadores de la selección" })
      .getByText("60", { exact: true }),
  ).toBeVisible();
});

test("drilling into a topic shows its reviews with paging and evidence", async ({
  page,
}) => {
  await page.goto("/dashboard/");
  const topics = page.getByRole("region", {
    name: "Temas por volumen y sentimiento",
  });
  await topics
    .getByRole("button", { name: /Tema 0 · Una funcionalidad/ })
    .click();
  const detail = page.getByRole("region", {
    name: "Lentitud al abrir y cargar la aplicación",
  });
  await expect(detail).toBeVisible();
  await expect(
    detail.getByText(
      "10 de 10 reseñas cumplen los filtros · negativas 8 · neutras 1 · positivas 1",
    ),
  ).toBeVisible();
  await expect(
    detail.getByText("Descripción ficticia del tema 0 para pruebas."),
  ).toBeVisible();
  await expect(
    detail.getByRole("list", { name: "Palabras clave" }).getByText("lenta"),
  ).toBeVisible();
  await expect(detail.getByText("10 reseñas · página 1 de 2")).toBeVisible();
  await expect(detail.locator("blockquote")).toHaveCount(6);
  await expect(detail.getByText("1 ★").first()).toBeVisible();
  await detail.getByRole("button", { name: "Siguiente →" }).click();
  await expect(detail.getByText("10 reseñas · página 2 de 2")).toBeVisible();
  await expect(detail.locator("blockquote")).toHaveCount(4);
  await detail.getByText("Ver evidencia", { exact: true }).click();
  await expect(detail.getByText("topic_detail", { exact: true })).toBeVisible();
  await detail.getByRole("button", { name: "Cerrar" }).click();
  await expect(
    page.getByRole("region", {
      name: "Lentitud al abrir y cargar la aplicación",
    }),
  ).toHaveCount(0);
});

test("a refused selection is reported without losing the page", async ({
  page,
}) => {
  await page.goto("/dashboard/");
  await expect(
    page.getByRole("region", { name: "Indicadores de la selección" }),
  ).toBeVisible();
  await page
    .getByRole("region", { name: "Filtros" })
    .getByRole("button", { name: "X", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "La selección no es válida",
  );
  await page.getByRole("button", { name: "Restablecer" }).click();
  await expect(
    page.getByRole("region", { name: "Indicadores de la selección" }),
  ).toBeVisible();
});

test("mobile layout keeps the analysis in the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/dashboard/");
  await expect(
    page.getByRole("region", { name: "Indicadores de la selección" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("the data page links to the panel and the panel refuses a v1 package", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Estado de los datos" }),
  ).toBeVisible();
  // Exact: "Abrir el panel de análisis" below the heading also carries the name.
  await expect(
    page.getByRole("link", { name: "Panel de análisis", exact: true }),
  ).toHaveAttribute("href", "/dashboard/");
  await expect(
    page.getByRole("link", { name: "Abrir el panel de análisis" }),
  ).toHaveAttribute("href", "/dashboard/");
  await expect(
    page.getByRole("region", { name: "Indicadores de la selección" }),
  ).toHaveCount(0);
  // A later route wins: only the selection changes, the kinds still come from the fixture.
  await page.route("**/api/v1/publications/current", (route) =>
    route.fulfill({
      json: {
        ...v2.selection,
        schema_version: "voc-data-state-v1",
        capabilities: [
          "overview",
          "sources",
          "coverage",
          "freshness",
          "embeddings",
        ],
      },
    }),
  );
  await page.goto("/dashboard/");
  await expect(
    page.getByRole("heading", {
      name: "Esta publicación no incluye temas ni sentimiento",
    }),
  ).toBeVisible();
});
