import { CategoryScale, Chart, Filler, Legend, LinearScale, LineController, LineElement, PointElement, Tooltip } from "chart.js";
import { useEffect, useRef } from "react";
import { cssVar, inr } from "../lib/format";

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend, Filler);

export interface Series { label: string; data: number[]; color: string; dashed?: boolean; width?: number }

export default function LineChart({ labels, series, label, height = 220 }: { labels: string[]; series: Series[]; label: string; height?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chart = useRef<Chart | null>(null);

  useEffect(() => {
    if (!canvas.current) return;
    const ink3 = cssVar("--ink-3"), line = cssVar("--line"), ink2 = cssVar("--ink-2");
    const data = {
      labels,
      datasets: series.map((s) => ({
        label: s.label, data: s.data, borderColor: cssVar(s.color) || s.color, borderWidth: s.width ?? 2,
        borderDash: s.dashed ? [4, 4] : [], pointRadius: s.data.length < 30 && !s.dashed ? 3 : 0, pointHoverRadius: s.dashed ? 0 : 4, tension: 0.2,
      })),
    };
    const options = {
      responsive: true, maintainAspectRatio: false, animation: false as const, interaction: { mode: "index" as const, intersect: false },
      plugins: {
        legend: { display: series.length > 1, position: "bottom" as const, labels: { boxWidth: 14, boxHeight: 2, color: ink2 } },
        tooltip: { callbacks: { label: (c: { dataset: { label?: string }; parsed: { y: number } }) => `${c.dataset.label}: ${inr(c.parsed.y)}` } },
      },
      scales: {
        x: { ticks: { color: ink3, maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } },
        y: { ticks: { color: ink3, callback: (v: string | number) => inr(Number(v)) }, grid: { color: line } },
      },
    };
    if (chart.current) {
      chart.current.data = data;
      chart.current.options = options;
      chart.current.update();
    } else {
      chart.current = new Chart(canvas.current, { type: "line", data, options });
    }
  }, [labels, series]);

  useEffect(() => () => { chart.current?.destroy(); chart.current = null; }, []);

  return <div className="chart-box" style={{ height }}><canvas ref={canvas} role="img" aria-label={label} /></div>;
}
