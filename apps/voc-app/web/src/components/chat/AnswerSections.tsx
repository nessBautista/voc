import type { ReactNode } from "react";

// The server writes every analysis answer as paragraphs that open with a label
// and a period: "Qué dicen los datos.", "Paso 2 · Queja principal.",
// "Interpretación.", "Lo que no se puede afirmar.". Each becomes a section
// with its label as a heading. Anything else stays a plain paragraph, so a
// change of wording loses no text.
const LABEL =
  /^(Qué dicen los datos|Interpretación|Lo que no se puede afirmar|Paso \d+ · [^.]+)\.\s+([\s\S]+)$/;
export const LIMITS = "Lo que no se puede afirmar";
export const DATA = "Qué dicen los datos";

export type Section =
  | { kind: "section"; label: string; text: string }
  | { kind: "paragraph"; text: string };

export function sections(text: string): Section[] {
  return text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph !== "")
    .map((paragraph) => {
      const match = LABEL.exec(paragraph);
      return match
        ? { kind: "section", label: match[1], text: match[2] }
        : { kind: "paragraph", text: paragraph };
    });
}

// The step a "Paso N · …" label opens, counted from zero; otherwise null.
export const stepIndex = (part: Section) => {
  const match =
    part.kind === "section" ? /^Paso (\d+) ·/.exec(part.label) : null;
  return match ? Number(match[1]) - 1 : null;
};

export function AnswerSections({
  parts,
  after,
}: {
  parts: Section[];
  // What follows a part, such as the panels a step drew.
  after?: (part: Section, index: number) => ReactNode;
}) {
  return (
    <div className="voc-answer">
      {parts.map((part, index) => (
        <div key={index} className="voc-answer-part">
          {part.kind === "section" ? (
            <section
              className={`voc-answer-section${part.label === LIMITS ? " voc-answer-caveat" : ""}`}
            >
              <h3 className="voc-answer-label">{part.label}</h3>
              <p className="voc-answer-text">{part.text}</p>
            </section>
          ) : (
            <p className="voc-answer-text">{part.text}</p>
          )}
          {after?.(part, index)}
        </div>
      ))}
    </div>
  );
}
