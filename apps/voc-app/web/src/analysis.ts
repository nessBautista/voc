import { z } from "zod";

// Contracts for the six analysis kinds of a voc-data-state-v2 package. The
// selection travels inside every envelope, so a result identifies its filters.
export const platforms = ["google_play", "app_store", "x", "youtube"] as const;
export const labels = ["negative", "neutral", "positive"] as const;
export const assessments = [
  "single_feature",
  "mixed",
  "unclear",
  "uninterpreted",
  "unassigned",
] as const;
export const sorts = ["volume", "negative", "positive"] as const;
export const analysisKinds = [
  "summary",
  "topic_list",
  "timeline",
  "platforms",
  "topic_detail",
  "reviews",
] as const;
export type Platform = (typeof platforms)[number];
export type Label = (typeof labels)[number];
export type Assessment = (typeof assessments)[number];
export type Sort = (typeof sorts)[number];
export type AnalysisKind = (typeof analysisKinds)[number];

const uuid = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const share = z.number().min(0).max(1).nullable();
const month = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/);
const topicId = z.number().int().min(-1);
const warning = z.object({ code: z.string(), message: z.string() }).strict();

export const selectionSchema = z
  .object({
    platforms: z.array(z.enum(platforms)).nullable(),
    labels: z.array(z.enum(labels)).nullable(),
    assessments: z.array(z.enum(assessments)).nullable(),
    month_from: month.nullable(),
    month_to: month.nullable(),
    search: z.string().max(100).nullable(),
    topic_id: topicId.nullable(),
    sort: z.enum(sorts).nullable(),
    page: z.number().int().min(1).nullable(),
    page_size: z.number().int().min(1).max(50).nullable(),
  })
  .strict();
export type CanonicalSelection = z.infer<typeof selectionSchema>;

const counts = {
  total: count,
  classified: count,
  negative: count,
  neutral: count,
  positive: count,
};
const feature = z
  .object({
    topic_id: topicId,
    display_name: z.string(),
    assessment: z.enum(assessments),
    negative: count,
    total: count,
    share: share,
    wilson_lower: share,
    eligible_topics: count,
  })
  .strict();
const summary = z
  .object({
    reviews_in_view: count,
    reviews_total: count,
    classified: count,
    topics_in_view: count,
    topics_total: count,
    negative: z.object({ count, share }).strict(),
    neutral: z.object({ count, share }).strict(),
    positive: z.object({ count, share }).strict(),
    most_negative_feature: feature.nullable(),
  })
  .strict();
const topicRow = z
  .object({
    topic_id: topicId,
    display_name: z.string(),
    wording_source: z.enum(["generated", "original"]),
    assessment: z.enum(assessments),
    keywords: z.array(z.string()).max(6),
    member_count: count,
    ...counts,
    negative_share: share,
    positive_share: share,
    wilson_lower: share,
  })
  .strict();
const timeline = z
  .object({
    topic_id: topicId.nullable(),
    months: z.array(z.object({ month, ...counts }).strict()),
    busiest: z.object({ month, total: count }).strict().nullable(),
    reviews_in_view: count,
  })
  .strict();
const platformRow = z
  .object({
    platform: z.string().min(1),
    ...counts,
    negative_share: share,
    positive_share: share,
  })
  .strict();
const topicDetail = z
  .object({
    topic_id: topicId,
    display_name: z.string(),
    wording_source: z.enum(["generated", "original"]),
    assessment: z.enum(assessments),
    description: z.string(),
    proposed_feature: z.string().nullable(),
    interpretation_status: z.string().nullable(),
    keywords: z.array(z.string()).max(6),
    evidence_ids: z.array(z.string()),
    supporting_review_ids: z.array(z.string()).nullable(),
    member_count: count,
    in_view: count,
    classified: count,
    negative: count,
    neutral: count,
    positive: count,
    negative_share: share,
    positive_share: share,
    wilson_lower: share,
  })
  .strict();
const review = z
  .object({
    record_id: z.string().min(1),
    platform: z.string().min(1),
    record_date: z.string().nullable(),
    month: month.nullable(),
    rating: z.number().int().min(1).max(5).nullable(),
    topic_id: topicId,
    status: z.enum(["classified", "excluded", "failed"]),
    label: z.enum(labels).nullable(),
    negative: share,
    neutral: share,
    positive: share,
    text: z.string(),
    confidence: share,
  })
  .strict();
