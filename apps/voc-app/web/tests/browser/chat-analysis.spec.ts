import { expect, test } from "@playwright/test";
import v2 from "../fixtures/example_v2.json" with { type: "json" };
import saved from "../fixtures/conversation_v2.json" with { type: "json" };
import research from "../fixtures/conversation_research.json" with { type: "json" };

// The chat on a version 2 package: the answer's blocks are the dashboard's components.
test.beforeEach(async ({ page }) => {
  await page.route("**/auth/me", (route) =>
    route.fulfill({ json: { email: "developer@localhost", mode: "local" } }),
  );
  await page.route("**/api/v1/publications/**", (route) => {
    const url = new URL(route.request().url());
    const kind = url.pathname.split("/").pop()!;
    return route.fulfill({
      json: url.pathname.endsWith("/current")
        ? v2.selection
        : v2.results.find(
            (result) => result.kind === kind && !("selection" in result),
          ),
    });
  });
});

test("suggests the six questions and answers mood with the ring and the platforms", async ({
  page,
}) => {
  const current = structuredClone(saved);
  await page.route("**/api/v1/ask", (route) => {
    const payload = route.request().postDataJSON();
    // The saved first turn answers the posted question under the posted request id.
    const turn = structuredClone(saved.turns[0]);
    turn.request_id = payload.request_id;
    turn.question = payload.question;
    current.turns = [turn];
    const running = structuredClone(current);
    Object.assign(running.turns[0], {
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
  await expect(
    chat.getByRole("button", { name: "¿Cuál es la queja principal?" }),
  ).toBeVisible();
  await expect(chat.locator(".voc-suggestion")).toHaveCount(6);
  // An empty conversation starts with the composer under the title, larger.
  await expect(chat.locator(".voc-composer.is-start")).toBeVisible();
  await expect(
    chat.getByRole("button", { name: /^Ánimo general/ }),
  ).toBeVisible();
  await chat
    .getByRole("button", {
      name: "¿Cómo se sienten, en general, quienes dejaron reseñas?",
    })
    .click();
  await chat.getByRole("button", { name: "Preguntar", exact: true }).click();
  // The answer's labelled paragraphs are drawn as sections.
  await expect(
    chat.getByRole("heading", { name: "Qué dicen los datos" }),
  ).toBeVisible();
  await expect(
    chat.getByRole("heading", { name: "Interpretación" }),
  ).toBeVisible();
  await expect(
    chat.getByRole("heading", { name: "Lo que no se puede afirmar" }),
  ).toBeVisible();
  await expect(
    chat.getByRole("region", { name: "Ánimo general" }),
  ).toBeVisible();
  await expect(
    chat
      .getByRole("region", { name: "Por plataforma" })
      .getByText("Google Play"),
  ).toBeVisible();
  await chat.getByText("Ver consultas y uso").click();
  await expect(chat.getByText(/Intención: mood\./)).toBeVisible();
  await expect(chat.locator(".voc-answer-foot li").first()).toContainText(
    "summary",
  );
  // Each block is the dashboard's panel, with its evidence drawer.
  await expect(chat.getByText("Ver evidencia", { exact: true })).toHaveCount(2);
});

test("research mode sends the mode and renders the steps with their blocks", async ({
  page,
}) => {
  const current = structuredClone(research);
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v1/ask", (route) => {
    const payload = route.request().postDataJSON();
    requests.push(payload);
    const turn = structuredClone(research.turns[0]);
    turn.request_id = payload.request_id;
    turn.question = payload.question;
    current.turns = [turn];
    return route.fulfill({ status: 202, json: current });
  });
  await page.route("**/api/v1/conversations/*", (route) =>
    route.fulfill({ json: current }),
  );
  await page.goto("/");
  const chat = page.getByRole("region", { name: "Conversa con los datos" });
  await chat
    .getByRole("button", { name: "Investigación", exact: true })
    .click();
  await expect(
    chat.getByText(/hasta seis consultas encadenadas/),
  ).toBeVisible();
  // Research is marked on the switch and on the composer's border.
  await expect(chat.locator(".voc-composer")).toHaveClass(/is-research/);
  await chat.getByLabel("Tu pregunta").fill(research.turns[0].question);
  await chat.getByRole("button", { name: "Preguntar", exact: true }).click();
  // A research answer is a report: one block per step, each with a visual made
  // for it, and the step's full text and panel folded.
  await expect(
    chat.getByRole("heading", { name: "Paso 1 · Evolución mensual" }),
  ).toBeVisible();
  await expect(
    chat.getByRole("heading", { name: "Paso 2 · Queja principal" }),
  ).toBeVisible();
  await expect(chat.getByText(/Investigación · 2 pasos/)).toBeVisible();
  await expect(
    chat.getByRole("img", { name: /Proporción de reseñas negativas por mes/ }),
  ).toBeVisible();
  await expect(chat.getByText("Tema destacado", { exact: true })).toHaveCount(
    0,
  );
  const details = chat.getByText("Ver el detalle y el panel completo");
  await expect(details).toHaveCount(2);
  await details.nth(0).click();
  await expect(
    chat.getByRole("region", { name: "Sentimiento en el tiempo" }),
  ).toBeVisible();
  await details.nth(1).click();
  await expect(
    chat.getByRole("region", { name: "Temas por volumen y sentimiento" }),
  ).toBeVisible();
  expect(requests[0].mode).toBe("research");
});
