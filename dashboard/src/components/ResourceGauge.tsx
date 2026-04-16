"use client";

interface ResourceGaugeProps {
  label: string;
  value: number;
  max: number;
  unit: string;
  size?: "sm" | "md" | "lg";
}

export function ResourceGauge({ label, value, max, unit, size = "md" }: ResourceGaugeProps) {
  const percent = max > 0 ? Math.min((value / max) * 100, 100) : 0;

  // Color based on usage level
  let barColor = "var(--color-accent)";
  if (percent > 80) barColor = "var(--color-danger)";
  else if (percent > 60) barColor = "var(--color-warning)";

  const dimensions = {
    sm: { width: "100%", barHeight: "4px", fontSize: "text-[11px]" },
    md: { width: "100%", barHeight: "6px", fontSize: "text-xs" },
    lg: { width: "100%", barHeight: "8px", fontSize: "text-sm" },
  }[size];

  return (
    <div className="flex-1 min-w-0">
      <div className={`flex items-center justify-between mb-1 ${dimensions.fontSize}`}>
        <span className="mono font-medium" style={{ color: "var(--color-text-muted)" }}>
          {label}
        </span>
        <span className="mono" style={{ color: "var(--color-text-secondary)" }}>
          {typeof value === "number" ? value.toFixed(1) : value}{unit}
        </span>
      </div>
      <div
        className="w-full rounded-full overflow-hidden"
        style={{ height: dimensions.barHeight, background: "var(--color-bg-input)" }}
      >
        <div
          className="h-full rounded-full transition-all duration-500 ease-out"
          style={{
            width: `${percent}%`,
            background: barColor,
            boxShadow: percent > 0 ? `0 0 8px ${barColor}40` : "none",
          }}
        />
      </div>
    </div>
  );
}

/**
 * Circular gauge for the container detail view.
 */
interface CircularGaugeProps {
  label: string;
  value: number;
  max: number;
  unit: string;
}

export function CircularGauge({ label, value, max, unit }: CircularGaugeProps) {
  const percent = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const radius = 40;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percent / 100) * circumference;

  let strokeColor = "var(--color-accent)";
  if (percent > 80) strokeColor = "var(--color-danger)";
  else if (percent > 60) strokeColor = "var(--color-warning)";

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-24 h-24">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
          {/* Background ring */}
          <circle
            cx="50" cy="50" r={radius}
            fill="none"
            stroke="var(--color-bg-input)"
            strokeWidth="6"
          />
          {/* Value ring */}
          <circle
            cx="50" cy="50" r={radius}
            fill="none"
            stroke={strokeColor}
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            style={{
              transition: "stroke-dashoffset 0.5s ease-out, stroke 0.3s ease",
              filter: `drop-shadow(0 0 4px ${strokeColor}60)`,
            }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="mono text-lg font-bold" style={{ color: "var(--color-text-heading)" }}>
            {percent.toFixed(0)}
          </span>
          <span className="mono text-[10px]" style={{ color: "var(--color-text-muted)" }}>
            {unit}
          </span>
        </div>
      </div>
      <span className="mono text-xs mt-2" style={{ color: "var(--color-text-secondary)" }}>
        {label}
      </span>
      <span className="mono text-[10px]" style={{ color: "var(--color-text-muted)" }}>
        {value.toFixed(1)} / {max}{unit === "%" ? "%" : ` ${unit}`}
      </span>
    </div>
  );
}
