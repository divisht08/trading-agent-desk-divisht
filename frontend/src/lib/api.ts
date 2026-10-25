// Typed client for the Agent Desk API.

export type Book = "test" | "live";
export type Mode = "test" | "automatic";

export interface Stage { id: string; label: string }
export interface Session { ok: boolean; user_id?: string; user_name?: string; valid_until?: string }
export interface Status {
  demo: boolean; mode: Mode; book: Book; halted: boolean; running: boolean; training: boolean;
  automatic: { available: boolean; blocked_reason: string; armed_at: string | null; armed_until: string | null;
    live_capital: number | null; reverify_daily: boolean };
  twostep: { enabled: boolean; locked_until: string | null };
  engine: "claude" | "rules";
  model: { version: string; auc: number | null; trained_until: string } | null;
  ml_weight: number;
  session: Session;
  universe_source: string; universe_index: string;
  risk_limits: { max_position_pct: number | null; daily_loss_limit_pct: number | null; stop_loss_pct: number | null; max_positions: number };
  cycle_minutes: number;
  last_run: Run | null;
  password_set: boolean; phone_channels: string[]; stages: Stage[];
}
export interface Run { id: number; started: string; finished: string | null; status: string; trigger: string; book?: Book; summary?: Record<string, unknown> }
export interface FlowEvent { id: number; ts: string; run_id: number | null; stage: string; status: string; message: string; data?: unknown }
export interface Flow { run: Run | null; stages: Record<string, FlowEvent> }
export interface Position {
  symbol: string; exchange: string; sector: string; qty: number; avg_price: number; product: string; ltp: number;
  value: number; pnl: number; pnl_pct: number; day_change_pct: number | null; stance: string | null; stop: number | null; target: number | null;
}
export interface Overview {
  book: Book; cash: number; invested: number; equity: number; start_capital: number; total_pnl: number; total_pnl_pct: number;
  day_pnl: number | null; realized: number; positions: Position[]; curve: { ts: string; equity: number }[];
}
export interface StockSignal {
  symbol: string; exchange: string; sector: string; ltp: number; change_pct: number; score: number; rule_score: number;
  ml_prob: number | null; stance: "bullish" | "bearish" | "neutral"; reasons: string[]; spark: number[]; rsi: number | null; ts: string;
}
export interface SectorSummary { sector: string; n: number; bullish: number; bearish: number; avg_score: number; avg_change: number }
export interface Signals { stocks: StockSignal[]; sectors: SectorSummary[]; as_of: string | null }
export interface Idea {
  id: number; run_id: number; ts: string; book: Book; symbol: string; exchange: string; sector: string; action: string;
  qty: number; price: number; conviction: number; size_pct: number; rationale: string; engine: string; status: string; reason: string;
  debate: { analyst?: string; bull?: string; bear?: string; product?: string; stop?: number | null; target?: number | null };
}
export interface Order {
  id: number; ts: string; book: Book; symbol: string; exchange: string; side: string; qty: number; product: string;
  ref_price: number; fill_price: number | null; charges: number; status: string; kite_order_id: string | null; note: string; pnl: number | null;
}
export interface BacktestMetrics {
  final_equity: number; total_return_pct: number; cagr_pct: number; sharpe: number; max_drawdown_pct: number; trades: number;
  win_rate_pct: number | null; profit_factor: number | null; avg_trade_pct: number | null; benchmark_return_pct?: number; excess_return_pct?: number;
}
export interface BacktestResult {
  id: number; metrics: BacktestMetrics; demo: boolean; model_signals_from: string | null;
  curve: { dates: string[]; equity: number[]; benchmark?: number[] };
  trades: { symbol: string; entry_date: string; entry: number; exit_date: string; exit: number; qty: number; pnl: number; ret_pct: number; reason: string }[];
}
export interface ModelMeta {
  version: string; created: string; trained_until: string; n_train: number; active: boolean;
  metrics: { holdout: { n: number; base_rate: number; auc: number | null; top_decile_hit_rate: number; top_decile_avg_fwd_pct: number; all_avg_fwd_pct: number; lift: number | null }; holdout_from: string; symbols: number };
  importance: { feature: string; importance: number }[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...init });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, (body as { detail?: string }).detail || r.statusText);
  return body as T;
}
const get = <T,>(p: string) => call<T>(p);
const post = <T,>(p: string, data: unknown = {}) => call<T>(p, { method: "POST", body: JSON.stringify(data) });

export const api = {
  me: () => get<{ authenticated: boolean; password_required: boolean }>("/api/auth/me"),
  login: (password: string) => post<{ ok: boolean }>("/api/auth/login", { password }),
  logout: () => post("/api/auth/logout"),
  status: () => get<Status>("/api/status"),
  flow: () => get<Flow>("/api/flow"),
  overview: (book: Book) => get<Overview>(`/api/overview?book=${book}`),
  signals: () => get<Signals>("/api/signals"),
  ideas: (book: Book) => get<Idea[]>(`/api/ideas?book=${book}&limit=150`),
  orders: (book: Book) => get<Order[]>(`/api/orders?book=${book}`),
  events: () => get<FlowEvent[]>("/api/events?limit=100"),
  run: () => post<{ started: boolean; note?: string }>("/api/run"),
  setTest: () => post<Status>("/api/mode", { mode: "test" }),
  armAutomatic: (password: string, code: string) => post<Status>("/api/mode", { mode: "automatic", password, code }),
  halt: (halted: boolean, flatten = false) => post<Status & { flattened: number }>("/api/halt", { halted, flatten }),
  loginLink: () => get<{ url: string }>("/api/login-link"),
  pushLogin: () => post<{ sent: string[] }>("/api/login/push"),
  resetTest: (capital?: number) => post("/api/test/reset", { capital }),
  fixLive: (symbol: string, exchange: string, qty: number) => post("/api/live/fix-position", { symbol, exchange, qty }),
  totpSetup: (password: string) => post<{ secret: string; uri: string }>("/api/security/totp/setup", { password }),
  totpConfirm: (code: string) => post("/api/security/totp/confirm", { code }),
  totpRemove: (password: string, code: string) => post("/api/security/totp/remove", { password, code }),
  models: () => get<{ models: ModelMeta[]; ml_weight: number; min_auc: number; training: boolean }>("/api/models"),
  train: () => post<{ started: boolean; note?: string }>("/api/models/train"),
  activate: (v: string) => post(`/api/models/${v}/activate`),
  deactivate: () => post("/api/models/deactivate"),
  strategies: () => get<{ strategies: Record<string, string>; ml_strategies: string[]; default_start: string; default_end: string }>("/api/backtest/strategies"),
  backtest: (params: Record<string, unknown>) => post<BacktestResult>("/api/backtest", params),
};
