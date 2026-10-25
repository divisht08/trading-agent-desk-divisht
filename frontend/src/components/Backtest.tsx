import { useEffect, useMemo, useState } from "react";
import { api, type BacktestResult } from "../lib/api";
import { inr, pct, signed, tone } from "../lib/format";
import LineChart from "./LineChart";

type Form = Record<string, string>;

export default function Backtest() {
  const [strategies, setStrategies] = useState<Record<string, string>>({});
  const [mlStrategies, setMl] = useState<string[]>([]);
  const [form, setForm] = useState<Form>({
    strategy: "screen", start: "", end: "", capital: "100000", max_positions: "10", size_pct: "10", stop_pct: "0",
    entry_score: "40", exit_score: "0", entry_prob: "0.58", exit_prob: "0.5", ml_weight: "0.5", symbols: "",
  });
  const [res, setRes] = useState<BacktestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.strategies().then((s) => {
      setStrategies(s.strategies); setMl(s.ml_strategies);
      setForm((f) => ({ ...f, start: s.default_start, end: s.default_end }));
    }).catch(() => undefined);
  }, []);

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const isMl = mlStrategies.includes(form.strategy);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    const params: Record<string, unknown> = { ...form };
    for (const k of ["capital", "max_positions", "size_pct", "stop_pct", "entry_score", "exit_score", "entry_prob", "exit_prob", "ml_weight"]) params[k] = Number(form[k]);
    try { setRes(await api.backtest(params)); }
    catch (ex) { setErr((ex as Error).message); }
    finally { setBusy(false); }
  }

  const series = useMemo(() => res ? [
    { label: "Strategy", data: res.curve.equity, color: "--flow" },
    ...(res.curve.benchmark ? [{ label: "Nifty 50 (same starting value)", data: res.curve.benchmark, color: "--bench" }] : []),
  ] : [], [res]);

  const m = res?.metrics;
  const items: [string, string, string][] = m ? [
    ["Total return", pct(m.total_return_pct), tone(m.total_return_pct)], ["Nifty 50 same period", pct(m.benchmark_return_pct), ""],
    ["Beat Nifty by", pct(m.excess_return_pct), tone(m.excess_return_pct)], ["Yearly (CAGR)", pct(m.cagr_pct), ""],
    ["Worst drop", pct(m.max_drawdown_pct), "down"], ["Sharpe", String(m.sharpe), ""], ["Trades", String(m.trades), ""],
    ["Win rate", m.win_rate_pct == null ? "–" : `${m.win_rate_pct}%`, ""], ["Profit factor", String(m.profit_factor ?? "–"), ""],
    ["Avg trade", pct(m.avg_trade_pct), tone(m.avg_trade_pct)], ["Final value", inr(m.final_equity), ""],
  ] : [];

  return (
    <section className="panel span-12" id="backtest" aria-labelledby="bt-h">
      <div className="panel-head"><h2 id="bt-h">Backtest a strategy</h2>
        <span className="note">Kite daily bars, next-day open fills, Zerodha charges and slippage included</span></div>
      <form className="form" onSubmit={run}>
        <label className="wide">Strategy<select id="bt-strategy" value={form.strategy} onChange={set("strategy")}>
          {Object.entries(strategies).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label>From<input id="bt-start" type="date" value={form.start} onChange={set("start")} /></label>
        <label>To<input id="bt-end" type="date" value={form.end} onChange={set("end")} /></label>
        <label>Capital (₹)<input id="bt-capital" type="number" min={10000} step={10000} value={form.capital} onChange={set("capital")} /></label>
        <label>Max positions<input id="bt-maxpos" type="number" min={1} max={50} value={form.max_positions} onChange={set("max_positions")} /></label>
        <label>Size per trade (%)<input id="bt-size" type="number" min={1} max={100} step={0.5} value={form.size_pct} onChange={set("size_pct")} /></label>
        <label>Stop loss (%)<input id="bt-stop" type="number" min={0} max={50} step={0.5} value={form.stop_pct} onChange={set("stop_pct")} /></label>
        {form.strategy === "ml" ? <>
          <label>Buy at probability<input id="bt-eprob" type="number" min={0.5} max={0.95} step={0.01} value={form.entry_prob} onChange={set("entry_prob")} /></label>
          <label>Sell below probability<input id="bt-xprob" type="number" min={0.3} max={0.9} step={0.01} value={form.exit_prob} onChange={set("exit_prob")} /></label>
        </> : <>
          <label>Entry score<input id="bt-entry" type="number" min={-100} max={100} value={form.entry_score} onChange={set("entry_score")} /></label>
          <label>Exit score<input id="bt-exit" type="number" min={-100} max={100} value={form.exit_score} onChange={set("exit_score")} /></label>
        </>}
        {form.strategy === "blend" && <label>Model weight<input id="bt-w" type="number" min={0} max={1} step={0.05} value={form.ml_weight} onChange={set("ml_weight")} /></label>}
        <label className="wide">Symbols (blank = first 40 in universe)<input id="bt-symbols" type="text" placeholder="RELIANCE, TCS, HDFCBANK" value={form.symbols} onChange={set("symbols")} /></label>
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Running…" : "Run backtest"}</button>
      </form>
      {busy && <p className="muted">{isMl ? "Training walk-forward models and simulating. This takes a minute or two." : "Fetching history and simulating."}</p>}
      {err && <p className="down">{err}</p>}
      {res && m && <>
        {res.demo && <div className="banner" style={{ padding: 0, margin: "0 0 12px" }}><div className="warn">These results use synthetic demo prices and say nothing about real markets.</div></div>}
        {res.model_signals_from && <p className="faint" style={{ marginBottom: 8 }}>Model signals start {res.model_signals_from}, after the first year of training data. Earlier dates hold cash.</p>}
        <div className="metrics">{items.map(([k, v, c]) => <div className="metric" key={k}><div className="k">{k}</div><div className={`v ${c}`}>{v}</div></div>)}</div>
        <LineChart labels={res.curve.dates} series={series} label="Strategy value against Nifty 50" height={300} />
        <details style={{ marginTop: 12 }}><summary>Trades ({res.trades.length})</summary>
          <div className="table-wrap"><table>
            <thead><tr><th>Stock</th><th>Bought</th><th>Sold</th><th className="num">Qty</th><th className="num">Return</th><th className="num">P&amp;L</th><th>Exit</th></tr></thead>
            <tbody>{res.trades.slice().reverse().map((t, i) => (
              <tr key={i}><td className="sym">{t.symbol}</td><td>{t.entry_date} at {inr(t.entry, 2)}</td><td>{t.exit_date} at {inr(t.exit, 2)}</td>
                <td className="num">{t.qty}</td><td className={`num ${tone(t.ret_pct)}`}>{pct(t.ret_pct)}</td><td className={`num ${tone(t.pnl)}`}>{signed(t.pnl)}</td><td>{t.reason}</td></tr>))}
            </tbody></table></div>
        </details>
      </>}
      <p className="callout">Rule strategies and the ML model are tested walk-forward, so the model only ever sees data from before each trade.
        The Claude debate can't be backtested fairly: the model already knows how past prices played out. Judge it from Test-mode results.</p>
    </section>
  );
}
