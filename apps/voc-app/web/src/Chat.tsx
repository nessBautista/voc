import { useEffect, useRef, useState, type FormEvent } from "react";
import { View } from "./components/registry";
import {
  parseConversation,
  pendingKey,
  readChat,
  type Ask,
  type Conversation,
} from "./conversation";

function urlId() {
  return new URLSearchParams(window.location.search).get("conversation");
}
function pendingRequest(): Ask | null {
  try {
    const value = JSON.parse(localStorage.getItem(pendingKey) ?? "null");
    if (
      !value ||
      typeof value.request_id !== "string" ||
      typeof value.question !== "string" ||
      !(
        value.conversation_id === null ||
        typeof value.conversation_id === "string"
      )
    )
      return null;
    return value;
  } catch {
    return null;
  }
}

export function Chat() {
  const [id, setId] = useState<string | null>(urlId);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<Ask | null>(pendingRequest);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  const running =
    conversation?.turns.some((turn) => turn.status === "running") ?? false;

  function select(next: string | null) {
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("conversation", next);
    else url.searchParams.delete("conversation");
    window.history.replaceState(null, "", url);
    setId(next);
  }
  function accept(value: Conversation) {
    setConversation(value);
    select(value.conversation_id);
    setError("");
  }
  useEffect(() => {
    const changed = () => {
      generation.current++;
      setConversation(null);
      setId(urlId());
      setRefresh((n) => n + 1);
    };
    window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, []);
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer: number | undefined;
    const started = Date.now();
    const poll = async () => {
      try {
        const value = await readChat(
          `/api/v1/conversations/${encodeURIComponent(id)}`,
        );
        if (cancelled) return;
        setConversation(value);
        setError("");
        if (value.turns.some((turn) => turn.status === "running")) {
          if (Date.now() - started > 130000)
            setError(
              "La pregunta sigue pendiente. Actualiza la conversación para recuperar su estado.",
            );
          else timer = window.setTimeout(poll, 800);
        }
      } catch {
        if (!cancelled)
          setError(
            "No pudimos recuperar la conversación. Actualiza su estado para volver a intentarlo.",
          );
      }
    };
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [id, refresh]);

  async function send(payload: Ask) {
    const revision = ++generation.current;
    setBusy(true);
    setError("");
    try {
      // Persist the exact request before sending; a lost reply can be retried safely.
      localStorage.setItem(pendingKey, JSON.stringify(payload));
      setPending(payload);
      const value = await readChat("/api/v1/ask", payload);
      if (revision !== generation.current) return;
      if (
        !value.turns.some(
          (turn) =>
            turn.request_id === payload.request_id &&
            turn.question === payload.question,
        )
      )
        throw new Error("La respuesta no incluye la pregunta enviada.");
      accept(parseConversation(value));
      localStorage.removeItem(pendingKey);
      setPending(null);
      setQuestion("");
      setRefresh((n) => n + 1);
    } catch (failure) {
      if (revision === generation.current)
        setError(
          failure instanceof Error
            ? failure.message
            : "No pudimos enviar la pregunta. Reintenta el mismo envío.",
        );
    } finally {
      if (revision === generation.current) setBusy(false);
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || busy || running || pending) return;
    void send({
      request_id: crypto.randomUUID(),
      question: question.trim(),
      conversation_id: id,
    });
  }
  function startNew() {
    generation.current++;
    localStorage.removeItem(pendingKey);
    setPending(null);
    select(null);
    setConversation(null);
    setQuestion("");
    setError("");
    setBusy(false);
  }
  return (
    <section className="chat" aria-labelledby="chat-heading">
      <div className="chat-heading">
        <div>
          <p className="eyebrow">Pregunta sobre esta información</p>
          <h2 id="chat-heading">Conversa con los datos</h2>
        </div>
        <button onClick={startNew} disabled={busy}>
          Nueva conversación
        </button>
      </div>
      <p>
        Consulta fuentes, cobertura, fechas y embeddings. Cada conversación
        conserva la publicación seleccionada al comenzar.
      </p>
      {id && (
        <div className="chat-identity">
          <span>
            Conversación <code>{id}</code>
          </span>
          {conversation && (
            <span>
              Publicación de la conversación{" "}
              <code>{conversation.publication_id}</code>
            </span>
          )}
          <button onClick={() => setRefresh((n) => n + 1)}>
            Actualizar conversación
          </button>
        </div>
      )}
      {!id && !pending && (
        <div className="suggestions">
          {[
            "¿De dónde provienen los datos?",
            "¿Cuál es la última actualización?",
            "¿Qué cobertura mensual tenemos?",
          ].map((example) => (
            <button key={example} onClick={() => setQuestion(example)}>
              {example}
            </button>
          ))}
        </div>
      )}
      <div className="chat-turns">
        {conversation?.turns.map((turn) => (
          <article className="chat-turn" key={turn.request_id}>
            <p className="chat-question">
              <span>Tú</span>
              {turn.question}
            </p>
            <p className="chat-mode">
              {turn.planner_mode === "fixture"
                ? "Planificador de prueba · Sin llamadas a un modelo"
                : "Intención interpretada por el modelo"}
            </p>
            {turn.status === "running" && (
              <p role="status">
                Consultando la publicación… Puedes volver a esta conversación
                después.
              </p>
            )}
            {turn.error && <p role="alert">{turn.error.message}</p>}
            {turn.answer && (
              <>
                <p className="chat-answer">{turn.answer.text}</p>
                <div className="chat-views">
                  {turn.answer.views.map((spec) => (
                    <View
                      key={spec.component}
                      spec={spec}
                      results={turn.answer!.results}
                      publicationId={conversation.publication_id}
                    />
                  ))}
                </div>
                {turn.answer.warnings.length > 0 && (
                  <details>
                    <summary>Advertencias de la publicación</summary>
                    <ul>
                      {turn.answer.warnings.map((item) => (
                        <li key={item.code + item.message}>{item.message}</li>
                      ))}
                    </ul>
                  </details>
                )}
                <details className="chat-trace">
                  <summary>Ver consultas y uso</summary>
                  <p>
                    Intención: {turn.answer.plan.intent}. Plataforma:{" "}
                    {turn.answer.plan.platform ?? "dataset completo"}.
                  </p>
                  <ul>
                    {turn.answer.trace.map((item) => (
                      <li key={item.result_id}>
                        {item.operation} · {item.duration_ms} ms ·{" "}
                        <code>{item.result_id}</code>
                      </li>
                    ))}
                  </ul>
                  <p>
                    Solicitudes al modelo: {turn.answer.usage.requests}. Tokens
                    de entrada:{" "}
                    {turn.answer.usage.prompt_tokens ?? "no informados"};
                    salida:{" "}
                    {turn.answer.usage.completion_tokens ?? "no informados"}.
                  </p>
                  {turn.answer.usage.model && (
                    <p>Modelo: {turn.answer.usage.model}</p>
                  )}
                </details>
              </>
            )}
          </article>
        ))}
      </div>
      {error && (
        <p role="alert" className="chat-error">
          {error}
        </p>
      )}
      {pending && (
        <div className="pending-request">
          <p>
            Envío sin confirmar: {pending.question}. Puedes recuperar la misma
            petición sin duplicar su ejecución.
          </p>
          <button disabled={busy} onClick={() => void send(pending)}>
            Reintentar el mismo envío
          </button>
          <button disabled={busy} onClick={startNew}>
            Dejar esta petición y comenzar otra
          </button>
        </div>
      )}
      <form onSubmit={submit}>
        <label htmlFor="chat-question">Tu pregunta</label>
        <textarea
          id="chat-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          maxLength={1000}
          rows={3}
          disabled={busy || running || pending !== null}
        />
        <div className="chat-submit">
          <span>Una pregunta a la vez · hasta 1,000 caracteres</span>
          <button
            disabled={!question.trim() || busy || running || pending !== null}
            type="submit"
          >
            {busy ? "Enviando…" : "Preguntar"}
          </button>
        </div>
      </form>
    </section>
  );
}
