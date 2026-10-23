import { describe, expect, it } from "vitest";
import v2 from "./fixtures/example_v2.json";
import {
  allFilters,
  analysisResultSchema,
  isDefault,
  parseAnalysisResult,
  selectionQuery,
  type Filters,
} from "../src/analysis";
import { resolveView } from "../src/components/registry";
import { parseSelection } from "../src/types";

const publicationId = v2.selection.publication_id;
const byKind = (
  kind: string,
  test: (selection: Record<string, unknown>) => boolean = () => true,
) =>
  v2.results.find(
    (result) =>
      result.kind === kind && test(result.selection as Record<string, unknown>),
  )!;

describe("selection query string", () => {
  it("is empty for the default filters and names only what is narrowed", () => {
    expect(selectionQuery(allFilters())).toBe("");
    const narrowed: Filters = {
      ...allFilters(),
      platforms: new Set(["x", "google_play"]),
      labels: new Set(["negative"]),
      monthFrom: "2026-05",
      search: "  tarda  ",
    };
    expect(selectionQuery(narrowed)).toBe(
      "?platforms=google_play%2Cx&labels=negative&month_from=2026-05&search=tarda",
    );
    expect(
      selectionQuery(allFilters(), { topic_id: 0, page: 2, page_size: 6 }),
    ).toBe("?topic_id=0&page=2&page_size=6");
    expect(selectionQuery(allFilters(), { sort: "negative" })).toBe(
      "?sort=negative",
    );
    expect(isDefault(allFilters())).toBe(true);
    expect(isDefault(narrowed)).toBe(false);
  });
  it("cuts a search to one hundred characters", () => {
    const long = { ...allFilters(), search: "x".repeat(150) };
    expect(selectionQuery(long)).toBe(`?search=${"x".repeat(100)}`);
  });
});

describe("analysis contracts", () => {
  it("accept every fixture result and reject a foreign publication or kind", () => {
    for (const result of v2.results.filter(
      (r) => r.schema_version === "voc-data-query-v2",
    )) {
      expect(parseAnalysisResult(result, publicationId).kind).toBe(result.kind);
    }
    expect(() =>
      parseAnalysisResult(
        byKind("summary"),
        "00000000-0000-4000-8000-000000000000",
      ),
    ).toThrow();
    expect(() =>
      parseAnalysisResult(byKind("summary"), publicationId, "timeline"),
    ).toThrow();
    expect(
      analysisResultSchema.safeParse({ ...byKind("summary"), extra: 1 })
        .success,
    ).toBe(false);
  });
  it("carry the selection that produced them", () => {
    const negative = byKind("topic_list", (s) => s.sort === "negative");
    expect(negative.selection).toMatchObject({
      sort: "negative",
      platforms: null,
    });
    expect(
      (negative.data as { topic_id: number }[]).map((row) => row.topic_id),
    ).toEqual([0, 3, 1, 4, 2, -1]);
    const page2 = byKind("reviews", (s) => s.page === 2);
    expect((page2.data as { page: number; pages: number }).page).toBe(2);
    const stores = byKind("summary", (s) => Array.isArray(s.platforms));
    expect((stores.data as { reviews_in_view: number }).reviews_in_view).toBe(
      36,
    );
  });
  it("accept the v2 selection with nine capabilities", () => {
    expect(parseSelection(v2.selection).schema_version).toBe(
      "voc-data-state-v2",
    );
  });
});

describe("registry", () => {
  it("resolves analysis components from v2 evidence and refuses mixed evidence", () => {
    const summary = byKind("summary", (s) => s.platforms === null);
    const detail = byKind("topic_detail");
    const reviews = byKind("reviews", (s) => s.page === 1);
    const resolved = resolveView(
      { component: "kpi_tiles", result_ids: [summary.result_id] },
      [summary],
      publicationId,
    );
    expect(resolved.results[0].kind).toBe("summary");
    expect(
      resolveView(
        {
          component: "topic_detail",
          result_ids: [detail.result_id, reviews.result_id],
        },
        [detail, reviews],
        publicationId,
      ).results,
    ).toHaveLength(2);
    expect(() =>
      resolveView(
        { component: "topic_detail", result_ids: [detail.result_id] },
        [detail],
        publicationId,
      ),
    ).toThrow();
    expect(() =>
      resolveView(
        { component: "kpi_tiles", result_ids: [byKind("overview").result_id] },
        [byKind("overview")],
        publicationId,
      ),
    ).toThrow();
  });
});
