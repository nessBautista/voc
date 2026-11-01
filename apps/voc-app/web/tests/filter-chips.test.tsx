import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FilterBar } from "../src/components/analysis/FilterBar";
import { allFilters, type Filters } from "../src/analysis";

describe("filter chips", () => {
  it("mark the chips that are on with a check, and only those", () => {
    const filters = {
      ...allFilters(),
      platforms: new Set(["app_store"]),
      labels: new Set(["negative"]),
    } as Filters;
    const page = renderToString(
      <FilterBar
        filters={filters}
        months={["2025-09", "2026-09"]}
        onChange={() => {}}
        onReset={() => {}}
      />,
    );
    const chips = [
      ...page.matchAll(/<button[^>]*class="voc-chip"[^>]*>([^]*?)<\/button>/g),
    ];
    const on = chips.filter((chip) => chip[0].includes('aria-pressed="true"'));
    const off = chips.filter((chip) =>
      chip[0].includes('aria-pressed="false"'),
    );
    expect(on.length).toBeGreaterThan(0);
    expect(off.length).toBeGreaterThan(0);
    for (const chip of on) expect(chip[1]).toContain('class="voc-icon"');
    for (const chip of off) expect(chip[1]).not.toContain('class="voc-icon"');
    const appStore = on.find((chip) => chip[1].includes("App Store"));
    expect(appStore).toBeDefined();
    expect(off.find((chip) => chip[1].includes("Google Play"))).toBeDefined();
  });
});
