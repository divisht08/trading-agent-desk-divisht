import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { useDesk } from "../lib/desk";
import { inr } from "../lib/format";

// Two-step switch to Automatic (real money): password, then authenticator code.
export default function AutomaticDialog({ onClose }: { onClose: () => void }) {
  const { status: s, refresh, toast } = useDesk();
  const [step, setStep] = useState<1 | 2>(1);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!s) return null;

  const checks: [boolean, string][] = [
    [!s.demo, "Using your real Kite data (not demo)"],
    [s.automatic.available, s.automatic.available ? `Server allows real orders, budget ${inr(s.automatic.live_capital)}` : `Server switch: ${s.automatic.blocked_reason}`],
    [s.session.ok, "Logged in to Kite today"],
    [s.orders_session.configured && s.orders_session.ok, s.orders_session.configured
      ? "Logged in to Kite for orders today (Controls, Kite login)"
      : "Kite Connect API key for orders is set (the free Personal plan is enough)"],
    [s.password_set, "Dashboard password is set"],
    [s.twostep.enabled, "Authenticator app is set up (Controls, Two-step verification)"],
  ];
  const ready = checks.every(([ok]) => ok) && s.automatic.available;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (step === 1) {
      if (!password) return setErr("Enter your dashboard password");
      setErr(null);
      setStep(2);
      return;
    }
    setBusy(true);
    try {
      await api.armAutomatic(password, code);
      toast("Automatic mode is on: the agent places real orders");
      await refresh("all");
      onClose();
    } catch (ex) {
      const msg = ex instanceof ApiError ? ex.message : "Something went wrong";
      setErr(msg);
      if (/password/i.test(msg)) { setStep(1); setPassword(""); }
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="auto-h" onSubmit={submit}>
        <h2 id="auto-h">Turn on Automatic mode</h2>
        <p>The agent will place real orders on your Kite account without asking, within a budget of {inr(s.automatic.live_capital)}.
          {s.automatic.reverify_daily ? " You'll verify again each morning." : " It stays on until you switch back to Test."}</p>
        <ul className="checks">
          {checks.map(([ok, text]) => <li key={text}><span className={ok ? "ok" : "no"}>{ok ? "✓" : "✕"}</span><span>{text}</span></li>)}
        </ul>
        {ready ? (
          <>
            <div className="steps" aria-hidden="true"><span className="on">1. Password</span><span className={step === 2 ? "on" : ""}>2. Authenticator code</span></div>
            {step === 1 ? (
              <label>Dashboard password
                <input id="auto-pw" type="password" autoComplete="current-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
            ) : (
              <label>6-digit code from your authenticator app
                <input id="auto-code" className="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} autoFocus
                  value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
              </label>
            )}
          </>
        ) : <p>Fix the items marked ✕ first. Server settings are in the .env file; see the README.</p>}
        {err && <p className="err" role="alert">{err}</p>}
        <div className="actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          {ready && (step === 1
            ? <button type="submit" className="btn primary">Continue</button>
            : <button type="submit" className="btn stop" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Turn on Automatic"}</button>)}
        </div>
      </form>
    </div>
  );
}
