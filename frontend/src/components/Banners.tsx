import { api } from "../lib/api";
import { useDesk } from "../lib/desk";
import { inr } from "../lib/format";

export default function Banners() {
  const { status: s, toast } = useDesk();
  if (!s) return null;
  const items: [string, React.ReactNode][] = [];
  if (s.mode === "automatic") items.push(["bad", <>Automatic mode: the agent is placing <strong>real orders</strong> on your Kite account (budget {inr(s.automatic.live_capital)}).</>]);
  if (s.demo) items.push(["warn", "Demo data: prices are synthetic so you can try the desk. Set DATA_MODE=kite to use your Kite account."]);
  if (s.halted) items.push(["bad", "The agent is stopped. Nothing runs until you resume it."]);
  if (!s.session.ok) items.push(["bad", <>Kite needs today's login. <button className="btn small primary" onClick={async () => {
    const r = await api.pushLogin();
    toast(r.sent.length ? `Login link sent to ${r.sent.join(" and ")}` : "No phone alerts set up. Use Log in here under Controls.");
  }}>Send link to my phone</button></>]);
  if (!s.password_set) items.push(["warn", "No dashboard password is set. Set DASHBOARD_PASSWORD before putting this online. Automatic mode stays off without it."]);
  if (s.phone_channels.length === 0) items.push(["info", "No phone alerts configured. Add NTFY_TOPIC or a Telegram bot so login links and fills reach you."]);
  if (!items.length) return null;
  return <div className="banner">{items.map(([k, m], i) => <div key={i} className={k}>{m}</div>)}</div>;
}
