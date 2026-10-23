import {
  assessments,
  labels,
  platforms,
  type Assessment,
  type Filters,
  type Label,
  type Platform,
} from "../../analysis";
import { platformName } from "../format";
import { assessmentName, labelName, shortMonth } from "./shared";

function Chips<T extends string>({
  name,
  values,
  chosen,
  label,
  swatch,
  onToggle,
}: {
  name: string;
  values: readonly T[];
  chosen: Set<T>;
  label: (value: T) => string;
  swatch?: (value: T) => string;
  onToggle: (value: T) => void;
}) {
  return (
    <fieldset className="chip-group">
      <legend>{name}</legend>
      {values.map((value) => {
        const active = chosen.has(value);
        return (
          <button
            key={value}
            type="button"
            className={`chip${active ? " active" : ""}`}
            aria-pressed={active}
            onClick={() => onToggle(value)}
          >
            {swatch && (
              <span className={`swatch ${swatch(value)}`} aria-hidden="true" />
            )}
            {label(value)}
          </button>
        );
      })}
    </fieldset>
  );
}

export function FilterBar({
  filters,
  months,
  onChange,
  onReset,
}: {
  filters: Filters;
  months: string[];
  onChange: (next: Filters) => void;
  onReset: () => void;
}) {
  // The last active chip of a group stays on: an empty group is no view at all.
  const toggle = <T extends string>(
    key: "platforms" | "labels" | "assessments",
    value: T,
  ) => {
    const current = filters[key] as Set<T>;
    const next = new Set(current);
    if (next.has(value)) {
      if (next.size === 1) return;
      next.delete(value);
    } else next.add(value);
    onChange({ ...filters, [key]: next });
  };
  const first = months[0];
  const last = months[months.length - 1];
  const from = filters.monthFrom ?? first;
  const to = filters.monthTo ?? last;
  return (
    <section className="filter-bar" aria-label="Filtros">
      <Chips<Platform>
        name="Plataforma"
        values={platforms}
        chosen={filters.platforms}
        label={platformName}
        swatch={(value) => `platform-${value}`}
        onToggle={(value) => toggle("platforms", value)}
      />
      <Chips<Label>
        name="Sentimiento"
        values={labels}
        chosen={filters.labels}
        label={(value) => labelName[value]}
        swatch={(value) => `seg-${value}`}
        onToggle={(value) => toggle("labels", value)}
      />
      <Chips<Assessment>
        name="Evaluación"
        values={assessments}
        chosen={filters.assessments}
        label={(value) => assessmentName[value]}
        onToggle={(value) => toggle("assessments", value)}
      />
      <div className="filter-months">
        <label>
          Meses de
          <select
            value={from ?? ""}
            disabled={months.length === 0}
            onChange={(event) => {
              const value = event.target.value;
              onChange({
                ...filters,
                monthFrom: value === first ? null : value,
                monthTo:
                  to && value > to
                    ? value === last
                      ? null
                      : value
                    : filters.monthTo,
              });
            }}
          >
            {months.map((value) => (
              <option key={value} value={value}>
                {shortMonth(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          a
          <select
            value={to ?? ""}
            disabled={months.length === 0}
            onChange={(event) => {
              const value = event.target.value;
              onChange({
                ...filters,
                monthTo: value === last ? null : value,
                monthFrom:
                  from && value < from
                    ? value === first
                      ? null
                      : value
                    : filters.monthFrom,
              });
            }}
          >
            {months.map((value) => (
              <option key={value} value={value}>
                {shortMonth(value)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="filter-search">
        <label>
          Buscar
          <input
            type="search"
            maxLength={100}
            placeholder="texto en las reseñas"
            value={filters.search}
            onChange={(event) =>
              onChange({ ...filters, search: event.target.value })
            }
          />
        </label>
        <button type="button" onClick={onReset}>
          Restablecer
        </button>
      </div>
    </section>
  );
}
