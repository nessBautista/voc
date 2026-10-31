import { useEffect, useId, useRef, useState } from "react";
import {
  ChatHeader,
  RecoveryBanner,
  SuggestionList,
} from "./components/chat/ChatHeader";
import { ChatTurn } from "./components/chat/ChatTurn";
import { Composer } from "./components/chat/Composer";
import {
  parseConversation,
  pendingKey,
  readChat,
  type Ask,
  type Conversation,
  type Mode,
} from "./conversation";
import { warningText } from "./shell";

// The six questions of the demo flow, suggested when the package carries analysis.
const DEMO_QUESTIONS = [
  "¿Cómo se sienten, en general, quienes dejaron reseñas?",
  "¿Cuál es la queja principal?",
  "¿Cuándo aumentaron las quejas?",
  "¿Qué dicen exactamente los clientes?",
  "¿Qué problemas más específicos aparecen?",
  "¿Dónde convendría poner atención primero?",
];
const DATA_QUESTIONS = [
  "¿De dónde provienen los datos?",
  "¿Cuál es la última actualización?",
  "¿Qué cobertura mensual tenemos?",
];

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
      ) ||
      !(
        value.mode === undefined ||
        value.mode === "daily" ||
        value.mode === "research"
      )
    )
      return null;
    return value;
  } catch {
    return null;
  }
}

export function Chat({ analysis = false }: { analysis?: boolean }) {
  const [id, setId] = useState<string | null>(urlId);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<Mode>("daily");
  // The mode of the last question sent, for the running turn's label.
  const [sentMode, setSentMode] = useState<Mode>("daily");
  const [pending, setPending] = useState<Ask | null>(pendingRequest);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const titleId = useId();
  // A suggestion or "Volver a preguntar" fills the composer; the reader sends.
  const prefill = (text: string) => {
    setQuestion(text);
    window.setTimeout(() => input.current?.focus(), 0);
  };
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

  // A follow-up sent from an answer keeps whatever the reader was drafting.
  async function send(payload: Ask, keepDraft = false) {
    setSentMode(payload.mode ?? "daily");
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
      if (!keepDraft) setQuestion("");
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
  function submit() {
    if (!question.trim() || busy || running || pending) return;
    void send({
      request_id: crypto.randomUUID(),
      question: question.trim(),
      conversation_id: id,
      mode,
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
  const locked = busy || running || pending !== null;
  // The server repeats the publication's warnings in every answer and once per
  // platform; the conversation shows each once, in the dashboard's words.
  const warnings = [
    ...new Set(
      (conversation?.turns ?? []).flatMap((turn) =>
        (turn.answer?.warnings ?? []).map(
          (item) => warningText[item.code] ?? item.message,
        ),
      ),
    ),
  ];
  return (
    <section className="app-chat" aria-labelledby={titleId}>
      <ChatHeader
        titleId={titleId}
        analysis={analysis}
        conversationId={id}
        publicationId={conversation?.publication_id}
        warnings={warnings}
        busy={busy}
        onNew={startNew}
        onRefresh={() => setRefresh((n) => n + 1)}
      />
      {!id && !pending && (
        <SuggestionList
          questions={analysis ? DEMO_QUESTIONS : DATA_QUESTIONS}
          onPick={prefill}
        />
      )}
      {conversation && conversation.turns.length > 0 && (
        <div className="voc-thread">
          {conversation.turns.map((turn) => (
            <ChatTurn
              key={turn.request_id}
              turn={turn}
              publicationId={conversation.publication_id}
              runningMode={sentMode}
              onAskAgain={locked ? undefined : prefill}
              onAsk={
                locked
                  ? undefined
                  : (text) =>
                      void send(
                        {
                          request_id: crypto.randomUUID(),
                          question: text,
                          conversation_id: id,
                          // A topic's reviews are a daily question.
                          mode: "daily",
                        },
                        true,
                      )
              }
            />
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className="app-notice is-error">
          {error}
        </p>
      )}
      <div className="app-chat-dock">
        {pending && (
          <RecoveryBanner
            question={pending.question}
            busy={busy}
            onRetry={() => void send(pending)}
            onDiscard={startNew}
          />
        )}
        <Composer
          value={question}
          onChange={setQuestion}
          mode={mode}
          onMode={setMode}
          onSubmit={submit}
          disabled={locked}
          sending={busy}
          inputRef={input}
        />
      </div>
    </section>
  );
}
