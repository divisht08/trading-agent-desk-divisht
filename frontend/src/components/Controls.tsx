import QRCode from "qrcode";
import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { useDesk } from "../lib/desk";
import { inr } from "../lib/format";

function TwoStepSetup({ onDone }: { onDone: () => void }) {
  const { toast } = useDesk();
  const [password, setPassword] = useState("");
  const [setup, setSetup] = useState<{ secret: string; uri: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function start(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try {
      const r = await api.totpSetup(password);
      setSetup({ ...r, qr: await QRCode.toDataURL(r.uri, { margin: 1, width: 180 }) });
    } catch (ex) { setErr(ex instanceof ApiError ? ex.message : "Setup failed"); }
  }
  async function confirm(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try { await api.totpConfirm(code); toast("Two-step verification is on"); onDone(); }
    catch (ex) { setErr(ex instanceof ApiError ? ex.message : "That code didn't match"); setCode(""); }
  }

  return (
    <div className="scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onDone()}>
      {!setup ? (
        <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="ts-h" onSubmit={start}>
          <h2 id="ts-h">Set up two-step verification</h2>
          <p>Automatic mode needs your password plus a code from an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, Authy).</p>
          <label>Dashboard password<input id="ts-pw" type="password" autoFocus autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          {err && <p className="err" role="alert">{err}</p>}
          <div className="actions"><button type="button" className="btn" onClick={onDone}>Cancel</button><button className="btn primary" type="submit">Continue</button></div>
        </form>
      ) : (
        <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="ts-h2" onSubmit={confirm}>
          <h2 id="ts-h2">Scan with your authenticator app</h2>
          <img className="qr" src={setup.qr} alt="QR code for your authenticator app" />
          <p>Can't scan? Add this key by hand:</p>
          <div className="secret">{setup.secret}</div>
          <label>Then enter the 6-digit code it shows
            <input id="ts-code" className="code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </label>
          {err && <p className="err" role="alert">{err}</p>}
          <div className="actions"><button type="button" className="btn" onClick={onDone}>Cancel</button><button className="btn primary" type="submit" disabled={code.length !== 6}>Turn on two-step</button></div>
        </form>
      )}
    </div>
  );
}

