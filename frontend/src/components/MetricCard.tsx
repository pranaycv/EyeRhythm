import type { ReactNode } from "react";

/** Distinct pastel palettes matching the reference health dashboard. */
export type CardPalette =
  | "sage"
  | "peach"
  | "sun"
  | "mint"
  | "sky"
  | "lavender"
  | "mist";

type Tone = "good" | "warn" | "bad" | "idle" | "neutral" | "future";

interface Props {
  title: string;
  value: ReactNode;
  unit?: string;
  subtitle?: string;
  help?: string;
  tone?: Tone;
  palette?: CardPalette;
  placeholder?: boolean;
  children?: ReactNode;
}

export function MetricCard({
  title,
  value,
  unit,
  subtitle,
  help,
  tone = "neutral",
  palette = "mist",
  placeholder,
  children,
}: Props) {
  return (
    <article
      className={`metric-card palette-${palette} tone-${tone} ${
        placeholder ? "placeholder" : ""
      } ${help ? "has-help" : ""}`}
    >
      <header className="metric-card__head">
        <span className="metric-card__title-row">
          <span className="metric-card__title">{title}</span>
          {help ? (
            <span className="metric-card__help" tabIndex={0}>
              <span className="metric-card__help-mark" aria-hidden>
                ?
              </span>
              <span className="metric-card__help-tip" role="tooltip">
                {help}
              </span>
            </span>
          ) : null}
        </span>
        {subtitle ? <span className="metric-card__sub">{subtitle}</span> : null}
      </header>
      <div className="metric-card__body">
        <div className="metric-card__value-row">
          <span className="metric-card__value">{value}</span>
          {unit ? <span className="metric-card__unit">{unit}</span> : null}
        </div>
        {children}
      </div>
    </article>
  );
}
