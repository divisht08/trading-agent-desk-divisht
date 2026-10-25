import { useMemo } from "react";
import { api } from "../lib/api";
import { useDesk } from "../lib/desk";
import { inr, pct, shortTs, signed, tone } from "../lib/format";
import LineChart from "./LineChart";

export function BookSwitch() {
  const { status, viewBook, setViewBook } = useDesk();
  if (!status) return null;
  return (
    <div className="seg" role="group" aria-label="Which book to show">
      <button aria-pressed={viewBook === "test"} onClick={() => setViewBook("test")}>Test book</button>
      <button aria-pressed={viewBook === "live"} onClick={() => setViewBook("live")}>Live book</button>
    </div>
  );
}

export default function Portfolio() {
  const { overview: o, viewBook } = useDesk();
  const labels = useMemo(() => (o?.curve ?? []).map((p) => shortTs(p.ts)), [o]);
  const series = useMemo(() => o ? [
    { label: "Portfolio value", data: o.curve.map((p) => p.equity), color: "--flow" },
    { label: "Starting capital", data: o.curve.map(() => o.start_capital), color: "--ink-3", dashed: true, width: 1 },
  ] : [], [o]);
  if (!o) return null;
  const kpis: [string, React.ReactNode, React.ReactNode][] = [
    ["Portfolio value", inr(o.equity), <><span className={tone(o.total_pnl)}>{signed(o.total_pnl)} ({pct(o.total_pnl_pct)})</span> since start</>],
    ["Today", o.day_pnl == null ? "–" : <span className={tone(o.day_pnl)}>{signed(o.day_pnl)}</span>, "since the first cycle today"],
    ["Cash", inr(o.cash), `${inr(o.invested)} invested`],
    ["Booked profit", <span className={tone(o.realized)}>{signed(o.realized)}</span>, `${o.positions.length} open position${o.positions.length === 1 ? "" : "s"}`],
  ];
  return (
    <section className="panel span-7" aria-labelledby="pf-h">
      <div className="panel-head">
        <h2 id="pf-h">{viewBook === "live" ? "Live portfolio" : "Test portfolio"}</h2>
        <span className="note">{o.start_capital ? `Started with ${inr(o.start_capital)}` : "Not started"}</span>
        <div className="right"><BookSwitch /></div>
      </div>
      <div className="kpis">
        {kpis.map(([k, v, d]) => <div className="kpi" key={k}><div className="k">{k}</div><div className="v">{v}</div><div className="d muted">{d}</div></div>)}
      </div>
      {o.curve.length > 0
        ? <LineChart labels={labels} series={series} label="Portfolio value over time" />
        : <div className="empty">The value chart starts after the first cycle in this book.</div>}
    </section>
  );
}

export function Positions() {
  const { overview: o, viewBook, toast, refresh } = useDesk();
  if (!o) return null;
  async function fix(symbol: string, exchange: string, current: number) {
    const v = window.prompt(`How many ${symbol} shares does the agent still own? (now ${current})`, String(current));
    if (v == null) return;
    try { await api.fixLive(symbol, exchange, Number(v)); toast("Live book updated"); refresh("all"); }
    catch (e) { toast((e as Error).message); }
  }
  return (
    <section className="panel span-5" aria-labelledby="pos-h">
      <div className="panel-head"><h2 id="pos-h">Open positions</h2><span className="note">{viewBook === "live" ? "Live book" : "Test book"}</span></div>
      <div className="table-wrap">
        <table className="cards">
          {o.positions.length === 0 ? <tbody><tr><td className="empty">No open positions.</td></tr></tbody> : <>
            <thead><tr><th>Stock</th><th className="num">Qty</th><th className="num">Avg / LTP</th><th className="num">P&amp;L</th></tr></thead>
            <tbody>
              {o.positions.map((p) => (
                <tr key={p.symbol}>
                  <td className="sym" data-label="Stock">{p.symbol}<small>{p.sector}{p.product === "MIS" ? ", intraday" : ""}{p.stop ? `, stop ${inr(p.stop, 2)}` : ""}</small>
                    {viewBook === "live" && <button className="btn small" style={{ marginTop: 4 }} onClick={() => fix(p.symbol, p.exchange, p.qty)}>Fix book</button>}</td>
                  <td className="num" data-label="Qty">{p.qty}</td>
                  <td className="num" data-label="Avg / LTP">{inr(p.avg_price, 2)}<br /><span className="faint">{inr(p.ltp, 2)}</span></td>
                  <td className={`num ${tone(p.pnl)}`} data-label="P&L">{signed(p.pnl)}<br />{pct(p.pnl_pct)}</td>
                </tr>
              ))}
            </tbody></>}
        </table>
      </div>
    </section>
  );
}
