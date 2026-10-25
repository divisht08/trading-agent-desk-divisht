import { useState } from "react";
import { api } from "../lib/api";
import { useDesk } from "../lib/desk";
import { hhmm } from "../lib/format";
import AutomaticDialog from "./AutomaticDialog";

const SECTIONS = [
  ["flow", "Live flow"], ["ideas", "Trade ideas"], ["market", "Bullish and bearish"], ["sectors", "Sectors"],
  ["orders", "Orders and positions"], ["universe", "All stocks"], ["backtest", "Backtest"], ["models", "Model"], ["controls", "Controls"],
] as const;

export default function TopBar() {
  const { status: s, refresh, toast } = useDesk();
  const [arming, setArming] = useState(false);
  if (!s) return null;
  const auto = s.mode === "automatic";

  async function toTest() {
    if (!auto) return;
    await api.setTest();
    toast("Test mode: orders are simulated");
    refresh("all");
  }
  async function run() {
    const r = await api.run();
    toast(r.started ? "Cycle started" : r.note ?? "Busy");
  }
  async function halt() {
    await api.halt(!s!.halted);
    toast(s!.halted ? "Agent resumed" : "Agent stopped");
    refresh("light");
  }

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand">Agent Desk<small>Kite</small></div>
        <div className="modeswitch" role="group" aria-label="Trading mode">
          <button className="test" aria-pressed={!auto} onClick={toTest} title="Simulated orders on live prices">Test</button>
          <button className="auto" aria-pressed={auto} onClick={() => !auto && setArming(true)} title="Real orders on your Kite account">Automatic</button>
        </div>
        {s.demo && <span className="pill demo">Demo data</span>}
        <span className={`pill ${s.session.ok ? "ok" : "bad"}`}>
          <span className="dot" />
          <span>{s.session.ok ? `Kite: ${s.session.user_id} until ${hhmm(s.session.valid_until)}` : "Kite: login needed"}</span>
        </span>
        {s.model && <span className="pill" title={`Holdout AUC ${s.model.auc}`}>Model {s.model.version.slice(0, 8)}</span>}
        <span className="spacer" />
        <button className="btn" onClick={run} disabled={s.running || s.halted}>Run a cycle now</button>
        <button className={`btn ${s.halted ? "go" : "stop"}`} onClick={halt}>{s.halted ? "Resume agent" : "Stop agent"}</button>
      </div>
      <nav className="navrow" aria-label="Sections">
        {SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
      </nav>
      {arming && <AutomaticDialog onClose={() => setArming(false)} />}
    </header>
  );
}
