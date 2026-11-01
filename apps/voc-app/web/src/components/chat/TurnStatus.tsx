import { useEffect, useState } from "react";
import type { Mode } from "../../conversation";

const clock = (ms: number) => {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
};

// The research limit the server enforces; the card measures time against it.
const RESEARCH_SECONDS = 90;

// A running turn. The page knows only that the turn runs and since when: the
// steps of a research answer arrive with the answer. Daily: the answer's place,
// held by its status and three placeholder lines. Research: a card with the
// time spent against the 90 s limit and what the agent is doing.
export function TurnRunning({
  mode,
  createdAt,
}: {
  mode: Mode;
  createdAt: string;
}) {
  const started = Date.parse(createdAt);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed = Number.isFinite(started) ? Math.max(0, now - started) : null;
  if (mode === "research") {
    const seconds = elapsed === null ? 0 : Math.floor(elapsed / 1000);
    return (
      <div className="voc-turn-progress" role="status">
        <p className="voc-turn-progress-head">
          <span className="voc-pulse" aria-hidden="true" />
          <span>Investigando en varios pasos…</span>
          {elapsed !== null && (
            <span className="voc-turn-clock">
              <span className="voc-num">{clock(elapsed)}</span> de ~90 s
            </span>
          )}
        </p>
        <div
          className="voc-progress"
          role="progressbar"
          aria-label="Tiempo de la investigación"
          aria-valuemin={0}
          aria-valuemax={RESEARCH_SECONDS}
          aria-valuenow={Math.min(seconds, RESEARCH_SECONDS)}
        >
          <span
            style={{
              width: `${(Math.min(seconds, RESEARCH_SECONDS) / RESEARCH_SECONDS) * 100}%`,
            }}
          />
        </div>
        <p className="voc-turn-progress-note">
          Elige cada consulta según el resultado de la anterior, hasta seis.
          Puedes volver a esta conversación después.
        </p>
      </div>
    );
  }
  return (
    <div className="voc-turn-wait" role="status">
      <p className="voc-turn-wait-head">
        <span className="voc-pulse" aria-hidden="true" />
        <span>Consultando la publicación…</span>
        {elapsed !== null && <span className="voc-num">{clock(elapsed)}</span>}
      </p>
      <span className="voc-skeleton" aria-hidden="true" />
      <span className="voc-skeleton" aria-hidden="true" />
      <span className="voc-skeleton" aria-hidden="true" />
    </div>
  );
}

// A failed or interrupted turn: the server's message, and the question back
// in the composer with one click.
export function TurnFailed({
  message,
  onAskAgain,
}: {
  message: string;
  onAskAgain?: () => void;
}) {
  return (
    <p className="voc-turn-error" role="alert">
      <span>{message}</span>
      {onAskAgain && (
        <button
          type="button"
          className="voc-btn voc-btn-sm"
          onClick={onAskAgain}
        >
          Volver a preguntar
        </button>
      )}
    </p>
  );
}
