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
import { Icon } from "../icons";
import { useId } from "react";
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
  const id = useId();
  return (
    <div className="voc-chip-group" role="group" aria-labelledby={id}>
      <span className="voc-eyebrow" id={id}>
        {name}
      </span>
      <div className="voc-chip-row">
        {values.map((value) => (
          <button
            key={value}
            type="button"
            className="voc-chip"
            aria-pressed={chosen.has(value)}
            onClick={() => onToggle(value)}
          >
            {/* On reads at a glance: a check, besides the accent and the solid border. */}
            {chosen.has(value) && <Icon name="check" />}
            {swatch && (
              <span
                className={`voc-swatch ${swatch(value)}`}
                aria-hidden="true"
              />
            )}
            {label(value)}
          </button>
        ))}
      </div>
    </div>
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
  const monthsId = useId();
  const searchId = useId();
  return (
    <section className="voc-filterbar" aria-label="Filtros">
      <Chips<Platform>
        name="Plataforma"
        values={platforms}
        chosen={filters.platforms}
        label={platformName}
        onToggle={(value) => toggle("platforms", value)}
      />
      <Chips<Label>
        name="Sentimiento"
        values={labels}
        chosen={filters.labels}
        label={(value) => labelName[value]}
        swatch={(value) => `voc-fill-${value}`}
        onToggle={(value) => toggle("labels", value)}
      />
      <Chips<Assessment>
        name="Evaluación"
        values={assessments}
        chosen={filters.assessments}
        label={(value) => assessmentName[value]}
        onToggle={(value) => toggle("assessments", value)}
      />
      <div className="voc-field" role="group" aria-labelledby={monthsId}>
        <span className="voc-eyebrow" id={monthsId}>
          Meses
        </span>
        <div className="voc-field-row">
          <span className="voc-select-wrap">
            <select
              className="voc-select"
              aria-label="Desde"
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
          </span>
          <span className="voc-meta">a</span>
          <span className="voc-select-wrap">
            <select
              className="voc-select"
              aria-label="Hasta"
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
          </span>
        </div>
      </div>
      <div className="voc-field app-filter-search">
        <span className="voc-eyebrow" id={searchId}>
          Buscar
        </span>
        <div className="voc-search">
          <input
            className="voc-input"
            type="search"
            aria-labelledby={searchId}
            maxLength={100}
            placeholder="texto en las reseñas"
            value={filters.search}
            onChange={(event) =>
              onChange({ ...filters, search: event.target.value })
            }
          />
          <button type="button" className="voc-btn" onClick={onReset}>
            Restablecer
          </button>
        </div>
      </div>
    </section>
  );
}
