import { useState } from "react";
import { useDesk } from "../lib/desk";
import { inr, shortTs, signed, tone } from "../lib/format";
import { BookSwitch } from "./Portfolio";
import { ActionTag, StatusTag } from "./Tags";

export function Ideas() {
  const { ideas, viewBook } = useDesk();
  const [filter, setFilter] = useState<"trades" | "all">("trades");
  const rows = filter === "trades" ? ideas.filter((x) => (x.action === "BUY" || x.action === "SELL") && x.status !== "no_action") : ideas;
  return (
    <section className="panel span-12" id="ideas" aria-labelledby="ideas-h">
      <div className="panel-head">
        <h2 id="ideas-h">Trade ideas</h2><span className="note">Every decision in the {viewBook} book, with the debate behind it</span>
        <div className="right">
          <div className="seg" role="group" aria-label="Filter ideas">
            <button aria-pressed={filter === "trades"} onClick={() => setFilter("trades")}>Trades</button>
            <button aria-pressed={filter === "all"} onClick={() => setFilter("all")}>All decisions</button>
          </div>
          <BookSwitch />
        </div>
      </div>
      <div className="table-wrap">
        <table className="cards">
          {rows.length === 0 ? <tbody><tr><td className="empty">No ideas yet. They appear when the desk finishes a debate.</td></tr></tbody> : <>
            <thead><tr><th>Time</th><th>Stock</th><th>Decision</th><th className="num">Qty @ price</th><th className="num">Conviction</th><th>Reasoning</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((x) => {
                const d = x.debate || {};
                return (
                  <tr key={x.id}>
                    <td className="faint" data-label="Time">{shortTs(x.ts)}</td>
                    <td className="sym" data-label="Stock">{x.symbol}<small>{x.sector}</small></td>
                    <td data-label="Decision"><ActionTag action={x.action} /></td>
                    <td className="num" data-label="Qty @ price">{x.qty ? `${x.qty} @ ${inr(x.price, 2)}` : inr(x.price, 2)}
                      {d.stop ? <><br /><span className="faint">stop {inr(d.stop, 2)}{d.target ? `, target ${inr(d.target, 2)}` : ""}</span></> : null}</td>
                    <td className="num" data-label="Conviction">{Math.round((x.conviction || 0) * 100)}%</td>
                    <td className="rationale wide" data-label="Reasoning">{x.rationale}
                      {(d.analyst || d.bull || d.bear) && (
                        <details className="debate"><summary>Read the debate ({x.engine === "claude" ? "Claude" : "rule set"})</summary>
                          <div className="cols"><div><h4>Analyst</h4>{d.analyst}</div><div><h4 className="up">Bull case</h4>{d.bull}</div><div><h4 className="down">Bear case</h4>{d.bear}</div></div>
                        </details>)}
                    </td>
                    <td className="wide" data-label="Status"><StatusTag status={x.status} />
                      {x.reason && x.status !== "executed" && <><br /><span className="faint" style={{ fontSize: ".8rem" }}>{x.reason}</span></>}</td>
                  </tr>
                );
              })}
            </tbody></>}
        </table>
      </div>
    </section>
  );
}

export function Orders() {
  const { orders, viewBook } = useDesk();
  return (
    <section className="panel span-7" id="orders" aria-labelledby="ord-h">
      <div className="panel-head"><h2 id="ord-h">Orders</h2>
        <span className="note">{viewBook === "live" ? "Real orders on Kite" : "Simulated fills with Zerodha charges"}</span></div>
      <div className="table-wrap">
        <table className="cards">
          {orders.length === 0 ? <tbody><tr><td className="empty">No orders yet.</td></tr></tbody> : <>
            <thead><tr><th>Time</th><th>Stock</th><th>Side</th><th className="num">Qty</th><th className="num">Fill</th><th className="num">Charges</th><th className="num">Booked</th><th>Status</th></tr></thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td className="faint" data-label="Time">{shortTs(o.ts)}</td>
                  <td className="sym" data-label="Stock">{o.symbol}<small>{o.exchange}, {o.product}{o.kite_order_id ? `, Kite ${o.kite_order_id}` : ""}</small></td>
                  <td data-label="Side"><span className={`tag ${o.side === "BUY" ? "buy" : "sell"}`}>{o.side === "BUY" ? "▲ Buy" : "▼ Sell"}</span></td>
                  <td className="num" data-label="Qty">{o.qty}</td>
                  <td className="num" data-label="Fill">{o.fill_price ? inr(o.fill_price, 2) : "–"}</td>
                  <td className="num" data-label="Charges">{inr(o.charges, 2)}</td>
                  <td className={`num ${tone(o.pnl)}`} data-label="Booked">{o.pnl ? signed(o.pnl) : "–"}</td>
                  <td data-label="Status"><StatusTag status={o.status} />{o.status !== "COMPLETE" && o.note && <><br /><span className="faint" style={{ fontSize: ".8rem" }}>{o.note}</span></>}</td>
                </tr>
              ))}
            </tbody></>}
        </table>
      </div>
    </section>
  );
}
