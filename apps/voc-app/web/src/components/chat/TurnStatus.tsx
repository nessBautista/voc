import { useEffect, useState } from "react";
import type { Mode } from "../../conversation";

const clock = (ms: number) => {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
};

// A running turn: the elapsed time, counted in the page from when it started.
// Research answers bring their steps when they finish, so time is what we have.
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
  const research = mode === "research";
  return (
    <p className="voc-turn-running" role="status">
      <span className="voc-pulse" aria-hidden="true" />
      <span>
        {research
          ? "Investigando en varios pasos…"
          : "Consultando la publicación…"}
      </span>
      {Number.isFinite(started) && (
        <span>
          <span className="voc-num">{clock(now - started)}</span>
          {research ? " de ~90 s" : ""}
        </span>
      )}
      <span>Puedes volver a esta conversación después.</span>
    </p>
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
