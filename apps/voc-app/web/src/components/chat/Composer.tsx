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
import { Icon, type IconName } from "../icons";

const MAX = 1000;
const modes: [Mode, string, IconName, string, string][] = [
  [
    "daily",
    "Diaria",
    "daily",
    "Una pregunta, una consulta directa; responde en segundos.",
    "Pregunta sobre las reseñas, por ejemplo: ¿cuál es la queja principal?",
  ],
  [
    "research",
    "Investigación",
    "research",
    "Una pregunta mayor, respondida con hasta seis consultas encadenadas; tarda hasta 90 segundos.",
    "Plantea una pregunta mayor, por ejemplo: ¿qué cambió en las quejas este año?",
  ],
];

// The question box with the mode inside it, the count and the send button.
// The chosen mode is said in words beside the switch, and research marks the
// box's border. On an empty conversation the box is larger: it is the page's
// main control. Enter adds a line; the button, or Ctrl/⌘ + Enter, sends.
export function Composer({
  value,
  onChange,
  mode,
  onMode,
  onSubmit,
  disabled,
  sending,
  inputRef,
  start = false,
}: {
  value: string;
  onChange: (value: string) => void;
  mode: Mode;
  onMode: (mode: Mode) => void;
  onSubmit: () => void;
  disabled: boolean;
  sending: boolean;
  inputRef?: Ref<HTMLTextAreaElement>;
  start?: boolean;
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
      className={[
        "voc-composer",
        mode === "research" ? "is-research" : "",
        start ? "is-start" : "",
      ]
        .filter(Boolean)
        .join(" ")}
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
        placeholder={current[4]}
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
          className="voc-segmented is-strong"
          role="group"
          aria-label="Modo de la pregunta"
        >
          {modes.map(([key, label, icon]) => (
            <button
              key={key}
              type="button"
              aria-pressed={mode === key}
              onClick={() => onMode(key)}
              disabled={disabled}
            >
              <Icon name={icon} />
              {label}
            </button>
          ))}
        </div>
        <p className="voc-composer-mode">{current[3]}</p>
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
