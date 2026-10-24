import { z } from "zod";
import { announceSessionEnded } from "./auth";
import {
  analysisKinds,
  analysisResultSchema,
  labels,
  platforms,
} from "./analysis";
import { kinds as datasetKinds, resultSchema } from "./types";
import { parseAny, resolveView, type ViewSpec } from "./components/registry";

const uuid = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const intents = [
  "overview",
  "sources",
  "coverage",
  "freshness",
  "embeddings",
  "mood",
  "main_complaints",
  "trend",
  "topic_reviews",
  "specific_problems",
  "focus",
  "unsupported",
  "clarify",
] as const;
type Intent = (typeof intents)[number];
// The kinds each intent runs, in order; the server's operations() table.
const expectedKinds: Record<Intent, string[]> = {
  overview: ["overview"],
  sources: ["sources"],
  coverage: ["coverage"],
  freshness: ["freshness", "embeddings"],
  embeddings: ["freshness", "embeddings"],
  mood: ["summary", "platforms"],
  main_complaints: ["summary", "topic_list"],
  trend: ["timeline"],
  topic_reviews: ["topic_detail", "reviews"],
  specific_problems: ["summary", "topic_list"],
  focus: ["summary", "topic_list"],
  unsupported: [],
  clarify: [],
};
const analysisIntents = new Set<Intent>([
  "mood",
  "main_complaints",
  "trend",
  "topic_reviews",
  "specific_problems",
  "focus",
]);
const mode = z.enum(["fixture", "model"]);
const count = z.number().int().nonnegative();
const month = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/);
const warning = z.object({ code: z.string(), message: z.string() }).strict();
const planSelection = z
  .object({
    platforms: z.array(z.enum(platforms)).min(1).nullable(),
    labels: z.array(z.enum(labels)).min(1).nullable(),
    month_from: month.nullable(),
    month_to: month.nullable(),
    search: z.string().min(1).max(100).nullable(),
  })
  .strict();
const topicReference = z
  .object({
    topic_id: z.number().int().min(-1).nullable(),
    reference: z.enum(["last_highlighted", "last_opened"]).nullable(),
  })
  .strict();
// Plans stored before the selection existed have two keys; the rest are read as null.
const planSchema = z
  .object({
    intent: z.enum(intents),
    platform: z
      .enum(["google_play", "app_store", "x", "youtube", "unknown"])
      .nullable(),
    selection: planSelection.nullable().optional(),
    topic: topicReference.nullable().optional(),
    limit: z.number().int().min(1).max(10).nullable().optional(),
  })
  .strict();
export type Plan = z.infer<typeof planSchema>;
const modeSchema = z.enum(["daily", "research"]);
export type Mode = z.infer<typeof modeSchema>;
const answerSchema = z
  .object({
    schema_version: z.literal("voc-answer-v1"),
    publication_id: uuid,
    // Answers stored before research mode have no mode: they are Daily.
    mode: modeSchema.optional(),
    plan: planSchema,
    // A research answer lists its steps; each names a slice of the results in order.
    steps: z
      .array(
        z
          .object({ plan: planSchema, result_ids: z.array(digest).max(6) })
          .strict(),
      )
      .min(1)
      .max(6)
      .optional(),
    text: z.string().min(1),
    results: z.array(z.union([resultSchema, analysisResultSchema])).max(6),
    views: z
      .array(
        z
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
            result_ids: z.array(digest).min(1).max(3),
          })
          .strict(),
      )
      .max(6),
    warnings: z.array(warning),
    trace: z
      .array(
        z
          .object({
            operation: z.enum([...datasetKinds, ...analysisKinds]),
            publication_id: uuid,
            result_id: digest,
            duration_ms: count,
          })
          .strict(),
      )
      .max(6),
    usage: z
      .object({
        mode,
        model: z.string().nullable(),
        requests: count.max(7),
        prompt_tokens: count.nullable(),
        completion_tokens: count.nullable(),
        duration_ms: count,
      })
      .strict(),
  })
  .strict();
const conversationSchema = z
  .object({
    schema_version: z.literal("voc-conversation-v1"),
    conversation_id: uuid,
    publication_id: uuid,
    created_at: z.iso.datetime(),
    turns: z
      .array(
        z
          .object({
            request_id: uuid,
            question: z.string(),
            status: z.enum(["running", "completed", "failed", "interrupted"]),
            planner_mode: mode,
            answer: answerSchema.nullable(),
            error: warning.nullable(),
            created_at: z.iso.datetime(),
            finished_at: z.iso.datetime().nullable(),
          })
          .strict(),
      )
      .max(100),
  })
  .strict();
export type Conversation = z.infer<typeof conversationSchema>;
export type Ask = {
  request_id: string;
  question: string;
  conversation_id: string | null;
  mode?: Mode;
};
export const pendingKey = "voc.pending-question.v1";

