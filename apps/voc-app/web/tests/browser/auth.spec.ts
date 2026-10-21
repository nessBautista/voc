import { expect, test, type Page } from "@playwright/test";
import a from "../fixtures/example_a.json" with { type: "json" };

async function publications(page: Page, status = 200) {
  const requests: string[] = [];
  await page.route("**/api/v1/publications/**", (route) => {
    requests.push(route.request().url());
    if (status !== 200)
      return route.fulfill({
        status,
        json: { error: { code: "sign_in_required" } },
      });
    return route.fulfill({
      json: route.request().url().endsWith("current")
        ? a.selection
        : a.results.find((result) =>
            route.request().url().endsWith(result.kind),
          ),
    });
  });
  return requests;
}

test("a signed-out visitor sees only the sign-in page", async ({ page }) => {
  await page.route("**/auth/me", (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: "sign_in_required" } },
    }),
  );
  const requests = await publications(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Inicia sesión para continuar" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Iniciar sesión" }),
  ).toHaveAttribute("href", "/auth/login");
  await expect(
    page.getByRole("heading", { name: "Estado de los datos" }),
  ).toHaveCount(0);
  expect(requests).toHaveLength(0);
});

test("a failed sign-in is explained on the sign-in page", async ({ page }) => {
  await page.route("**/auth/me", (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: "sign_in_required" } },
    }),
  );
  await page.goto("/?login_error=login_cancelled");
  await expect(page.getByRole("alert")).toContainText("no se completó");
});

test("a signed-in person sees the data, their email and can sign out", async ({
  page,
}) => {
  let signedIn = true;
  await page.route("**/auth/me", (route) =>
    signedIn
      ? route.fulfill({ json: { email: "a@example.com", mode: "cognito" } })
      : route.fulfill({ status: 401, json: { error: {} } }),
  );
  await page.route("**/auth/logout", (route) => {
    expect(route.request().method()).toBe("POST");
    signedIn = false;
    return route.fulfill({ json: { logout_url: "/?signed-out=1" } });
  });
  await publications(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Estado de los datos" }),
  ).toBeVisible();
  await expect(page.getByText("a@example.com")).toBeVisible();
  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page).toHaveURL(/signed-out=1/);
  await expect(
    page.getByRole("heading", { name: "Inicia sesión para continuar" }),
  ).toBeVisible();
});

test("an expired session returns the page to sign-in without a loop", async ({
  page,
}) => {
  await page.route("**/auth/me", (route) =>
    route.fulfill({ json: { email: "a@example.com", mode: "cognito" } }),
  );
  const requests = await publications(page, 401);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Inicia sesión para continuar" }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Tu sesión terminó");
  await page.waitForTimeout(500);
  expect(requests.length).toBeLessThanOrEqual(1);
});

test("local mode shows no sign-out control", async ({ page }) => {
  await page.route("**/auth/me", (route) =>
    route.fulfill({ json: { email: "developer@localhost", mode: "local" } }),
  );
  await publications(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Estado de los datos" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Cerrar sesión" })).toHaveCount(
    0,
  );
});
