import { z } from "zod";

// Runtime contracts: TypeScript alone cannot validate a response received over HTTP.
export const kinds = [
  "overview",
  "sources",
  "coverage",
  "freshness",
  "embeddings",
] as const;
export type Kind = (typeof kinds)[number];
const uuid = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const date = z.iso.datetime().nullable();
const identifier = z.string().min(1).max(256);
const warning = z.object({ code: z.string(), message: z.string() }).strict();
const evidence = z
  .object({
    evidence_id: identifier,
    kind: z.string(),
    evidence_ref: z.string(),
    description: z.string(),
  })
  .strict();
const collection = {
  platform: identifier,
  collection_state: z.enum(["verified", "unknown"]),
  latest_collection_at: date,
  collection_evidence_id: identifier.nullable(),
};
const source = z
  .object({ ...collection, record_count: count, unknown_dates: count })
  .strict();
const overview = z
  .object({
    dataset_id: identifier,
    source_kind: z.enum(["fixture", "personal", "s3"]),
    source_version: identifier,
    row_count: count,
    unknown_dates: count,
    record_start: date,
    record_end: date,
  })
  .strict();
const coverage = z
  .object({
    months: z.array(
      z
        .object({
          month: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/),
          record_count: count,
        })
        .strict(),
    ),
    unknown_dates: count,
  })
  .strict();
const freshness = z
  .object({
    dataset_id: identifier,
    prepared_at: date,
    publication_created_at: date,
    sources: z.array(z.object(collection).strict()),
    evidence: z.array(evidence),
  })
  .strict();
const embeddings = z
  .object({
    dataset_id: identifier,
    state: z.enum(["not_reported", "registered", "verified"]),
    release_id: identifier.nullable(),
    model: z.string().nullable(),
    covered_rows: count.nullable(),
    verification_evidence_id: identifier.nullable(),
    evidence: z.array(evidence),
  })
  .strict();
const envelope = {
  schema_version: z.literal("voc-data-query-v1"),
  publication_id: uuid,
  result_id: digest,
  warnings: z.array(warning),
};
export const resultSchema = z.discriminatedUnion("kind", [
  z
    .object({ ...envelope, kind: z.literal("overview"), data: overview })
    .strict(),
  z
    .object({ ...envelope, kind: z.literal("sources"), data: z.array(source) })
    .strict(),
  z
    .object({ ...envelope, kind: z.literal("coverage"), data: coverage })
    .strict(),
  z
    .object({ ...envelope, kind: z.literal("freshness"), data: freshness })
    .strict(),
  z
    .object({ ...envelope, kind: z.literal("embeddings"), data: embeddings })
    .strict(),
]);
export type Result = z.infer<typeof resultSchema>;
export type ResultOf<K extends Kind> = Extract<Result, { kind: K }>;
export const selectionSchema = z
  .object({
    publication_id: uuid,
    manifest_sha256: digest,
    activated_at: z.iso.datetime(),
    schema_version: z.enum(["voc-data-state-v1", "voc-data-state-v2"]),
    dataset_id: identifier,
    capabilities: z.array(z.string().min(1).max(40)),
    warnings: z.array(warning),
  })
  .strict();
export type Selection = z.infer<typeof selectionSchema>;
export type Snapshot = { selection: Selection; results: Result[] };

export function parseSelection(payload: unknown): Selection {
  const parsed = selectionSchema.safeParse(payload);
  // This screen needs the five dataset kinds; a v2 package lists more, never fewer.
  if (
    !parsed.success ||
    new Set(parsed.data.capabilities).size !==
      parsed.data.capabilities.length ||
    kinds.some((kind) => !parsed.data.capabilities.includes(kind))
  ) {
    throw new Error(
      "La selección recibida no es compatible con esta pantalla.",
    );
  }
  return parsed.data;
}

export function parseResult(
  payload: unknown,
  publicationId: string,
  kind?: Kind,
): Result {
  const parsed = resultSchema.safeParse(payload);
  if (
    !parsed.success ||
    parsed.data.publication_id !== publicationId ||
    (kind !== undefined && parsed.data.kind !== kind)
  ) {
    throw new Error(
      "Los datos recibidos no corresponden a la consulta o publicación solicitada.",
    );
  }
  return parsed.data;
}

export function validateSnapshot(snapshot: Snapshot): Snapshot {
  // Reject mixed datasets or incomplete totals before rendering any card.
  const byKind = <K extends Kind>(kind: K) => {
    const matches = snapshot.results.filter((result) => result.kind === kind);
    if (matches.length !== 1)
      throw new Error("La respuesta de datos está incompleta.");
    return matches[0] as ResultOf<K>;
  };
  const summary = byKind("overview").data;
  const sources = byKind("sources").data;
  const months = byKind("coverage").data;
  const fresh = byKind("freshness").data;
  const embedded = byKind("embeddings").data;
  const sum = (values: number[]) =>
    values.reduce((total, value) => total + value, 0);
  if (
    snapshot.results.length !== kinds.length ||
    snapshot.results.some(
      (result) => result.publication_id !== snapshot.selection.publication_id,
    ) ||
    [summary.dataset_id, fresh.dataset_id, embedded.dataset_id].some(
      (id) => id !== snapshot.selection.dataset_id,
    ) ||
    sum(sources.map((source) => source.record_count)) !== summary.row_count ||
    sum(sources.map((source) => source.unknown_dates)) !==
      summary.unknown_dates ||
    sum(months.months.map((month) => month.record_count)) +
      months.unknown_dates !==
      summary.row_count ||
    months.unknown_dates !== summary.unknown_dates ||
    summary.unknown_dates > summary.row_count ||
    sources.some((source) => source.unknown_dates > source.record_count) ||
    new Set(sources.map((source) => source.platform)).size !== sources.length ||
    new Set(months.months.map((month) => month.month)).size !==
      months.months.length
  ) {
    throw new Error(
      "Las consultas no forman una publicación coherente. Intenta cargar de nuevo.",
    );
  }
  return snapshot;
}