const reviews = z
  .object({
    topic_id: topicId.nullable(),
    total: count,
    page: z.number().int().min(1),
    page_size: z.number().int().min(1).max(50),
    pages: count,
    items: z.array(review).max(50),
  })
  .strict();

const envelope = {
  schema_version: z.literal("voc-data-query-v2"),
  publication_id: uuid,
  result_id: digest,
  warnings: z.array(warning),
  selection: selectionSchema,
};
export const analysisResultSchema = z.discriminatedUnion("kind", [
  z.object({ ...envelope, kind: z.literal("summary"), data: summary }).strict(),
  z
    .object({
      ...envelope,
      kind: z.literal("topic_list"),
      data: z.array(topicRow),
    })
    .strict(),
  z
    .object({ ...envelope, kind: z.literal("timeline"), data: timeline })
    .strict(),
  z
    .object({
      ...envelope,
      kind: z.literal("platforms"),
      data: z.array(platformRow),
    })
    .strict(),
  z
    .object({
      ...envelope,
      kind: z.literal("topic_detail"),
      data: topicDetail,
    })
    .strict(),
  z.object({ ...envelope, kind: z.literal("reviews"), data: reviews }).strict(),
]);
export type AnalysisResult = z.infer<typeof analysisResultSchema>;
export type AnalysisResultOf<K extends AnalysisKind> = Extract<
  AnalysisResult,
  { kind: K }
>;
export type TopicRow = z.infer<typeof topicRow>;
export type Review = z.infer<typeof review>;
export type TimelineData = z.infer<typeof timeline>;

export function parseAnalysisResult(
  payload: unknown,
  publicationId: string,
  kind?: AnalysisKind,
): AnalysisResult {
  const parsed = analysisResultSchema.safeParse(payload);
  if (
    !parsed.success ||
    parsed.data.publication_id !== publicationId ||
    (kind !== undefined && parsed.data.kind !== kind)
  ) {
    throw new Error(
      "Los datos del análisis no corresponden a la consulta o publicación solicitada.",
    );
  }
  return parsed.data;
}

// The filters the bar holds. Sets are never empty: the last chip stays on.
export type Filters = {
  platforms: Set<Platform>;
  labels: Set<Label>;
  assessments: Set<Assessment>;
  monthFrom: string | null;
  monthTo: string | null;
  search: string;
};
export const allFilters = (): Filters => ({
  platforms: new Set(platforms),
  labels: new Set(labels),
  assessments: new Set(assessments),
  monthFrom: null,
  monthTo: null,
  search: "",
});

export function isDefault(filters: Filters) {
  return (
    filters.platforms.size === platforms.length &&
    filters.labels.size === labels.length &&
    filters.assessments.size === assessments.length &&
    filters.monthFrom === null &&
    filters.monthTo === null &&
    filters.search.trim() === ""
  );
}

// Canonical on the way out: a full set is no filter, months only when narrowed.
export function selectionQuery(
  filters: Filters,
  extra: {
    topic_id?: number;
    sort?: Sort;
    page?: number;
    page_size?: number;
  } = {},
): string {
  const params = new URLSearchParams();
  const subset = <T extends string>(chosen: Set<T>, all: readonly T[]) =>
    chosen.size === all.length ? null : all.filter((v) => chosen.has(v));
  const chosenPlatforms = subset(filters.platforms, platforms);
  const chosenLabels = subset(filters.labels, labels);
  const chosenAssessments = subset(filters.assessments, assessments);
  if (chosenPlatforms) params.set("platforms", chosenPlatforms.join(","));
  if (chosenLabels) params.set("labels", chosenLabels.join(","));
  if (chosenAssessments) params.set("assessments", chosenAssessments.join(","));
  if (filters.monthFrom) params.set("month_from", filters.monthFrom);
  if (filters.monthTo) params.set("month_to", filters.monthTo);
  const search = filters.search.trim().slice(0, 100);
  if (search) params.set("search", search);
  if (extra.topic_id !== undefined)
    params.set("topic_id", String(extra.topic_id));
  if (extra.sort) params.set("sort", extra.sort);
  if (extra.page) params.set("page", String(extra.page));
  if (extra.page_size) params.set("page_size", String(extra.page_size));
  const text = params.toString();
  return text ? `?${text}` : "";
}
