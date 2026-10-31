import { planScope, type Conversation } from "../../conversation";
import { warningText } from "../../shell";
import { number } from "../format";

type Answer = NonNullable<Conversation["turns"][number]["answer"]>;

// The evidence behind an answer, one click away: the queries that ran, what
// the model used, and the publication's warnings in the dashboard's words.
export function AnswerFooter({ answer }: { answer: Answer }) {
  const { usage, trace } = answer;
  const tokens =
    usage.prompt_tokens === null && usage.completion_tokens === null
      ? null
      : (usage.prompt_tokens ?? 0) + (usage.completion_tokens ?? 0);
  const used =
    usage.requests === 0
      ? "sin llamadas a un modelo"
      : [
          tokens === null
            ? `${usage.requests} ${usage.requests === 1 ? "solicitud" : "solicitudes"} al modelo`
            : `${number(tokens)} tokens`,
          usage.model,
        ]
          .filter(Boolean)
          .join(" · ");
  // The server repeats a warning per platform; the reader needs it once.
  const warnings = [
    ...new Set(
      answer.warnings.map((item) => warningText[item.code] ?? item.message),
    ),
  ];
  return (
    <div className="voc-answer-foot">
      <details>
        <summary>
          Ver consultas y uso · {trace.length}{" "}
          {trace.length === 1 ? "consulta" : "consultas"} · {used}
        </summary>
        <p>
          Intención: {answer.plan.intent}. {planScope(answer.plan)}
        </p>
        <ul>
          {trace.map((item) => (
            <li key={item.result_id}>
              <span>{item.operation}</span>
              <span className="voc-num">{number(item.duration_ms)} ms</span>
              <code className="voc-code">{item.result_id}</code>
            </li>
          ))}
        </ul>
        <p>
          Solicitudes al modelo: {usage.requests}. Tokens de entrada:{" "}
          {usage.prompt_tokens ?? "no informados"}; salida:{" "}
          {usage.completion_tokens ?? "no informados"}.
        </p>
        {usage.model && <p>Modelo: {usage.model}</p>}
      </details>
      {warnings.length > 0 && (
        <details>
          <summary>Advertencias de la publicación ({warnings.length})</summary>
          <ul>
            {warnings.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
