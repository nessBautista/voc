// Opens the chat page: what the conversation is for, a way to start over, and
// the ids that pin it to one publication.
export function ChatHeader({
  titleId,
  analysis,
  conversationId,
  publicationId,
  busy,
  onNew,
  onRefresh,
}: {
  titleId: string;
  analysis: boolean;
  conversationId: string | null;
  publicationId?: string;
  busy: boolean;
  onNew: () => void;
  onRefresh: () => void;
}) {
  return (
    <>
      <div className="voc-chat-head">
        <div>
          <p className="voc-eyebrow">Pregunta sobre esta información</p>
          <h1 className="voc-chat-title" id={titleId}>
            Conversa con los datos
          </h1>
          <p className="voc-chat-lede">
            {analysis
              ? "Pregunta por el ánimo general, la queja principal, su evolución mensual y las reseñas de un tema; también por fuentes, cobertura y fechas. "
              : "Consulta fuentes, cobertura, fechas y embeddings. "}
            Cada conversación conserva la publicación seleccionada al comenzar.
          </p>
        </div>
        <div className="voc-chat-actions">
          <button
            type="button"
            className="voc-btn"
            onClick={onNew}
            disabled={busy}
          >
            Nueva conversación
          </button>
        </div>
      </div>
      {conversationId && (
        <details className="voc-chat-details">
          <summary>Detalles de la conversación</summary>
          <dl>
            <dt>Conversación</dt>
            <dd>{conversationId}</dd>
            {publicationId && (
              <>
                <dt>Publicación</dt>
                <dd>{publicationId}</dd>
              </>
            )}
          </dl>
          <p>
            <button
              type="button"
              className="voc-btn voc-btn-sm"
              onClick={onRefresh}
            >
              Actualizar conversación
            </button>
          </p>
        </details>
      )}
    </>
  );
}

// Ready questions while the conversation is empty. Picking one fills the
// composer without sending, so the reader can edit it or change the mode.
export function SuggestionList({
  questions,
  onPick,
}: {
  questions: string[];
  onPick: (question: string) => void;
}) {
  return (
    <section className="voc-suggestions" aria-label="Preguntas sugeridas">
      <p className="voc-eyebrow">Prueba con una pregunta</p>
      <ul>
        {questions.map((question) => (
          <li key={question}>
            <button
              type="button"
              className="voc-suggestion"
              onClick={() => onPick(question)}
            >
              {question}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

// A send that was not confirmed: retry the same request, or drop it.
export function RecoveryBanner({
  question,
  busy,
  onRetry,
  onDiscard,
}: {
  question: string;
  busy: boolean;
  onRetry: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className="voc-recovery" role="status">
      <p>
        Envío sin confirmar: «{question}». Puedes recuperar la misma petición
        sin duplicar su ejecución.
      </p>
      <button
        type="button"
        className="voc-btn"
        disabled={busy}
        onClick={onRetry}
      >
        Reintentar el mismo envío
      </button>
      <button
        type="button"
        className="voc-btn voc-btn-ghost"
        disabled={busy}
        onClick={onDiscard}
      >
        Dejar esta petición y comenzar otra
      </button>
    </div>
  );
}
