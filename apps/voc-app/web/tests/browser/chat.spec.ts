import { expect, test, type Page } from "@playwright/test";
import a from "../fixtures/example_a.json" with { type: "json" };
import saved from "../fixtures/conversation.json" with { type: "json" };

// These specs cover the dashboard itself; the server session is local mode.
test.beforeEach(async ({ page }) => {
  await page.route("**/auth/me", (route) =>
    route.fulfill({ json: { email: "developer@localhost", mode: "local" } }),
  );
});

async function dashboard(page: Page) {
  await page.route("**/api/v1/publications/**", (route) =>
    route.fulfill({
      json: route.request().url().endsWith("current")
        ? a.selection
        : a.results.find((result) =>
            route.request().url().endsWith(result.kind),
          ),
    }),
  );
}

test("asks, polls, reuses cards, follows up and reloads the conversation", async ({
  page,
}) => {
  await dashboard(page);
  let current = structuredClone(saved);
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v1/ask", (route) => {
    const payload = route.request().postDataJSON();
    requests.push(payload);
    const turn = structuredClone(saved.turns[0]);
    turn.request_id = payload.request_id;
    turn.question = payload.question;
    if (requests.length > 1) {
      turn.answer.plan.platform = "app_store";
      turn.answer.text =
        "La publicación consultada contiene 4 registros de App Store.";
      current.turns.push(turn);
    } else current.turns = [turn];
    const running = structuredClone(current);
    Object.assign(running.turns.at(-1)!, {
      status: "running",
      answer: null,
      finished_at: null,
    });
    return route.fulfill({ status: 202, json: running });
  });
  await page.route("**/api/v1/conversations/*", (route) =>
    route.fulfill({ json: current }),
  );
  await page.goto("/");
  const chat = page.getByRole("region", { name: "Conversa con los datos" });
  await chat.getByLabel("Tu pregunta").fill(saved.turns[0].question);
  await chat.getByRole("button", { name: "Preguntar", exact: true }).click();
  await expect(chat.getByText(/8 registros de Google Play/)).toBeVisible();
  await expect(
    chat.getByRole("row", { name: "Google Play 8 1" }),
  ).toBeVisible();
  await expect(
    chat.getByText("Planificador de prueba · Sin llamadas a un modelo"),
  ).toBeVisible();
  await chat.getByLabel("Tu pregunta").fill("¿y de App Store?");
  await chat.getByRole("button", { name: "Preguntar", exact: true }).click();
  await expect(chat.getByText(/4 registros de App Store/)).toBeVisible();
  expect(requests[1].conversation_id).toBe(saved.conversation_id);
  expect(requests[1].request_id).not.toBe(requests[0].request_id);
  await page.reload();
  await expect(chat.getByText(/4 registros de App Store/)).toBeVisible();
  await expect(chat.getByText(/8 registros de Google Play/)).toBeVisible();
  await expect(page).toHaveURL(
    new RegExp(`conversation=${saved.conversation_id}`),
  );
});

test("recovers a lost first POST with the same request ID after reload", async ({
  page,
}) => {
  await dashboard(page);
  let current = structuredClone(saved);
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v1/ask", (route) => {
    const payload = route.request().postDataJSON();
    requests.push(payload);
    current.turns[0].request_id = payload.request_id;
    current.turns[0].question = payload.question;
    if (requests.length === 1) return route.abort("failed");
    return route.fulfill({ json: current });
  });
  await page.route("**/api/v1/conversations/*", (route) =>
    route.fulfill({ json: current }),
  );
  await page.goto("/");
  await page.getByLabel("Tu pregunta").fill("Google Play");
  await page.getByRole("button", { name: "Preguntar", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Reintentar el mismo envío" }),
  ).toBeEnabled();
  await page.reload();
  await page.getByRole("button", { name: "Reintentar el mismo envío" }).click();
  await expect(page.getByText(/8 registros de Google Play/)).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);
});

test("failed model turn leaves dashboard usable and permits a new question", async ({
  page,
}) => {
  await dashboard(page);
  const current = structuredClone(saved);
  Object.assign(current.turns[0], {
    planner_mode: "model",
    status: "failed",
    answer: null,
    error: {
      code: "model_unavailable",
      message: "El modelo no está disponible.",
    },
  });
  await page.route("**/api/v1/conversations/*", (route) =>
    route.fulfill({ json: current }),
  );
  await page.goto(`/?conversation=${saved.conversation_id}`);
  const chat = page.getByRole("region", { name: "Conversa con los datos" });
  await expect(chat.getByText("El modelo no está disponible.")).toBeVisible();
  await expect(chat.getByLabel("Tu pregunta")).toBeEnabled();
  await expect(
    page
      .getByRole("region", { name: "Una vista del dataset" })
      .getByText("12", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Actualizar vista" }).click();
  await expect(
    page
      .getByRole("region", { name: "Una vista del dataset" })
      .getByText("12", { exact: true }),
  ).toBeVisible();
  await chat.getByRole("button", { name: "Nueva conversación" }).click();
  await expect(page).not.toHaveURL(/conversation=/);
});

test("rejects mismatched conversation evidence without rendering a false card", async ({
  page,
}) => {
  await dashboard(page);
  const current = structuredClone(saved);
  current.turns[0].answer.views[0].result_ids = ["0".repeat(64)];
  await page.route("**/api/v1/conversations/*", (route) =>
    route.fulfill({ json: current }),
  );
  await page.goto(`/?conversation=${saved.conversation_id}`);
  const chat = page.getByRole("region", { name: "Conversa con los datos" });
  await expect(chat.getByRole("alert")).toBeVisible();
  await expect(chat.getByText(/8 registros de Google Play/)).toHaveCount(0);
  await expect(
    chat.getByRole("region", { name: "De dónde vienen" }),
  ).toHaveCount(0);
});

for (const width of [560, 742]) {
  test(`half-screen conversation stays usable at ${width} points`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 650 });
    await dashboard(page);
    const current = structuredClone(saved);
    await page.route("**/api/v1/conversations/*", (route) =>
      route.fulfill({ json: current }),
    );
    await page.route("**/api/v1/ask", (route) => {
      const payload = route.request().postDataJSON();
      const turn = structuredClone(current.turns[0]);
      turn.request_id = payload.request_id;
      turn.question = payload.question;
      turn.answer.text =
        "La publicación consultada contiene 4 registros de App Store.";
      current.turns.push(turn);
      return route.fulfill({ json: current });
    });
    await page.goto(`/?conversation=${saved.conversation_id}`);
    const chat = page.getByRole("region", { name: "Conversa con los datos" });
    await expect(chat.getByText(/8 registros de Google Play/)).toBeVisible();
    await page.getByRole("button", { name: "Ir al chat", exact: true }).click();
    await expect(chat.getByLabel("Tu pregunta")).toBeFocused();
    await chat.getByLabel("Tu pregunta").fill("¿y de App Store?");
    await chat.getByRole("button", { name: "Preguntar", exact: true }).click();
    await expect(chat.getByText(/4 registros de App Store/)).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    const table = chat.getByRole("row", { name: "Google Play 8 1" }).first();
    await table.scrollIntoViewIfNeeded();
    await expect(table).toBeVisible();
    await page.reload();
    await expect(chat.getByText(/4 registros de App Store/)).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
  });
}
