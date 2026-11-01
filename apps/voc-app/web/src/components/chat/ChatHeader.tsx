import { Icon, type IconName } from "../icons";

// Opens the chat page: what the conversation is for, a way to start over, and
// the ids that pin it to one publication. While the conversation is empty it is
// the page's hero, centred above the composer.
export function ChatHeader({
  titleId,
  analysis,
  conversationId,
  publicationId,
  warnings,
  busy,
  onNew,
  onRefresh,
  start = false,
}: {
  titleId: string;
  analysis: boolean;
  conversationId: string | null;
  publicationId?: string;
  // The publication's warnings across the conversation's answers, once each.
  warnings: string[];
  busy: boolean;
  onNew: () => void;
  onRefresh: () => void;
  start?: boolean;
}) {
  return (
    <>
      <div className={start ? "voc-chat-head is-start" : "voc-chat-head"}>
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
        {conversationId && (
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
        )}
      </div>
      {conversationId && (
        <div className="voc-chat-meta">
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
          {warnings.length > 0 && (
            <details className="voc-chat-details">
              <summary>
                Advertencias de la publicación ({warnings.length})
              </summary>
              <ul>
                {warnings.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </>
  );
}

export type Suggestion = { label: string; question: string; icon: IconName };

// Ready questions while the conversation is empty: short buttons, each named
// for its topic and carrying the full question, which is also its accessible
// name. Picking one fills the composer without sending, so the reader can edit
// it or change the mode.
export function SuggestionList({
  suggestions,
  onPick,
}: {
  suggestions: Suggestion[];
  onPick: (question: string) => void;
}) {
  return (
    <section className="voc-suggestions" aria-label="Preguntas sugeridas">
      <ul>
        {suggestions.map(({ label, question, icon }) => (
          <li key={question}>
            <button
              type="button"
              className="voc-suggestion"
              title={question}
              onClick={() => onPick(question)}
            >
              <Icon name={icon} />
              {label}
              <span className="voc-sr">{`: ${question}`}</span>
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
