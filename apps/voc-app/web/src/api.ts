import { announceSessionEnded } from "./auth";
import {
  parseAnalysisResult,
  selectionQuery,
  type AnalysisResultOf,
  type Filters,
  type Sort,
} from "./analysis";
import {
  kinds,
  parseResult,
  parseSelection,
  validateSnapshot,
  type Selection,
  type Snapshot,
} from "./types";

export class DataError extends Error {
  constructor(
    message: string,
    public code: string,
    public publicationId?: string,
  ) {
    super(message);
  }
}

async function readJSON(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(path, {
    signal,
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (response.status === 401) {
    announceSessionEnded();
    throw new DataError(
      "Tu sesión terminó. Inicia sesión de nuevo.",
      "session_ended",
    );
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const noSelection =
      response.status === 503 && body?.error?.code === "no_active_publication";
    const badSelection =
      response.status === 422 && body?.error?.code === "invalid_selection";
    throw new DataError(
      noSelection
        ? "Todavía no hay una publicación disponible para explorar."
        : badSelection
          ? "La selección no es válida para esta publicación."
          : "No pudimos consultar los datos publicados. Intenta de nuevo.",
      noSelection
        ? "no_active_publication"
        : badSelection
          ? "invalid_selection"
          : "query_failed",
    );
  }
  return response.json();
}

export async function loadDashboard(
  signal: AbortSignal,
  onSelected: (selection: Selection) => void,
): Promise<Snapshot> {
  let selection: Selection | undefined;
  try {
    // Capture one identity, then use it for every query in this page load.
    selection = parseSelection(
      await readJSON("/api/v1/publications/current", signal),
    );
    onSelected(selection);
    const publicationId = selection.publication_id;
    const results = await Promise.all(
      kinds.map(async (kind) =>
        parseResult(
          await readJSON(
            `/api/v1/publications/${publicationId}/${kind}`,
            signal,
          ),
          publicationId,
          kind,
        ),
      ),
    );
    return validateSnapshot({ selection, results });
  } catch (error) {
    if (signal.aborted) throw error;
    if (error instanceof DataError) {
      error.publicationId = selection?.publication_id;
      throw error;
    }
    throw new DataError(
      "No pudimos validar la respuesta de datos. Intenta cargar de nuevo.",
      "invalid_response",
      selection?.publication_id,
    );
  }
}

// The analysis views: four kinds per selection, three more when a topic is open.
export type AnalysisSnapshot = {
  summary: AnalysisResultOf<"summary">;
  topics: AnalysisResultOf<"topic_list">;
  timeline: AnalysisResultOf<"timeline">;
  platforms: AnalysisResultOf<"platforms">;
};
export type TopicSnapshot = {
  detail: AnalysisResultOf<"topic_detail">;
  timeline: AnalysisResultOf<"timeline">;
  reviews: AnalysisResultOf<"reviews">;
};

function wrap(error: unknown, signal: AbortSignal): never {
  if (signal.aborted) throw error;
  if (error instanceof DataError) throw error;
  throw new DataError(
    "No pudimos validar la respuesta del análisis. Intenta de nuevo.",
    "invalid_response",
  );
}

export async function loadAnalysis(
  publicationId: string,
  filters: Filters,
  sort: Sort,
  signal: AbortSignal,
): Promise<AnalysisSnapshot> {
  const base = `/api/v1/publications/${publicationId}`;
  const read = async <
    K extends "summary" | "topic_list" | "timeline" | "platforms",
  >(
    kind: K,
    query: string,
  ) =>
    parseAnalysisResult(
      await readJSON(`${base}/${kind}${query}`, signal),
      publicationId,
      kind,
    ) as AnalysisResultOf<K>;
  try {
    const [summary, topics, timeline, platforms] = await Promise.all([
      read("summary", selectionQuery(filters)),
      read("topic_list", selectionQuery(filters, { sort })),
      read("timeline", selectionQuery(filters)),
      read("platforms", selectionQuery(filters)),
    ]);
    return { summary, topics, timeline, platforms };
  } catch (error) {
    wrap(error, signal);
  }
}

export async function loadTopic(
  publicationId: string,
  topicId: number,
  filters: Filters,
  page: number,
  pageSize: number,
  signal: AbortSignal,
): Promise<TopicSnapshot> {
  const base = `/api/v1/publications/${publicationId}`;
  const read = async <K extends "topic_detail" | "timeline" | "reviews">(
    kind: K,
    query: string,
  ) =>
    parseAnalysisResult(
      await readJSON(`${base}/${kind}${query}`, signal),
      publicationId,
      kind,
    ) as AnalysisResultOf<K>;
  try {
    const [detail, timeline, reviews] = await Promise.all([
      read("topic_detail", selectionQuery(filters, { topic_id: topicId })),
      read("timeline", selectionQuery(filters, { topic_id: topicId })),
      read(
        "reviews",
        selectionQuery(filters, {
          topic_id: topicId,
          page,
          page_size: pageSize,
        }),
      ),
    ]);
    if (
      detail.data.topic_id !== topicId ||
      timeline.data.topic_id !== topicId ||
      reviews.data.topic_id !== topicId
    ) {
      throw new DataError(
        "La respuesta no corresponde al tema abierto.",
        "invalid_response",
      );
    }
    return { detail, timeline, reviews };
  } catch (error) {
    wrap(error, signal);
  }
}
