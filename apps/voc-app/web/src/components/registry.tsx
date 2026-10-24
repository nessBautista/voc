import type { ReactNode } from "react";
import { z } from "zod";
import {
  kinds,
  parseResult,
  type Kind,
  type Result,
  type ResultOf,
} from "../types";
import {
  parseAnalysisResult,
  type AnalysisKind,
  type AnalysisResult,
  type AnalysisResultOf,
} from "../analysis";
import { KpiTiles } from "./analysis/KpiTiles";
import { MoodRing } from "./analysis/MoodRing";
import { Platforms } from "./analysis/Platforms";
import { Timeline } from "./analysis/Timeline";
import { TopicDetail } from "./analysis/TopicDetail";
import { TopicList } from "./analysis/TopicList";
import { Coverage } from "./Coverage";
import { Panel } from "./Panel";
import { DatasetSummary } from "./DatasetSummary";
import { Preparation } from "./Preparation";
import { Sources } from "./Sources";

// Dataset components read v1 results; analysis components read v2 results.
// Both lists are closed: a view names a component and evidence, never code.
const viewSchema = z
  .object({
    component: z.enum([
      "dataset_summary",
      "sources",
      "coverage",
      "preparation",
      "kpi_tiles",
      "topic_list",
      "timeline",
      "platforms",
      "mood_ring",
      "topic_detail",
    ]),
    result_ids: z
      .array(z.string().regex(/^[0-9a-f]{64}$/))
      .min(1)
      .max(3),
  })
  .strict();
export type ViewSpec = z.infer<typeof viewSchema>;
type AnyKind = Kind | AnalysisKind;
export type AnyResult = Result | AnalysisResult;
const required: Record<ViewSpec["component"], AnyKind[]> = {
  dataset_summary: ["overview"],
  sources: ["sources"],
  coverage: ["coverage"],
  preparation: ["freshness", "embeddings"],
  kpi_tiles: ["summary"],
  topic_list: ["topic_list"],
  timeline: ["timeline"],
  platforms: ["platforms"],
  mood_ring: ["summary"],
  topic_detail: ["topic_detail", "reviews"],
};

export // The analysis panels' chrome, shared by the dashboard page and the chat's blocks,
// so a "mood_ring" view is the "Ánimo general" panel wherever it is drawn.
const analysisPanels = {
  kpi_tiles: {
    title: "Indicadores",
    eyebrow: "Selección actual",
    className: "kpi-panel",
  },
  topic_list: {
    title: "Temas por volumen y sentimiento",
    eyebrow: "Temas",
    className: "topics-panel",
  },
  timeline: {
    title: "Sentimiento en el tiempo",
    eyebrow: "Meses",
    className: "",
  },
  platforms: { title: "Por plataforma", eyebrow: "Fuentes", className: "" },
  mood_ring: { title: "Ánimo general", eyebrow: "Sentimiento", className: "" },
} as const;
export const topicDetailPanel = {
  eyebrow: "Detalle del tema",
  className: "detail-panel",
} as const;

export function parseAny(payload: unknown, publicationId: string): AnyResult {
  const version =
    typeof payload === "object" && payload !== null
      ? (payload as { schema_version?: unknown }).schema_version
      : undefined;
  return version === "voc-data-query-v2"
    ? parseAnalysisResult(payload, publicationId)
    : parseResult(payload, publicationId);
}

export function resolveView(
  spec: unknown,
  evidence: unknown[],
  publicationId: string,
) {
  // A view can name existing evidence; it cannot supply code, HTML or invented props.
  const view = viewSchema.parse(spec);
  const results = evidence.map((result) => parseAny(result, publicationId));
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
  results: AnyResult[];
  publicationId: string;
}) {
  const resolved = resolveView(spec, results, publicationId);
  const get = <K extends Kind>(kind: K) =>
    resolved.results.find((result) => result.kind === kind) as ResultOf<K>;
  const analysis = <K extends AnalysisKind>(kind: K) =>
    resolved.results.find(
      (result) => result.kind === kind,
    ) as AnalysisResultOf<K>;
  const component = resolved.view.component;
  // Analysis blocks are the dashboard's panels, read-only: no filters, sort or paging.
  const panel = (name: keyof typeof analysisPanels, child: ReactNode) => (
    <Panel {...analysisPanels[name]} results={resolved.results}>
      {child}
    </Panel>
  );
  switch (component) {
    case "kpi_tiles":
      return panel(component, <KpiTiles result={analysis("summary")} />);
    case "topic_list":
      return panel(
        component,
        <TopicList result={analysis("topic_list")} limit={8} />,
      );
    case "timeline":
      return panel(component, <Timeline data={analysis("timeline").data} />);
    case "platforms":
      return panel(component, <Platforms result={analysis("platforms")} />);
    case "mood_ring":
      return panel(component, <MoodRing result={analysis("summary")} />);
    case "topic_detail": {
      const detail = analysis("topic_detail");
      return (
        <Panel
          title={detail.data.display_name}
          {...topicDetailPanel}
          results={resolved.results}
        >
          <TopicDetail detail={detail} reviews={analysis("reviews")} />
        </Panel>
      );
    }
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

const datasetComponents = [
  "dataset_summary",
  "sources",
  "coverage",
  "preparation",
] as const;

export function dashboardViews(results: Result[]): ViewSpec[] {
  // Fixed dashboard and future agent answers share the same renderer.
  if (results.length !== kinds.length)
    throw new Error("Faltan resultados para esta pantalla.");
  return datasetComponents.map((component) => ({
    component,
    result_ids: required[component].map((kind) => {
      const result = results.find((item) => item.kind === kind);
      if (!result) throw new Error("Falta una consulta de datos.");
      return result.result_id;
    }),
  }));
}
