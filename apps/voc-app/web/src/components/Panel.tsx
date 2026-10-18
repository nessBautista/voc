import { useId, type ReactNode } from "react";
import type { Result } from "../types";

export function Panel({
  title,
  eyebrow,
  children,
  results,
  className = "",
}: {
  title: string;
  eyebrow: string;
  children: ReactNode;
  results: Result[];
  className?: string;
}) {
  const heading = useId();
  return (
    <section className={`panel ${className}`} aria-labelledby={heading}>
      <div className="panel-heading">
        <p className="eyebrow">{eyebrow}</p>
        <h2 id={heading}>{title}</h2>
      </div>
      {children}
      <details className="evidence">
        <summary>Ver evidencia</summary>
        <p>
          Resultados de la publicación <code>{results[0].publication_id}</code>
        </p>
        <ul>
          {results.map((result) => (
            <li key={result.result_id}>
              <span>{result.kind}</span>
              <code>{result.result_id}</code>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
