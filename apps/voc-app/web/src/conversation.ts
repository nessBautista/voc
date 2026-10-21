import { z } from "zod";
import { announceSessionEnded } from "./auth";
import { parseResult, resultSchema } from "./types";
import { resolveView, type ViewSpec } from "./components/registry";

const uuid = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const intent = z.enum([
  "overview",
  "sources",
  "coverage",
  "freshness",
  "embeddings",
  "unsupported",
  "clarify",
]);
const mode = z.enum(["fixture", "model"]);
const count = z.number().int().nonnegative();
const warning = z.object({ code: z.string(), message: z.string() }).strict();
const answerSchema = z
  .object({
    schema_version: z.literal("voc-answer-v1"),
    publication_id: uuid,
    plan: z
      .object({
        intent,
        platform: z
          .enum(["google_play", "app_store", "x", "youtube", "unknown"])
          .nullable(),
      })
      .strict(),
    text: z.string().min(1),
    results: z.array(resultSchema).max(3),
    views: z
      .array(
        z
          .object({
            component: z.enum([
              "dataset_summary",
              "sources",
              "coverage",
              "preparation",
            ]),
            result_ids: z.array(digest).min(1).max(2),
          })
          .strict(),
      )
      .max(1),
    warnings: z.array(warning),
    trace: z
      .array(
        z
          .object({
            operation: z.enum([
              "overview",
              "sources",
              "coverage",
              "freshness",
              "embeddings",
            ]),
            publication_id: uuid,
            result_id: digest,
            duration_ms: count,
          })
          .strict(),
      )
      .max(3),
    usage: z
      .object({
        mode,
        model: z.string().nullable(),
        requests: count.max(1),
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
      parseResult(result, conversation.publication_id),
    );
    if (
      new Set(results.map((result) => result.result_id)).size !== results.length
    )
      throw new Error("Evidencia duplicada.");
    const noData = ["unsupported", "clarify"].includes(answer.plan.intent);
    const kinds = noData
      ? []
      : ["freshness", "embeddings"].includes(answer.plan.intent)
        ? ["freshness", "embeddings"]
        : [answer.plan.intent];
    if (
      JSON.stringify(results.map((result) => result.kind)) !==
        JSON.stringify(kinds) ||
      answer.trace.length !== results.length ||
      answer.views.length !== (noData ? 0 : 1)
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
