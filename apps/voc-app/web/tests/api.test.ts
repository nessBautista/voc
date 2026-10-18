import { afterEach, expect, it, vi } from "vitest";
import a from "./fixtures/example_a.json";
import b from "./fixtures/example_b.json";
import { loadDashboard } from "../src/api";

afterEach(() => vi.unstubAllGlobals());

it("pins every query to the one selected publication even when current changes", async () => {
  const urls: string[] = [];
  let current = a;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      if (url.endsWith("/current")) {
        const selected = current.selection;
        current = b;
        return Response.json(selected);
      }
      expect(url).toContain(a.selection.publication_id);
      return Response.json(
        a.results.find((result) => url.endsWith(result.kind)),
      );
    }),
  );
  const value = await loadDashboard(new AbortController().signal, vi.fn());
  expect(value.selection.publication_id).toBe(a.selection.publication_id);
  expect(urls.filter((url) => url.endsWith("/current"))).toHaveLength(1);
  expect(urls).toHaveLength(6);
});

it("preserves the selected ID when one query fails instead of replacing data with zeros", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/current")) return Response.json(a.selection);
      if (url.endsWith("/sources"))
        return Response.json(
          { error: { code: "publication_unavailable" } },
          { status: 503 },
        );
      return Response.json(
        a.results.find((result) => url.endsWith(result.kind)),
      );
    }),
  );
  await expect(
    loadDashboard(new AbortController().signal, vi.fn()),
  ).rejects.toMatchObject({
    code: "query_failed",
    publicationId: a.selection.publication_id,
  });
});

it("rejects a response for B while A was requested", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      Response.json(
        url.endsWith("/current")
          ? a.selection
          : b.results.find((result) => url.endsWith(result.kind)),
      ),
    ),
  );
  await expect(
    loadDashboard(new AbortController().signal, vi.fn()),
  ).rejects.toMatchObject({
    code: "invalid_response",
    publicationId: a.selection.publication_id,
  });
});

it("distinguishes missing publication from a transport failure", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(
        { error: { code: "no_active_publication" } },
        { status: 503 },
      ),
    ),
  );
  await expect(
    loadDashboard(new AbortController().signal, vi.fn()),
  ).rejects.toMatchObject({ code: "no_active_publication" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }),
  );
  await expect(
    loadDashboard(new AbortController().signal, vi.fn()),
  ).rejects.toMatchObject({ code: "invalid_response" });
});