export function parseConversation(
  payload: unknown,
  expectedId?: string,
): Conversation {
  const conversation = conversationSchema.parse(payload);
  if (expectedId && conversation.conversation_id !== expectedId)
    throw new Error("La conversación recibida no es la solicitada.");
  if (
    new Set(conversation.turns.map((turn) => turn.request_id)).size !==
    conversation.turns.length
  )
    throw new Error("Hay preguntas duplicadas.");
  let running = 0;
  for (const turn of conversation.turns) {
    if (turn.status === "running") running++;
    if (
      (turn.status === "completed") !== (turn.answer !== null) ||
      (turn.status === "running") !== (turn.finished_at === null) ||
      ["failed", "interrupted"].includes(turn.status) !== (turn.error !== null)
    )
      throw new Error("El estado de la pregunta es inconsistente.");
    const answer = turn.answer;
    if (!answer) continue;
    if (
      answer.publication_id !== conversation.publication_id ||
      answer.usage.mode !== turn.planner_mode
    )
      throw new Error("La respuesta pertenece a otra publicación o modo.");
    const results = answer.results.map((result) =>
      parseAny(result, conversation.publication_id),
    );
    if (
      new Set(results.map((result) => result.result_id)).size !== results.length
    )
      throw new Error("Evidencia duplicada.");
    const intent = answer.plan.intent;
    const views = answer.views.length;
    const research = answer.mode === "research";
    if (research !== (answer.steps !== undefined))
      throw new Error("La respuesta no declara sus pasos.");
    // A research answer's steps partition the results in order and start with its plan.
    const steps = answer.steps ?? [{ plan: answer.plan, result_ids: [] }];
    if (
      research &&
      (JSON.stringify(steps[0].plan) !== JSON.stringify(answer.plan) ||
        JSON.stringify(steps.flatMap((step) => step.result_ids)) !==
          JSON.stringify(results.map((result) => result.result_id)))
    )
      throw new Error("Los pasos no corresponden a la evidencia.");
    const kinds = research
      ? steps.flatMap((step) => expectedKinds[step.plan.intent])
      : expectedKinds[intent];
    // Version 1 intents draw one block; analysis intents one to three; a research
    // answer at most one per step; refusals none.
    const viewsExpected = research
      ? views <= steps.length
      : analysisIntents.has(intent)
        ? views >= 1 && views <= 3
        : views === (expectedKinds[intent].length === 0 ? 0 : 1);
    if (
      JSON.stringify(results.map((result) => result.kind)) !==
        JSON.stringify(kinds) ||
      answer.trace.length !== results.length ||
      !viewsExpected
    )
      throw new Error("La evidencia no corresponde al plan.");
    for (const [index, item] of answer.trace.entries()) {
      const result = results[index];
      if (
        item.result_id !== result.result_id ||
        item.publication_id !== conversation.publication_id ||
        item.operation !== result.kind
      )
        throw new Error("La traza no corresponde a la evidencia.");
    }
    for (const spec of answer.views)
      resolveView(spec as ViewSpec, results, conversation.publication_id);
  }
  if (running > 1) throw new Error("Hay más de una pregunta en ejecución.");
  return conversation;
}

// The plan's scope in words for the trace drawer, as the evidence echo writes it.
export function planScope(plan: Plan) {
  const parts: string[] = [];
  if (plan.platform) parts.push(`Plataforma: ${plan.platform}.`);
  const selection = Object.fromEntries(
    Object.entries(plan.selection ?? {}).filter(([, value]) => value !== null),
  );
  if (Object.keys(selection).length > 0)
    parts.push(`Selección: ${JSON.stringify(selection)}.`);
  if (plan.topic)
    parts.push(
      `Tema: ${plan.topic.topic_id !== null && plan.topic.topic_id !== undefined ? plan.topic.topic_id : plan.topic.reference}.`,
    );
  if (plan.limit) parts.push(`Límite: ${plan.limit}.`);
  return parts.length > 0 ? parts.join(" ") : "Alcance: dataset completo.";
}

export async function readChat(
  path: string,
  payload?: Ask,
): Promise<Conversation> {
  const response = await fetch(path, {
    method: payload ? "POST" : "GET",
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
    headers: {
      Accept: "application/json",
      ...(payload ? { "Content-Type": "application/json" } : {}),
    },
    ...(payload ? { body: JSON.stringify(payload) } : {}),
  });
  if (response.status === 401) {
    announceSessionEnded();
    throw new Error("Tu sesión terminó. Inicia sesión de nuevo.");
  }
  const body = await response.json();
  if (!response.ok)
    throw new Error(
      typeof body?.error?.message === "string"
        ? body.error.message
        : "No pudimos consultar la conversación.",
    );
  return parseConversation(
    body,
    payload?.conversation_id ?? (payload ? undefined : path.split("/").at(-1)),
  );
}
