import type { ReactNode } from "react";

// A record's attributes as label and value rows. Rows without a value are
// left out, never shown as "—".
export function PropertyList({
  rows,
}: {
  rows: { label: string; value: ReactNode; chart?: boolean }[];
}) {
  const shown = rows.filter((row) => row.value !== null && row.value !== "");
  if (shown.length === 0) return null;
  return (
    <dl className="voc-props">
      {shown.map((row) => (
        <div key={row.label} className={row.chart ? "is-chart" : undefined}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
