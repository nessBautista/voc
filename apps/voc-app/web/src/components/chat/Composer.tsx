import {
  useEffect,
  useId,
  useImperativeHandle,
  useRef,
  type FormEvent,
  type Ref,
} from "react";
import type { Mode } from "../../conversation";
import { number } from "../format";

const MAX = 1000;
const modes: [Mode, string, string, string][] = [
  [
    "daily",
    "Diaria",
    "Una pregunta, un plan, una respuesta.",
    "Pregunta sobre las reseñas, por ejemplo: ¿cuál es la queja principal?",
  ],
  [
    "research",
    "Investigación",
    "Una pregunta mayor, respondida en hasta seis consultas encadenadas; hasta 90 segundos.",
    "Plantea una pregunta mayor, por ejemplo: ¿qué cambió en las quejas este año?",
  ],
];

// The question box with the mode inside it, the count and the send button.
// Enter adds a line; the button, or Ctrl/⌘ + Enter, sends.
export function Composer({
  value,
  onChange,
  mode,
  onMode,
  onSubmit,
  disabled,
  sending,
  inputRef,
}: {
  value: string;
  onChange: (value: string) => void;
  mode: Mode;
  onMode: (mode: Mode) => void;
  onSubmit: () => void;
  disabled: boolean;
  sending: boolean;
  inputRef?: Ref<HTMLTextAreaElement>;
}) {
  const id = useId();
  const box = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(inputRef, () => box.current as HTMLTextAreaElement);
  // One line at rest; the box grows with the text up to its maximum height.
  useEffect(() => {
    const field = box.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight}px`;
  }, [value]);
  const current = modes.find(([key]) => key === mode) ?? modes[0];
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };
  return (
    <form
      className="voc-composer"
      aria-label="Nueva pregunta"
      onSubmit={submit}
    >
      <label className="voc-sr" htmlFor={id}>
        Tu pregunta
      </label>
      <textarea
        id={id}
        ref={box}
        rows={1}
        maxLength={MAX}
        placeholder={current[3]}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            onSubmit();
          }
        }}
      />
      <div className="voc-composer-bar">
        <div
          className="voc-segmented"
          role="group"
          aria-label="Modo de la pregunta"
        >
          {modes.map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={mode === key}
              onClick={() => onMode(key)}
              disabled={disabled}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="voc-composer-mode">{current[2]}</p>
        <span className="voc-composer-count">
          {number(value.length)} / {number(MAX)}
        </span>
        <button
          type="submit"
          className="voc-btn voc-btn-primary"
          disabled={!value.trim() || disabled}
        >
          {sending ? "Enviando…" : "Preguntar"}
        </button>
      </div>
    </form>
  );
}
