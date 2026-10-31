import { useId, type ReactNode } from "react";

type Evidence = {
  kind: string;
  result_id: string;
  publication_id: string;
  selection?: Record<string, unknown>;
};

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
  results: Evidence[];
  className?: string;
}) {
  const heading = useId();
  return (
    <section className={`voc-panel ${className}`} aria-labelledby={heading}>
      <div className="voc-panel-head">
        <div>
          <p className="voc-eyebrow">{eyebrow}</p>
          <h2 id={heading} className="voc-panel-title">
            {title}
          </h2>
        </div>
      </div>
      {children}
      <details className="voc-evidence">
        <summary>Ver evidencia</summary>
        <p>
          Resultados de la publicación{" "}
          <code className="voc-code">{results[0].publication_id}</code>
        </p>
        <ul>
          {results.map((result) => (
            <li key={result.result_id}>
              <span>{result.kind}</span>
              <code className="voc-code">{result.result_id}</code>
              {result.selection && (
                <code className="voc-code">
                  {JSON.stringify(
                    Object.fromEntries(
                      Object.entries(result.selection).filter(
                        ([, v]) => v !== null,
                      ),
                    ),
                  )}
                </code>
              )}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
