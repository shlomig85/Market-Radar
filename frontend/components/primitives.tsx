import type { DataMode } from "@/lib/api";

/**
 * Provenance badge.
 *
 * Rendered next to anything derived from outside data. This is the single most important
 * component in the UI: it is what stops a synthetic figure from being read as a live one.
 */
export function DataModeBadge({ mode, title }: { mode: DataMode; title?: string }) {
  const style: Record<DataMode, string> = {
    LIVE: "border-favour/40 text-favour bg-favour/5",
    HISTORICAL: "border-signal/40 text-signal bg-signal/5",
    DEMO: "border-demo/40 text-demo bg-demo/5",
    UNAVAILABLE: "border-line text-faint bg-transparent",
  };
  return (
    <span
      title={title ?? mode}
      className={`inline-block border px-1.5 py-0.5 text-[10px] font-mono tracking-wider ${style[mode]}`}
    >
      {mode}
    </span>
  );
}

/** A labelled number with an optional 0-100 bar. */
export function Metric({
  label,
  value,
  suffix = "",
  bar = false,
  tone = "signal",
  hint,
}: {
  label: string;
  value: number | null | undefined;
  suffix?: string;
  bar?: boolean;
  tone?: "signal" | "warn" | "against" | "favour";
  hint?: string;
}) {
  const toneClass = {
    signal: "text-signal",
    warn: "text-warn",
    against: "text-against",
    favour: "text-favour",
  }[tone];
  const barClass = {
    signal: "bg-signal",
    warn: "bg-warn",
    against: "bg-against",
    favour: "bg-favour",
  }[tone];

  return (
    <div title={hint}>
      <div className="label">{label}</div>
      {value === null || value === undefined ? (
        // Never a zero, never a dash that could read as a value.
        <div className="numeric text-lg text-faint">unavailable</div>
      ) : (
        <>
          <div className={`numeric text-lg ${toneClass}`}>
            {value.toFixed(value % 1 === 0 ? 0 : 1)}
            <span className="text-faint text-xs">{suffix}</span>
          </div>
          {bar && (
            <div className="mt-1 h-[3px] w-full bg-line">
              <div
                className={`h-full ${barClass}`}
                style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Maturity as a rail, so the stage is legible relative to the whole lifecycle. */
export function MaturityRail({ maturity, stage }: { maturity: string; stage: number }) {
  const stages = [
    "INVISIBLE",
    "EMERGING",
    "DEVELOPING",
    "ACCELERATING",
    "CONSENSUS",
    "CROWDED",
    "MATURE",
  ];
  if (stage < 0) {
    return <span className="numeric text-against text-xs">{maturity}</span>;
  }
  return (
    <div className="flex items-center gap-[3px]">
      {stages.map((name, index) => (
        <span
          key={name}
          title={name}
          className={`h-[6px] w-6 ${
            index === stage
              ? "bg-warn"
              : index < stage
                ? "bg-signal/40"
                : "bg-line"
          }`}
        />
      ))}
      <span className="numeric ml-2 text-xs text-warn">{maturity}</span>
    </div>
  );
}

/** Signed value with direction colouring. */
export function Delta({ value, suffix = "" }: { value: number | null; suffix?: string }) {
  if (value === null) return <span className="numeric text-faint">unavailable</span>;
  const tone = value > 2 ? "text-favour" : value < -2 ? "text-against" : "text-muted";
  return (
    <span className={`numeric ${tone}`}>
      {value > 0 ? "+" : ""}
      {value.toFixed(0)}
      {suffix}
    </span>
  );
}

export function ClaimTag({ type }: { type: string }) {
  const style: Record<string, string> = {
    FACT: "text-favour border-favour/30",
    INFERENCE: "text-signal border-signal/30",
    HYPOTHESIS: "text-warn border-warn/30",
    FORECAST: "text-demo border-demo/30",
  };
  return (
    <span
      className={`border px-1 py-px text-[9px] font-mono tracking-wider ${
        style[type] ?? "text-faint border-line"
      }`}
      title={
        {
          FACT: "Directly supported by evidence.",
          INFERENCE: "Derived from multiple facts.",
          HYPOTHESIS: "A proposed explanation, not an observation.",
          FORECAST: "A forward-looking expectation.",
        }[type] ?? type
      }
    >
      {type}
    </span>
  );
}

export function Panel({
  title,
  subtitle,
  children,
  right,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <section className="panel">
      <header className="flex items-start justify-between border-b border-line px-4 py-2.5">
        <div>
          <h2 className="text-sm font-semibold tracking-wide">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
        </div>
        {right}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

/** Empty states explain what to do, never a blank panel. */
export function Empty({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="border border-dashed border-line px-4 py-6 text-center">
      <p className="text-sm text-muted">{title}</p>
      <p className="mt-1 text-xs text-faint">{hint}</p>
    </div>
  );
}

export function NotImplemented({ feature, reason }: { feature: string; reason: string }) {
  return (
    <div className="border border-dashed border-line/70 bg-raised/40 px-3 py-2">
      <span className="label text-faint">Not implemented</span>
      <p className="mt-0.5 text-xs text-muted">
        <span className="text-ink">{feature}</span> — {reason}
      </p>
    </div>
  );
}
