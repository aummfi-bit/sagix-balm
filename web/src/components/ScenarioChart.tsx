"use client";

type Series = { name: string; data: number[]; color: string };

export function ScenarioChart({
  categories,
  series,
}: {
  categories: string[];
  series: Series[];
}) {
  const width = 720;
  const height = 260;
  const pad = { t: 20, r: 16, b: 36, l: 48 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;

  const all = series.flatMap((s) => s.data);
  const minY = Math.min(...all, 0);
  const maxY = Math.max(...all, 0);
  const span = Math.max(maxY - minY, 1);

  const x = (i: number) => pad.l + (i / (categories.length - 1)) * plotW;
  const y = (v: number) => pad.t + ((maxY - v) / span) * plotH;

  const zeroY = y(0);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full h-auto"
      role="img"
      aria-label="Scenario P&L chart"
    >
      <line
        x1={pad.l}
        y1={zeroY}
        x2={width - pad.r}
        y2={zeroY}
        stroke="var(--stroke)"
        strokeDasharray="4 4"
      />
      {series.map((s) => {
        const d = s.data
          .map((v, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(v)}`)
          .join(" ");
        return (
          <g key={s.name}>
            <path d={d} fill="none" stroke={s.color} strokeWidth={2} />
            {s.data.map((v, i) => (
              <circle
                key={i}
                cx={x(i)}
                cy={y(v)}
                r={3}
                fill={s.color}
              />
            ))}
          </g>
        );
      })}
      {categories.map((label, i) =>
        i % 2 === 0 ? (
          <text
            key={label}
            x={x(i)}
            y={height - 10}
            textAnchor="middle"
            className="fill-[var(--muted)] text-[10px]"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            {label}
          </text>
        ) : null,
      )}
      {[minY, 0, maxY].map((v) => (
        <text
          key={v}
          x={pad.l - 8}
          y={y(v) + 3}
          textAnchor="end"
          className="fill-[var(--muted)] text-[10px]"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          {`$${v.toFixed(0)}`}
        </text>
      ))}
    </svg>
  );
}
