import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import a from "./fixtures/example_a.json";
import b from "./fixtures/example_b.json";
import { parseResult, parseSelection, validateSnapshot } from "../src/types";
import { dashboardViews, resolveView, View } from "../src/components/registry";

const snapshot = () => ({
  selection: parseSelection(a.selection),
  results: a.results.map((result) =>
    parseResult(result, a.selection.publication_id),
  ),
});

describe("published response validation", () => {
  it("accepts all five actual library responses", () => {
    expect(validateSnapshot(snapshot()).results).toHaveLength(5);
  });
  it.each(["publication_id", "kind", "schema_version", "result_id"])(
    "rejects an incorrect %s",
    (field) => {
      expect(() =>
        parseResult(
          { ...a.results[0], [field]: "invalid" },
          a.selection.publication_id,
          "overview",
        ),
      ).toThrow();
    },
  );
  it("rejects another valid publication and the wrong query kind", () => {
    expect(() =>
      parseResult(b.results[0], a.selection.publication_id),
    ).toThrow();
    expect(() =>
      parseResult(a.results[0], a.selection.publication_id, "sources"),
    ).toThrow();
  });
  it.each([-1, 1.5, "12", Number.MAX_SAFE_INTEGER + 1])(
    "rejects invalid counts: %s",
    (value) => {
      expect(() =>
        parseResult(
          { ...a.results[0], data: { ...a.results[0].data, row_count: value } },
          a.selection.publication_id,
        ),
      ).toThrow();
    },
  );
  it("rejects capabilities omitted, added or repeated", () => {
    for (const capabilities of [
      [],
      ["topics"],
      [...a.selection.capabilities, "sources"],
    ]) {
      expect(() => parseSelection({ ...a.selection, capabilities })).toThrow();
    }
  });
  it("rejects aggregate disagreement instead of rendering different totals", () => {
    const value = snapshot();
    const result = value.results.find((result) => result.kind === "overview")!;
    result.data.row_count += 1;
    expect(() => validateSnapshot(value)).toThrow();
  });
  it("rejects a mismatched dataset and duplicated months", () => {
    const value = snapshot();
    value.selection.dataset_id = "other-dataset";
    expect(() => validateSnapshot(value)).toThrow();
    const duplicate = snapshot();
    const months = duplicate.results.find(
      (result) => result.kind === "coverage",
    )!.data.months;
    months[1].month = months[0].month;
    expect(() => validateSnapshot(duplicate)).toThrow();
  });
});

describe("closed component registry", () => {
  it("renders the four dashboard components using cited results", () => {
    const value = snapshot();
    const views = dashboardViews(value.results);
    expect(views).toHaveLength(4);
    for (const spec of views) {
      expect(
        resolveView(spec, value.results, value.selection.publication_id).results
          .length,
      ).toBeGreaterThan(0);
      const html = renderToStaticMarkup(
        createElement(View, {
          spec,
          results: value.results,
          publicationId: value.selection.publication_id,
        }),
      );
      expect(html).toContain("Ver evidencia");
      expect(html).toContain(value.selection.publication_id);
    }
  });
  it("refuses executable components, props, missing references and duplicate IDs", () => {
    const value = snapshot();
    const id = value.results[0].result_id;
    for (const spec of [
      { component: "script", result_ids: [id] },
      { component: "dataset_summary", result_ids: [id], html: "<script />" },
      { component: "dataset_summary", result_ids: ["f".repeat(64)] },
      { component: "preparation", result_ids: [id, id] },
    ])
      expect(() =>
        resolveView(spec, value.results, value.selection.publication_id),
      ).toThrow();
  });
  it("refuses evidence of another kind or publication", () => {
    const value = snapshot();
    const spec = {
      component: "sources",
      result_ids: [value.results[0].result_id],
    };
    expect(() =>
      resolveView(spec, value.results, value.selection.publication_id),
    ).toThrow();
    expect(() =>
      resolveView(
        dashboardViews(value.results)[0],
        b.results,
        value.selection.publication_id,
      ),
    ).toThrow();
    expect(() =>
      resolveView(
        dashboardViews(value.results)[0],
        [...value.results, value.results[0]],
        value.selection.publication_id,
      ),
    ).toThrow();
  });
  it("escapes source text rather than treating it as HTML", () => {
    const value = snapshot();
    value.results.find(
      (result) => result.kind === "sources",
    )!.data[0].platform = '<img src=x onerror="alert(1)">';
    const html = renderToStaticMarkup(
      createElement(View, {
        spec: dashboardViews(value.results)[1],
        results: value.results,
        publicationId: value.selection.publication_id,
      }),
    );
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<img src=x");
  });
});
