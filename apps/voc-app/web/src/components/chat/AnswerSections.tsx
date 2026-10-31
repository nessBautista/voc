// The server writes every analysis answer as paragraphs that open with a label
// and a period: "Qué dicen los datos.", "Paso 2 · Queja principal.",
// "Interpretación.", "Lo que no se puede afirmar.". Each becomes a section
// with its label as a heading. Anything else stays a plain paragraph, so a
// change of wording loses no text.
const LABEL =
  /^(Qué dicen los datos|Interpretación|Lo que no se puede afirmar|Paso \d+ · [^.]+)\.\s+([\s\S]+)$/;
const LIMITS = "Lo que no se puede afirmar";

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

export function AnswerSections({ text }: { text: string }) {
  return (
    <div className="voc-answer">
      {sections(text).map((part, index) =>
        part.kind === "section" ? (
          <section
            key={index}
            className={`voc-answer-section${part.label === LIMITS ? " voc-answer-caveat" : ""}`}
          >
            <h3 className="voc-answer-label">{part.label}</h3>
            <p className="voc-answer-text">{part.text}</p>
          </section>
        ) : (
          <p key={index} className="voc-answer-text">
            {part.text}
          </p>
        ),
      )}
    </div>
  );
}
