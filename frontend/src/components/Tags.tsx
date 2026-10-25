export function StanceTag({ stance }: { stance: string | null | undefined }) {
  if (stance === "bullish") return <span className="tag buy">▲ Bullish</span>;
  if (stance === "bearish") return <span className="tag sell">▼ Bearish</span>;
  return <span className="tag hold">Neutral</span>;
}

export function ActionTag({ action }: { action: string }) {
  const label = action === "BUY" ? "▲ Buy" : action === "SELL" ? "▼ Sell" : action === "HOLD" ? "Hold" : "Avoid";
  return <span className={`tag ${action.toLowerCase()}`}>{label}</span>;
}

const STATUS_LABEL: Record<string, string> = {
  executed: "placed", failed: "failed", skipped: "skipped", no_action: "no trade", proposed: "proposed",
  COMPLETE: "filled", REJECTED: "rejected", ERROR: "error", BLOCKED: "blocked", PENDING: "pending", CANCELLED: "cancelled",
};
export function StatusTag({ status }: { status: string }) {
  return <span className={`tag status ${status}`}>{STATUS_LABEL[status] ?? status.toLowerCase()}</span>;
}
