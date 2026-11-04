import { chromium, expect as defaultExpect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const origin = "http://127.0.0.1:8092";
const out = root + "/data/reports/difusion/demo";
mkdirSync(out, { recursive: true });
const libs = root + "/data/browser-libs";
const browser = await chromium.launch({
  headless: true,
  env: existsSync(libs + "/fonts.conf")
    ? {
        ...process.env,
        FONTCONFIG_FILE: libs + "/fonts.conf",
        LD_LIBRARY_PATH:
          libs +
          "/usr/lib/aarch64-linux-gnu:" +
          libs +
          "/lib/aarch64-linux-gnu",
      }
    : process.env,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("response", (r) => {
  if (r.status() >= 400) errors.push(r.status() + " " + r.url());
});
const results = {};
const expect = defaultExpect.configure({ timeout: 30000 });
const get = async (path) => {
  const r = await page.request.get(origin + path);
  expect(r.ok()).toBeTruthy();
  return r.json();
};
try {
  for (const system of ["base", "alternative"]) {
    execFileSync("bash", ["scripts/difusion-demo.sh", "select", system], {
      cwd: root,
    });
    const selection = await get("/api/v1/publications/current");
    const api = "/api/v1/publications/" + selection.publication_id;
    const summary = (await get(api + "/summary")).data;
    await page.goto(origin + "/dashboard/");
    await expect(
      page.getByRole("heading", { name: "Qué dicen las reseñas" }),
    ).toBeVisible();
    const tiles = page.getByRole("region", {
      name: "Indicadores de la selección",
    });
    await expect(tiles.locator(".voc-kpi-value").first()).toHaveText(
      new Intl.NumberFormat("es-MX").format(summary.reviews_total),
    );
    await page.screenshot({
      path: out + "/" + system + "-dashboard.png",
      fullPage: false,
    });
    const topics = page.getByRole("region", {
      name: "Temas por volumen y sentimiento",
    });
    await topics.getByRole("button", { name: /Tema 0 ·/ }).click();
    await page.getByRole("tab", { name: /Reseñas/ }).click();
    const feed = page.getByRole("region", { name: "Reseñas del tema" });
    await expect(feed.locator("blockquote")).toHaveCount(6);
    const data = (await get(api + "/reviews?topic_id=0&page_size=6")).data;
    const normalize = (s) => s.replace(/\s+/g, " ").trim();
    for (let i = 0; i < data.items.length; i++) {
      expect(
        normalize(await feed.locator("blockquote").nth(i).innerText()),
      ).toBe(normalize(data.items[i].text));
    }
    await page.screenshot({
      path: out + "/" + system + "-topic-reviews.png",
      fullPage: false,
    });
    await feed.getByRole("button", { name: "Siguiente →" }).click();
    await expect(feed).toContainText("página 2");
    const data2 = (await get(api + "/reviews?topic_id=0&page_size=6&page=2"))
      .data;
    await expect(feed.locator("blockquote").first()).toHaveText(
      data2.items[0].text,
    );
    await feed.getByRole("button", { name: /^Negativas/ }).click();
    await expect(tiles.locator(".voc-kpi-value").first()).toHaveText(
      new Intl.NumberFormat("es-MX").format(summary.negative.count),
    );
    await page.getByRole("button", { name: "Restablecer" }).click();
    await expect(tiles.locator(".voc-kpi-value").first()).toHaveText(
      new Intl.NumberFormat("es-MX").format(summary.reviews_total),
    );
    // Reload closes detail; platform filtering is checked against the real API.
    await page.reload();
    const filters = page.getByRole("region", { name: "Filtros" });
    for (const name of ["App Store", "X", "YouTube"])
      await filters.getByRole("button", { name, exact: true }).click();
    const play = (await get(api + "/summary?platforms=google_play")).data;
    await expect(tiles.locator(".voc-kpi-value").first()).toHaveText(
      String(play.reviews_in_view),
    );
    results[system] = {
      publication_id: selection.publication_id,
      reviews: summary.reviews_total,
      topic_0_full_texts: true,
      pagination: true,
      sentiment_filter: true,
      platform_filter: true,
    };
  }
  expect(errors).toEqual([]);
  writeFileSync(
    out + "/browser-verification.json",
    JSON.stringify({ results, errors, model_requests: 0 }, null, 2) + "\n",
  );
  console.log(JSON.stringify(results, null, 2));
} catch (error) {
  console.error(errors);
  await page.screenshot({ path: out + "/failure.png", fullPage: false });
  console.error((await page.locator("body").innerText()).slice(0, 2000));
  throw error;
} finally {
  execFileSync("bash", ["scripts/difusion-demo.sh", "select", "alternative"], {
    cwd: root,
  });
  await browser.close();
}
