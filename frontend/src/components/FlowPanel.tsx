import { useDesk } from "../lib/desk";
import { hhmm } from "../lib/format";

const STATE_TEXT: Record<string, string> = {
  ok: "Idle until the next cycle", needs_login: "Waiting for your Kite login", error: "Last cycle failed", halted: "Stopped by you",
};

export function FlowRail() {
  const { status: s, flow } = useDesk();
  if (!s || !flow) return null;
  const order = s.stages.map((x) => x.id);
  let lastDone = -1;
  order.forEach((id, i) => { if (flow.stages[id]?.status === "done") lastDone = i; });
  const run = flow.run;

  let state = "Waiting for the first cycle";
  if (s.halted) state = "Stopped by you";
  else if (s.running || run?.status === "running") {
    const active = order.find((id) => flow.stages[id]?.status === "active");
    state = "Working: " + (active ? s.stages.find((x) => x.id === active)!.label.toLowerCase() : "starting");
  } else if (run) state = STATE_TEXT[run.status] ?? run.status;

  let meta = "";
  if (run) {
    const took = run.finished ? Math.max(1, Math.round((+new Date(run.finished) - +new Date(run.started)) / 1000)) : null;
    meta = `Cycle ${run.id} in the ${run.book === "live" ? "live" : "test"} book, ${run.finished ? `finished at ${hhmm(run.finished)}${took ? `, took ${took}s` : ""}` : `started at ${hhmm(run.started)}`}`;
    if (run.finished && run.status === "ok" && !s.halted) {
      const next = new Date(+new Date(run.finished) + s.cycle_minutes * 60000);
      meta += `. Next around ${next.toTimeString().slice(0, 5)} during market hours`;
    }
  }

  return (
    <section className="panel circuit span-12" id="flow" aria-labelledby="flow-h">
      <div className="circuit-head">
        <h2 id="flow-h">Right now</h2>
        <span className="state">{state}</span>
        <span className="meta">{meta}</span>
      </div>
      <div className="rail">
        <div className="track-fill" style={{ width: lastDone < 0 ? 0 : `${(lastDone / (order.length - 1)) * 88}%` }} />
        {s.stages.map((st, i) => {
          const ev = flow.stages[st.id];
          return (
            <div key={st.id} className="station" data-status={ev?.status ?? "idle"} title={ev ? `${hhmm(ev.ts)}: ${ev.message}` : "Not reached this cycle"}>
              <div className="node">{i + 1}</div>
              <div className="label">{st.label}</div>
              <div className="out">{ev?.message ?? ""}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

const STAGE_NAME: Record<string, string> = { cycle: "Cycle", security: "Security", model: "Model" };

export function Tape() {
  const { status: s, events, freshId } = useDesk();
  const label = (id: string) => s?.stages.find((x) => x.id === id)?.label ?? STAGE_NAME[id] ?? id;
  return (
    <section className="panel span-5" aria-labelledby="tape-h">
      <div className="panel-head"><h2 id="tape-h">What the agent is doing</h2><span className="note">Live, newest first</span></div>
      <ul className="tape" aria-live="polite">
        {events.length === 0 && <li><span /><span /><span className="m muted">Nothing yet. Press "Run a cycle now" or wait for market hours.</span></li>}
        {events.map((e) => (
          <li key={e.id} data-status={e.status} className={e.id === freshId ? "fresh" : undefined}>
            <span className="t">{hhmm(e.ts)}</span><span className="s">{label(e.stage)}</span><span className="m">{e.message}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