export default function Controls() {
  const { status: s, toast, refresh } = useDesk();
  const [setupOpen, setSetupOpen] = useState(false);
  if (!s) return null;
  const rl = s.risk_limits;
  const off = (v: number | null, u = "%") => (v == null ? "off" : `${v}${u}`);

  async function pushLogin() {
    const r = await api.pushLogin();
    toast(r.sent.length ? `Login link sent to ${r.sent.join(" and ")}` : "No phone alerts set up. Use Log in here.");
  }
  async function openLogin() {
    const r = await api.loginLink();
    window.open(r.url, "_blank", "noopener");
  }
  async function flatten() {
    if (!window.confirm(`Stop the agent and close every position in the ${s!.book} book at market?`)) return;
    const r = await api.halt(true, true);
    toast(`Agent stopped, ${r.flattened} positions closed`);
    refresh("all");
  }
  async function resetTest() {
    if (!window.confirm("Clear the test book and start again with fresh test capital?")) return;
    await api.resetTest(); toast("Test book reset"); refresh("all");
  }
  async function removeTotp() {
    const pw = window.prompt("Dashboard password");
    if (!pw) return;
    const code = window.prompt("Current 6-digit authenticator code");
    if (!code) return;
    try { await api.totpRemove(pw, code); toast("Two-step verification removed; back in Test mode"); refresh("all"); }
    catch (e) { toast((e as Error).message); }
  }

  return (
    <section className="panel span-12" id="controls" aria-labelledby="ctl-h">
      <div className="panel-head"><h2 id="ctl-h">Controls</h2></div>
      <div className="controls">
        <div className="control">
          <h3>Kite login</h3>
          <p>Kite logs every app out each morning. The agent sends a login link to your phone at the reminder time and whenever a cycle finds no session.</p>
          <dl className="kv">
            <dt>Status</dt><dd>{s.session.ok ? "Logged in" : "Not logged in"}</dd>
            {s.session.ok && <><dt>User</dt><dd>{s.session.user_name || s.session.user_id}</dd>
              <dt>Valid until</dt><dd>{(s.session.valid_until || "").replace("T", " ").slice(0, 16)}</dd></>}
            <dt>Phone alerts</dt><dd>{s.phone_channels.length ? s.phone_channels.join(", ") : "not set up"}</dd>
          </dl>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" onClick={pushLogin}>Send login link to my phone</button>
            <button className="btn" onClick={openLogin}>Log in here</button>
          </div>
        </div>
        <div className="control">
          <h3>Trading mode</h3>
          <p>{s.mode === "automatic"
            ? `Automatic: real orders, budget ${inr(s.automatic.live_capital)}. Switch to Test in the top bar to stop real orders at once.`
            : "Test: every decision is simulated on live prices with Zerodha charges and slippage. Switch to Automatic in the top bar."}</p>
          <dl className="kv">
            <dt>Real orders allowed by server</dt><dd>{s.automatic.available ? "yes" : `no (${s.automatic.blocked_reason})`}</dd>
            <dt>Live budget</dt><dd>{inr(s.automatic.live_capital)}</dd>
            <dt>Verify again each morning</dt><dd>{s.automatic.reverify_daily ? "yes" : "no"}</dd>
            {s.automatic.armed_at && <><dt>Last turned on</dt><dd>{s.automatic.armed_at.replace("T", " ").slice(0, 16)}</dd></>}
          </dl>
          <button className="btn" onClick={resetTest}>Reset the test book</button>
        </div>
        <div className="control">
          <h3>Two-step verification</h3>
          <p>Needed to switch on Automatic: your password plus a 6-digit code from an authenticator app. Five wrong tries lock it for 15 minutes.</p>
          <dl className="kv"><dt>Status</dt><dd>{s.twostep.enabled ? "on" : "off"}</dd>
            {s.twostep.locked_until && <><dt>Locked until</dt><dd>{s.twostep.locked_until.slice(11, 16)}</dd></>}</dl>
          {s.twostep.enabled
            ? <button className="btn" onClick={removeTotp}>Remove authenticator</button>
            : <button className="btn primary" onClick={() => setSetupOpen(true)} disabled={!s.password_set}>Set up authenticator</button>}
          {!s.password_set && <p className="down">Set DASHBOARD_PASSWORD on the server first.</p>}
        </div>
        <div className="control">
          <h3>Stop switch</h3>
          <p>Stopping blocks every future cycle until you resume. You can also close all open positions in the current book at market.</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className={`btn ${s.halted ? "go" : "stop"}`} onClick={async () => { await api.halt(!s.halted); refresh("light"); }}>{s.halted ? "Resume agent" : "Stop agent"}</button>
            <button className="btn" onClick={flatten}>Stop and close all positions</button>
          </div>
        </div>
        <div className="control">
          <h3>Risk limits</h3>
          <p>Set in the server's .env file. Blank means off.</p>
          <dl className="kv">
            <dt>Max per position</dt><dd>{off(rl.max_position_pct)}</dd><dt>Daily loss stop</dt><dd>{off(rl.daily_loss_limit_pct)}</dd>
            <dt>Stop loss per trade</dt><dd>{off(rl.stop_loss_pct)}</dd><dt>Max open positions</dt><dd>{rl.max_positions}</dd>
            <dt>Cycle every</dt><dd>{s.cycle_minutes} min</dd><dt>Decision desk</dt><dd>{s.engine === "claude" ? "Claude debate" : "Rule set (no API key)"}</dd>
          </dl>
        </div>
      </div>
      {setupOpen && <TwoStepSetup onDone={() => { setSetupOpen(false); refresh("light"); }} />}
    </section>
  );
}
