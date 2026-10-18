export const number = (value: number) =>
  new Intl.NumberFormat("es-MX").format(value);
export const platformName = (name: string) =>
  ({
    google_play: "Google Play",
    app_store: "App Store",
    x: "X",
    youtube: "YouTube",
    unknown: "Sin fuente identificada",
  })[name] ?? name;
export const dateLabel = (value: string | null, withTime = false) =>
  value === null
    ? "No disponible"
    : new Intl.DateTimeFormat("es-MX", {
        timeZone: "UTC",
        year: "numeric",
        month: "short",
        day: "numeric",
        ...(withTime
          ? { hour: "2-digit" as const, minute: "2-digit" as const }
          : {}),
      }).format(new Date(value));
export const monthLabel = (value: string) =>
  new Intl.DateTimeFormat("es-MX", {
    timeZone: "UTC",
    month: "short",
    year: "numeric",
  }).format(new Date(`${value}-01T00:00:00Z`));
