export const inr = (v: number | null | undefined, d = 0): string =>
  v == null || Number.isNaN(v) ? "–" : "₹" + Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });

export const pct = (v: number | null | undefined, d = 2): string =>
  v == null || Number.isNaN(v) ? "–" : (v > 0 ? "+" : "") + Number(v).toFixed(d) + "%";

export const signed = (v: number | null | undefined): string =>
  v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + inr(Math.abs(v));

export const tone = (v: number | null | undefined): string => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");

export const hhmm = (iso?: string | null): string => (iso ? iso.slice(11, 16) : "");

export const shortTs = (iso?: string | null): string => (iso ? iso.slice(5, 16).replace("T", " ") : "");

export const cssVar = (name: string): string => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
