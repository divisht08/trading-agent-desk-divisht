import { useMemo, useState } from "react";
import { useDesk } from "../lib/desk";
import { hhmm, inr, pct, tone } from "../lib/format";
import Spark from "./Spark";
import { StanceTag } from "./Tags";

export function BullBear() {
  const { signals } = useDesk();
  const rows = useMemo(() => {
    const st = signals?.stocks ?? [];
    const bulls = st.filter((x) => x.stance === "bullish").slice(0, 10);
    const bears = st.filter((x) => x.stance === "bearish").sort((a, b) => a.score - b.score).slice(0, 10).reverse();
    return [...bulls, ...bears];
  }, [signals]);
  return (
    <section className="panel span-7" id="market" aria-labelledby="bb-h">
      <div className="panel-head"><h2 id="bb-h">Bullish and bearish</h2>
        <span className="note">{signals?.as_of ? `Score from −100 to +100, as of ${hhmm(signals.as_of)}` : ""}</span></div>
      <div className="split-head"><span className="down">▼ Most bearish</span><span className="up">Most bullish ▲</span></div>
      <div className="diverge-axis"><span /><span>−100</span><span>+100</span><span /></div>
      <div className="diverge">
        {rows.length === 0 && <div className="empty">No signals yet. They appear after the first cycle.</div>}
        {rows.map((x) => {
          const bar = <span className="bar" style={{ width: `${Math.min(100, Math.abs(x.score))}%` }} />;
          const tip = `${x.symbol}: score ${x.score > 0 ? "+" : ""}${x.score}` + (x.ml_prob != null ? `, model ${(x.ml_prob * 100).toFixed(0)}%` : "") + `. ${x.reasons.slice(0, 3).join("; ")}`;
          return (
            <div className="drow" key={x.symbol} title={tip}>
              <span className="name">{x.symbol}</span>
              <span className="l">{x.score < 0 ? bar : null}</span><span className="r">{x.score > 0 ? bar : null}</span>
              <span className={`val ${tone(x.score)}`}>{x.score > 0 ? "▲" : "▼"}{Math.abs(x.score).toFixed(0)}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function MarketRead() {
  const { signals, status } = useDesk();
  const st = signals?.stocks ?? [];
  const secs = signals?.sectors ?? [];
  const nb = st.filter((x) => x.stance === "bullish").length, nr = st.filter((x) => x.stance === "bearish").length, nn = st.length - nb - nr;
  const withModel = st.filter((x) => x.ml_prob != null);
  const avgProb = withModel.length ? withModel.reduce((a, x) => a + (x.ml_prob ?? 0), 0) / withModel.length : null;
  return (
    <section className="panel span-5" aria-labelledby="mix-h">
      <div className="panel-head"><h2 id="mix-h">Market read</h2><span className="note">How the screen splits the universe</span></div>
      {st.length === 0 ? <div className="empty">Waiting for the first cycle.</div> : <>
        <div role="img" aria-label={`${nb} bullish, ${nn} neutral, ${nr} bearish`} style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden", gap: 2, margin: "4px 0 12px" }}>
          <span style={{ flex: nb, background: "var(--bull)" }} /><span style={{ flex: nn, background: "var(--neutral)" }} /><span style={{ flex: nr, background: "var(--bear)" }} />
        </div>
        <dl className="kv">
          <dt className="up">▲ Bullish</dt><dd>{nb}</dd><dt>Neutral</dt><dd>{nn}</dd><dt className="down">▼ Bearish</dt><dd>{nr}</dd>
          <dt>Stocks scored</dt><dd>{st.length}</dd>
          <dt>Strongest sector</dt><dd>{secs[0]?.sector}</dd><dt>Weakest sector</dt><dd>{secs[secs.length - 1]?.sector}</dd>
          <dt>Scoring</dt><dd>{status?.model ? `rules ${Math.round((1 - status.ml_weight) * 100)}% + model ${Math.round(status.ml_weight * 100)}%` : "rules only"}</dd>
          {avgProb != null && <><dt>Average model probability</dt><dd>{(avgProb * 100).toFixed(0)}%</dd></>}
        </dl>
        <p className="faint" style={{ fontSize: ".8rem", marginTop: 10 }}>Universe {status?.universe_index}, sectors from {status?.universe_source || "–"}</p>
      </>}
    </section>
  );
}

export function Sectors() {
  const { signals } = useDesk();
  const secs = signals?.sectors ?? [];
  return (
    <section className="panel span-12" id="sectors" aria-labelledby="sec-h">
      <div className="panel-head"><h2 id="sec-h">Sectors being analysed</h2>
        <div className="right legend"><span>Bearish</span><span className="ramp" aria-hidden="true" /><span>Bullish</span></div></div>
      <div className="sectors">
        {secs.length === 0 && <div className="empty">Sectors appear after the first cycle.</div>}
        {secs.map((s) => {
          const k = Math.min(1, Math.abs(s.avg_score) / 60) * 42;
          const col = s.avg_score >= 0 ? "var(--bull)" : "var(--bear)";
          return (
            <div className="sector" key={s.sector} title={`${s.sector}: average score ${s.avg_score}, ${s.bullish} bullish, ${s.bearish} bearish`}>
              <span className="heat" style={{ background: `color-mix(in srgb, ${col} ${k}%, var(--panel))` }} />
              <div className="nm">{s.sector}</div>
              <div className="sc">{s.avg_score > 0 ? "▲ +" : s.avg_score < 0 ? "▼ " : ""}{s.avg_score}</div>
              <div className="ct">{s.n} stock{s.n > 1 ? "s" : ""}: {s.bullish} bullish, {s.bearish} bearish. Today {pct(s.avg_change)}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function Universe() {
  const { signals } = useDesk();
  const [q, setQ] = useState(""), [sector, setSector] = useState(""), [stance, setStance] = useState(""), [all, setAll] = useState(false);
  const stocks = signals?.stocks ?? [];
  const rows = stocks.filter((x) => (!q || x.symbol.includes(q.toUpperCase())) && (!sector || x.sector === sector) && (!stance || x.stance === stance));
  return (
    <section className="panel span-12" id="universe" aria-labelledby="uni-h">
      <div className="panel-head"><h2 id="uni-h">All stocks in scope</h2><span className="note">{rows.length} of {stocks.length} match, sorted by score</span></div>
      <div className="filters">
        <input id="uni-q" type="search" placeholder="Search symbol" aria-label="Search symbol" value={q} onChange={(e) => setQ(e.target.value)} />
        <select id="uni-sector" aria-label="Sector" value={sector} onChange={(e) => setSector(e.target.value)}>
          <option value="">All sectors</option>{(signals?.sectors ?? []).map((s) => <option key={s.sector}>{s.sector}</option>)}
        </select>
        <select id="uni-stance" aria-label="Stance" value={stance} onChange={(e) => setStance(e.target.value)}>
          <option value="">Any stance</option><option value="bullish">Bullish</option><option value="neutral">Neutral</option><option value="bearish">Bearish</option>
        </select>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Stock</th><th className="num">Price</th><th className="num">Today</th><th className="num">Score</th><th className="num">Rules</th><th className="num">Model</th><th>Stance</th><th>30 days</th><th>Why</th></tr></thead>
          <tbody>
            {rows.slice(0, all ? 2000 : 25).map((x) => (
              <tr key={x.symbol}>
                <td className="sym">{x.symbol}<small>{x.exchange}, {x.sector}</small></td>
                <td className="num">{inr(x.ltp, 2)}</td><td className={`num ${tone(x.change_pct)}`}>{pct(x.change_pct)}</td>
                <td className={`num ${tone(x.score)}`}>{x.score > 0 ? "+" : ""}{x.score}</td>
                <td className="num faint">{x.rule_score > 0 ? "+" : ""}{x.rule_score}</td>
                <td className="num">{x.ml_prob == null ? "–" : `${(x.ml_prob * 100).toFixed(0)}%`}</td>
                <td><StanceTag stance={x.stance} /></td><td><Spark values={x.spark} /></td>
                <td className="rationale">{x.reasons.join("; ")}</td>
              </tr>
            ))}
          </tbody>
          {rows.length > 25 && <tfoot><tr><td colSpan={9}><button className="btn small" onClick={() => setAll(!all)}>{all ? "Show top 25" : `Show all ${rows.length}`}</button></td></tr></tfoot>}
        </table>
      </div>
    </section>
  );
}
