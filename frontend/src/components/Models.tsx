import { useCallback, useEffect, useState } from "react";
import { api, type ModelMeta } from "../lib/api";
import { useDesk } from "../lib/desk";
import { pct } from "../lib/format";

const FEATURE_NAMES: Record<string, string> = {
  ret1: "1-day return", ret5: "5-day return", ret20: "20-day return", ret60: "60-day return", rs20: "20-day vs Nifty",
  rs60: "60-day vs Nifty", dist_sma20: "Distance from 20-day avg", dist_sma50: "Distance from 50-day avg",
  dist_sma200: "Distance from 200-day avg", sma50_over_200: "50 vs 200-day avg", rsi14: "RSI", macd_hist_pct: "MACD",
  atr_pct: "Volatility (ATR)", vol20: "20-day volatility", vol_ratio: "Volume vs normal", vol_ratio5: "5-day volume trend",
  pos_in_52w: "Place in 52-week range", dist_hi20: "Below 20-day high", dist_lo20: "Above 20-day low", gap: "Opening gap",
  range_pct: "Day range", rule_score: "Rule score", bench_ret5: "Nifty 5-day", bench_ret20: "Nifty 20-day", bench_vol20: "Nifty volatility",
};

export default function Models() {
  const { status, toast, modelTick, refresh } = useDesk();
  const [models, setModels] = useState<ModelMeta[]>([]);
  const [meta, setMeta] = useState<{ ml_weight: number; min_auc: number; training: boolean } | null>(null);

  const load = useCallback(() => api.models().then((r) => { setModels(r.models); setMeta(r); }).catch(() => undefined), []);
  useEffect(() => { load(); }, [load, modelTick]);

  const active = models.find((m) => m.active);
  const shown = active ?? models[0];
  const maxImp = Math.max(0.0001, ...(shown?.importance ?? []).map((x) => x.importance));

  async function train() {
    const r = await api.train();
    toast(r.started ? "Training started. Progress shows in the live feed." : r.note ?? "Busy");
    load();
  }
  async function use(v: string | null) {
    if (v) await api.activate(v); else await api.deactivate();
    toast(v ? `Model ${v} is now active` : "Model off: rules only");
    load(); refresh("light");
  }

  return (
    <section className="panel span-12" id="models" aria-labelledby="mdl-h">
      <div className="panel-head">
        <h2 id="mdl-h">Model</h2>
        <span className="note">Gradient-boosted classifier: chance that a stock's next 5 days beat trading costs</span>
        <div className="right">
          {active && <button className="btn" onClick={() => use(null)}>Switch model off</button>}
          <button className="btn primary" onClick={train} disabled={meta?.training || status?.training}>{meta?.training || status?.training ? "Training…" : "Train now"}</button>
        </div>
      </div>
      {!shown ? <div className="empty">No model yet. Train one after logging in to Kite. It retrains itself every Friday after the close.</div> : (
        <div className="controls">
          <div className="control">
            <h3>{shown.active ? "Active model" : "Latest model (not active)"}</h3>
            <dl className="kv">
              <dt>Version</dt><dd>{shown.version}</dd>
              <dt>Trained on data to</dt><dd>{shown.trained_until}</dd>
              <dt>Stocks / rows</dt><dd>{shown.metrics.symbols} / {shown.n_train.toLocaleString("en-IN")}</dd>
              <dt>Holdout from</dt><dd>{shown.metrics.holdout_from}</dd>
              <dt>Holdout AUC</dt><dd>{shown.metrics.holdout.auc ?? "–"}</dd>
              <dt>Base rate (beat costs)</dt><dd>{(shown.metrics.holdout.base_rate * 100).toFixed(1)}%</dd>
              <dt>Top 10% picks hit rate</dt><dd>{(shown.metrics.holdout.top_decile_hit_rate * 100).toFixed(1)}%</dd>
              <dt>Top 10% avg 5-day return</dt><dd>{pct(shown.metrics.holdout.top_decile_avg_fwd_pct)}</dd>
              <dt>All stocks avg 5-day return</dt><dd>{pct(shown.metrics.holdout.all_avg_fwd_pct)}</dd>
              <dt>Weight in the score</dt><dd>{Math.round((meta?.ml_weight ?? 0) * 100)}%</dd>
            </dl>
            <p>AUC 0.5 is a coin flip. New models switch on by themselves only above {meta?.min_auc} and not worse than the current one.</p>
          </div>
          <div className="control">
            <h3>What drives it</h3>
            <p>Drop in holdout AUC when each input is shuffled. Longer bars matter more.</p>
            <div style={{ display: "grid", gap: 4 }}>
              {(shown.importance ?? []).slice(0, 10).map((f) => (
                <div className="imp" key={f.feature}>
                  <span>{FEATURE_NAMES[f.feature] ?? f.feature}</span>
                  <span className="bar" style={{ width: `${Math.max(2, (Math.max(0, f.importance) / maxImp) * 100)}%` }} />
                  <span className="faint num">{f.importance.toFixed(4)}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="control">
            <h3>History</h3>
            <div className="table-wrap"><table>
              <thead><tr><th>Version</th><th className="num">AUC</th><th /></tr></thead>
              <tbody>{models.slice(0, 8).map((m) => (
                <tr key={m.version}><td>{m.version}</td><td className="num">{m.metrics.holdout.auc ?? "–"}</td>
                  <td>{m.active ? <span className="tag status executed">active</span> : <button className="btn small" onClick={() => use(m.version)}>Use</button>}</td></tr>))}
              </tbody></table></div>
          </div>
        </div>
      )}
    </section>
  );
}
