export default function Spark({ values, w = 90, h = 26 }: { values: number[]; w?: number; h?: number }) {
  if (!values || values.length < 2) return null;
  const mn = Math.min(...values), mx = Math.max(...values), r = mx - mn || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * (w - 2) + 1},${h - 1 - ((v - mn) / r) * (h - 2)}`).join(" ");
  const col = values[values.length - 1] >= values[0] ? "var(--bull)" : "var(--bear)";
  return (
    <svg className="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={col} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
