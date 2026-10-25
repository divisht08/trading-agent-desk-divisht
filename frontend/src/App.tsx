import { useEffect, useState } from "react";
import Backtest from "./components/Backtest";
import Banners from "./components/Banners";
import Controls from "./components/Controls";
import { FlowRail, Tape } from "./components/FlowPanel";
import { BullBear, MarketRead, Sectors, Universe } from "./components/Market";
import Models from "./components/Models";
import Portfolio, { Positions } from "./components/Portfolio";
import TopBar from "./components/TopBar";
import { Ideas, Orders } from "./components/Trades";
import { api } from "./lib/api";
import { DeskProvider, useDesk } from "./lib/desk";

function SignIn({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="signin-page">
      <form className="signin-card" onSubmit={async (e) => {
        e.preventDefault();
        try { await api.login(pw); onDone(); } catch { setErr("That password didn't match."); }
      }}>
        <h1>Agent Desk</h1>
        <p>Enter the dashboard password.</p>
        {err && <p className="err" role="alert">{err}</p>}
        <input id="signin-pw" type="password" autoFocus autoComplete="current-password" aria-label="Password" value={pw} onChange={(e) => setPw(e.target.value)} />
        <button type="submit" className="btn primary">Sign in</button>
      </form>
    </div>
  );
}

function Toast() {
  const { toastMsg } = useDesk();
  return toastMsg ? <div className="toast" role="status">{toastMsg}</div> : null;
}

function Desk() {
  return (
    <>
      <TopBar />
      <Banners />
      <main>
        <FlowRail />
        <Tape />
        <Portfolio />
        <Ideas />
        <BullBear />
        <MarketRead />
        <Sectors />
        <Orders />
        <Positions />
        <Universe />
        <Backtest />
        <Models />
        <Controls />
      </main>
      <footer>Prices and decisions come from your Kite account and the agent's own logs. Nothing here is investment advice.</footer>
      <Toast />
    </>
  );
}

export default function App() {
  const [auth, setAuth] = useState<"loading" | "in" | "out">("loading");
  useEffect(() => { api.me().then((m) => setAuth(m.authenticated ? "in" : "out")).catch(() => setAuth("out")); }, []);
  if (auth === "loading") return null;
  if (auth === "out") return <SignIn onDone={() => setAuth("in")} />;
  return <DeskProvider><Desk /></DeskProvider>;
}
