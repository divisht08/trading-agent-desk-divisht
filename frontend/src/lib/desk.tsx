// Shared dashboard state: loads everything, listens to the live event stream, refreshes what changed.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type Book, type Flow, type FlowEvent, type Idea, type Order, type Overview, type Signals, type Status } from "./api";

interface DeskState {
  status: Status | null; flow: Flow | null; overview: Overview | null; signals: Signals | null;
  ideas: Idea[]; orders: Order[]; events: FlowEvent[]; freshId: number | null;
  viewBook: Book; setViewBook: (b: Book) => void;
  refresh: (what?: "light" | "all") => Promise<void>;
  toast: (msg: string) => void; toastMsg: string | null;
  modelTick: number;
}

const Ctx = createContext<DeskState | null>(null);

export function useDesk(): DeskState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDesk outside provider");
  return v;
}

export function DeskProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [signals, setSignals] = useState<Signals | null>(null);
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [events, setEvents] = useState<FlowEvent[]>([]);
  const [freshId, setFreshId] = useState<number | null>(null);
  const [viewBookState, setViewBookState] = useState<Book | null>(null);
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const [modelTick, setModelTick] = useState(0);
  const timer = useRef<number | undefined>(undefined);
  const pendingFull = useRef(false);

  const viewBook: Book = viewBookState ?? status?.book ?? "test";
  const viewBookRef = useRef(viewBook);
  viewBookRef.current = viewBook;

  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.setTimeout(() => setToastMsg((m) => (m === msg ? null : m)), 3800);
  }, []);

  const refresh = useCallback(async (what: "light" | "all" = "all") => {
    const b = viewBookRef.current;
    const [st, fl] = await Promise.all([api.status(), api.flow()]);
    setStatus(st); setFlow(fl);
    if (what === "all") {
      const [ov, sg, id, od] = await Promise.all([api.overview(b), api.signals(), api.ideas(b), api.orders(b)]);
      setOverview(ov); setSignals(sg); setIdeas(id); setOrders(od);
    }
  }, []);

  const schedule = useCallback((full: boolean) => {
    pendingFull.current = pendingFull.current || full;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const f = pendingFull.current;
      pendingFull.current = false;
      refresh(f ? "all" : "light").catch(() => undefined);
    }, 350);
  }, [refresh]);

  useEffect(() => {
    let es: EventSource | null = null;
    let poll: number | undefined;
    (async () => {
      await refresh("all");
      const evs = await api.events();
      setEvents(evs.slice().reverse());
      const last = evs.length ? evs[evs.length - 1].id : 0;
      es = new EventSource(`/api/stream?after=${last}`);
      es.addEventListener("flow", (m) => {
        const ev = JSON.parse((m as MessageEvent).data) as FlowEvent;
        setEvents((cur) => [ev, ...cur].slice(0, 200));
        setFreshId(ev.id);
        if (ev.stage === "model") setModelTick((t) => t + 1);
        schedule(["cycle", "execute", "security", "reconcile", "model"].includes(ev.stage) || (ev.stage === "signals" && ev.status === "done"));
      });
      poll = window.setInterval(() => schedule(false), 30000);
    })().catch(() => toast("Could not reach the desk"));
    return () => { es?.close(); window.clearInterval(poll); };
  }, [refresh, schedule, toast]);

  const setViewBook = useCallback((b: Book) => {
    setViewBookState(b);
    viewBookRef.current = b;
    refresh("all").catch(() => undefined);
  }, [refresh]);

  const value = useMemo(() => ({
    status, flow, overview, signals, ideas, orders, events, freshId, viewBook, setViewBook, refresh, toast, toastMsg, modelTick,
  }), [status, flow, overview, signals, ideas, orders, events, freshId, viewBook, setViewBook, refresh, toast, toastMsg, modelTick]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
