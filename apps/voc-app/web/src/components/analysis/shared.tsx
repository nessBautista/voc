import type { Assessment, Label } from "../../analysis";
import { platformName } from "../format";

export const percent = (share: number | null, digits = 0) =>
  share === null
    ? "—"
    : new Intl.NumberFormat("es-MX", {
        style: "percent",
        maximumFractionDigits: digits,
      }).format(share);
export const shortMonth = (value: string) =>
  new Intl.DateTimeFormat("es-MX", {
    timeZone: "UTC",
    month: "short",
    year: "2-digit",
  })
    .format(new Date(`${value}-01T00:00:00Z`))
    .replace(".", "");
export const labelName: Record<Label, string> = {
  negative: "Negativas",
  neutral: "Neutras",
  positive: "Positivas",
};
export const assessmentName: Record<Assessment, string> = {
  single_feature: "Una funcionalidad",
  mixed: "Mixto",
  unclear: "Poco claro",
  uninterpreted: "Sin interpretar",
  unassigned: "Sin asignar",
};

// One 8px bar: the fill is the row's total on the list's shared scale, each
// segment its share of that total. Spans, so it can sit inside a row button.
export function StackedBar({
  negative,
  neutral,
  positive,
  scale,
  title,
  label,
}: {
  negative: number;
  neutral: number;
  positive: number;
  scale: number;
  title?: string;
  // With a label the bar is an image that names its counts; without one it is
  // decoration beside numbers the row already shows.
  label?: string;
}) {
  const total = negative + neutral + positive;
  const width = scale > 0 ? Math.min((total / scale) * 100, 100) : 0;
  const part = (value: number) => (total > 0 ? (value / total) * 100 : 0);
  const segments = (
    [
      ["negative", negative],
      ["neutral", neutral],
      ["positive", positive],
    ] as const
  ).filter(([, value]) => value > 0);
  return (
    <span
      className="voc-stacked"
      title={
        title ??
        `negativas ${negative} · neutras ${neutral} · positivas ${positive}`
      }
      {...(label
        ? { role: "img", "aria-label": label }
        : { "aria-hidden": true })}
    >
      <span className="voc-stacked-fill" style={{ width: `${width}%` }}>
        {segments.map(([name, value]) => (
          <span
            key={name}
            className={`voc-fill-${name}`}
            style={{ width: `${part(value)}%` }}
          />
        ))}
      </span>
    </span>
  );
}

export function Legend() {
  return (
    <ul className="voc-legend" aria-label="Leyenda de sentimiento">
      {labels.map((label) => (
        <li key={label}>
          <span className={`voc-swatch voc-fill-${label}`} aria-hidden="true" />
          {labelName[label]}
        </li>
      ))}
    </ul>
  );
}
const labels: Label[] = ["negative", "neutral", "positive"];

export function PlatformTag({ platform }: { platform: string }) {
  return (
    <span
      className={`voc-platform${platform === "unknown" ? " is-unknown" : ""}`}
    >
      {platformName(platform)}
    </span>
  );
}
