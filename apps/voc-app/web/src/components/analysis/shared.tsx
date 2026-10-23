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

export function StackedBar({
  negative,
  neutral,
  positive,
  scale,
  title,
}: {
  negative: number;
  neutral: number;
  positive: number;
  scale: number;
  title?: string;
}) {
  const total = negative + neutral + positive;
  const width = scale > 0 ? (total / scale) * 100 : 0;
  const part = (value: number) => (total > 0 ? (value / total) * 100 : 0);
  return (
    <div
      className="stacked-bar"
      title={
        title ??
        `negativas ${negative} · neutras ${neutral} · positivas ${positive}`
      }
      aria-hidden="true"
    >
      <div className="stacked-bar-fill" style={{ width: `${width}%` }}>
        <span
          className="seg-negative"
          style={{ width: `${part(negative)}%` }}
        />
        <span className="seg-neutral" style={{ width: `${part(neutral)}%` }} />
        <span
          className="seg-positive"
          style={{ width: `${part(positive)}%` }}
        />
      </div>
    </div>
  );
}

export function Legend() {
  return (
    <ul className="legend" aria-label="Leyenda de sentimiento">
      {labels.map((label) => (
        <li key={label}>
          <span className={`swatch seg-${label}`} aria-hidden="true" />
          {labelName[label]}
        </li>
      ))}
    </ul>
  );
}
const labels: Label[] = ["negative", "neutral", "positive"];

export function PlatformTag({ platform }: { platform: string }) {
  return (
    <span className="platform-tag">
      <span className={`swatch platform-${platform}`} aria-hidden="true" />
      {platformName(platform)}
    </span>
  );
}
