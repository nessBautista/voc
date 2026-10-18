import { z } from "zod";
import {
  kinds,
  parseResult,
  type Kind,
  type Result,
  type ResultOf,
} from "../types";
import { Coverage } from "./Coverage";
import { DatasetSummary } from "./DatasetSummary";
import { Preparation } from "./Preparation";
import { Sources } from "./Sources";

const viewSchema = z
  .object({
    component: z.enum([
      "dataset_summary",
      "sources",
      "coverage",
      "preparation",
    ]),
    result_ids: z
      .array(z.string().regex(/^[0-9a-f]{64}$/))
      .min(1)
      .max(2),
  })
  .strict();
export type ViewSpec = z.infer<typeof viewSchema>;
const required: Record<ViewSpec["component"], Kind[]> = {
  dataset_summary: ["overview"],
  sources: ["sources"],
  coverage: ["coverage"],
  preparation: ["freshness", "embeddings"],
};

export function resolveView(
  spec: unknown,
  evidence: unknown[],
  publicationId: string,
) {
  // A view can name existing evidence; it cannot supply code, HTML or invented props.
  const view = viewSchema.parse(spec);
  const results = evidence.map((result) => parseResult(result, publicationId));
  const ids = new Set(results.map((result) => result.result_id));
  if (
    ids.size !== results.length ||
    new Set(view.result_ids).size !== view.result_ids.length
  ) {
    throw new Error("Referencias de evidencia duplicadas.");
  }
  const selected = view.result_ids.map((id) => {
    const result = results.find((item) => item.result_id === id);
    if (!result) throw new Error("La vista referencia evidencia inexistente.");
    return result;
  });
  const needs = required[view.component];
  if (
    selected.length !== needs.length ||
    needs.some(
      (kind) => selected.filter((result) => result.kind === kind).length !== 1,
    )
  ) {
    throw new Error("La evidencia no corresponde al componente solicitado.");
  }
  return { view, results: selected };
}

export function View({
  spec,
  results,
  publicationId,
}: {
  spec: ViewSpec;
  results: Result[];
  publicationId: string;
}) {
  const resolved = resolveView(spec, results, publicationId);
  const get = <K extends Kind>(kind: K) =>
    resolved.results.find((result) => result.kind === kind) as ResultOf<K>;
  switch (resolved.view.component) {
    case "dataset_summary":
      return <DatasetSummary result={get("overview")} />;
    case "sources":
      return <Sources result={get("sources")} />;
    case "coverage":
      return <Coverage result={get("coverage")} />;
    case "preparation":
      return (
        <Preparation
          freshness={get("freshness")}
          embeddings={get("embeddings")}
        />
      );
  }
}

export function dashboardViews(results: Result[]): ViewSpec[] {
  // Fixed dashboard and future agent answers share the same renderer.
  if (results.length !== kinds.length)
    throw new Error("Faltan resultados para esta pantalla.");
  return (Object.keys(required) as ViewSpec["component"][]).map(
    (component) => ({
      component,
      result_ids: required[component].map((kind) => {
        const result = results.find((item) => item.kind === kind);
        if (!result) throw new Error("Falta una consulta de datos.");
        return result.result_id;
      }),
    }),
  );
}
