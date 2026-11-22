// Model page: training facts, walk-forward folds, holdout metrics, cutoffs, cumulative return, importance.
import { DASH, Poller, anchorClock, badge, esc, isNum, load, num, pct, utcStamp, versionStamp } from './util.js';
import { alpha, baseLayout, plot, tokens } from './charts.js';

const MODEL_LABELS = { model: 'Classifier', base_rate: 'Base rate', ticker_history: 'Ticker history' };
const ratio = (x) => (isNum(x) ? x.toFixed(3) : DASH);
const tone = (x) => (isNum(x) && x > 0 ? 'pos' : isNum(x) && x < 0 ? 'neg' : '');

export function createModel(root) {
  const $ = (sel) => root.querySelector(sel);
  const delayed = badge($('#md-delayed'));
  let data = null;

  function renderFacts() {
    const d = data;
    $('#md-version').textContent = d.version ?? DASH;
    $('#md-version-when').textContent = versionStamp(d.version);
    const facts = [
      ['Trained', utcStamp(d.trained_at)],
      ['Git commit', d.git_sha ?? DASH],
      ['Training rows', num(d.train_rows)],
      ['Holdout rows', num(d.test_rows)],
      ['Holdout starts', utcStamp(d.test_start).replace(/, 00:00 UTC$/, '')],
      ['Short winner', isNum(d.label_return) ? `30-day return < ${pct(d.label_return, 0)}` : DASH],
      ['Holdout base rate', pct(d.base_rate, 1, false)],
      ['Probability cutoff', isNum(d.probability_cutoff) ? `short when p \u2265 ${pct(d.probability_cutoff, 0, false)}` : DASH],
    ];
    $('#md-facts').innerHTML = facts.map(([k, v]) => `<div class="fact"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
    const params = Object.entries(d.params ?? {});
    $('#md-params').innerHTML = params.map(([k, v]) => `<span class="param"><span>${esc(k)}</span><b>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</b></span>`).join('')
      || '<span class="muted">No parameters recorded.</span>';

    const m = d.metrics?.model;
    const parts = [];
    if (isNum(m?.average_precision) && isNum(d.base_rate)) {
      parts.push(`On the holdout, the model's average precision is ${ratio(m.average_precision)}, against ${ratio(d.base_rate)} for a constant predictor${m.average_precision <= d.base_rate ? '; the model does not rank short winners better than chance' : ''}.`);
    }
    if (isNum(m?.n_shorts) && m.n_shorts > 0) {
      parts.push(`The ${num(m.n_shorts)} rows it would short returned a median of ${pct(m.median_return)} and a mean of ${pct(m.short_return)} as shorts.`);
    }
    $('#md-verdict').textContent = parts.join(' ');
  }

  function renderMetrics() {
    const rows = Object.entries(data.metrics ?? {}).filter(([, v]) => v);
    $('#md-metrics').innerHTML = rows.map(([k, v]) => `<tr class="${k === 'model' ? 'hl' : ''}">
      <th scope="row">${esc(MODEL_LABELS[k] ?? k)}</th>
      <td class="num">${esc(ratio(v.average_precision))}</td>
      <td class="num">${esc(ratio(v.roc_auc))}</td>
      <td class="num ${tone(v.median_return)}">${esc(pct(v.median_return, 1))}</td>
      <td class="num">${esc(num(v.n_shorts))}</td></tr>`).join('')
      || '<tr><td colspan="5" class="empty">No metrics recorded.</td></tr>';
  }

  function renderCharts() {
    const k = tokens();
    const L = baseLayout();

    const folds = (data.folds ?? []).filter((f) => f && isNum(f.model_ap));
    $('#md-folds-card').hidden = !folds.length;
    if (folds.length) {
      const hm = data.metrics?.model?.average_precision;
      const hb = data.base_rate;
      const x = [...folds.map((f) => `Fold ${f.fold}`), ...(isNum(hm) ? ['Holdout'] : [])];
      const model = [...folds.map((f) => f.model_ap), ...(isNum(hm) ? [hm] : [])];
      const base = [...folds.map((f) => f.base_rate ?? null), ...(isNum(hm) ? [hb ?? null] : [])];
      const sym = x.map((v) => (v === 'Holdout' ? 'diamond' : 'circle'));
      plot($('#md-folds'), [
        { x, y: base, name: 'Base rate', type: 'scatter', mode: 'lines+markers', line: { color: k.base, width: 2, dash: 'dot' }, marker: { size: 9, symbol: sym, color: k.base, line: { color: k.panel, width: 2 } }, hovertemplate: '%{x}<br>Base rate (constant AP) <b>%{y:.3f}</b><extra></extra>' },
        { x, y: model, name: 'Model', type: 'scatter', mode: 'lines+markers', line: { color: k.accent, width: 2 }, marker: { size: 10, symbol: sym, color: k.accent, line: { color: k.panel, width: 2 } }, hovertemplate: '%{x}<br>Model AP <b>%{y:.3f}</b><extra></extra>' },
      ], baseLayout({
        yaxis: { ...L.yaxis, tickformat: '.2f', title: { text: 'Average precision (higher is better)', font: { color: k.muted, size: 12 } }, zeroline: false },
        xaxis: { ...L.xaxis, type: 'category' },
        shapes: isNum(hm) ? [{ type: 'line', xref: 'x', yref: 'paper', x0: folds.length - 0.5, x1: folds.length - 0.5, y0: 0, y1: 1, line: { color: k.line, width: 1, dash: 'dash' } }] : [],
        hovermode: 'closest',
      }));
    }

    const th = (data.cutoffs ?? []).filter((r) => r && isNum(r.cutoff)).sort((a, b) => a.cutoff - b.cutoff);
    $('#md-thr-card').hidden = !th.length;
    if (th.length) {
      const active = data.probability_cutoff;
      const x = th.map((r) => `\u2265 ${pct(r.cutoff, 0, false)}`);
      const isActive = th.map((r) => Math.abs(r.cutoff - active) < 1e-9);
      plot($('#md-thr'), [
        { x, y: th.map((r) => r.n_shorts), type: 'bar', name: 'Shorts', text: th.map((r) => (isNum(r.n_shorts) ? r.n_shorts.toLocaleString('en-US') : '')), textposition: 'outside', cliponaxis: false, textfont: { color: k.muted, size: 12 }, marker: { color: isActive.map((a) => (a ? k.accent : alpha(k.accent, 0.4))) }, hovertemplate: 'Cutoff %{x}<br><b>%{y:,}</b> shorts<extra></extra>', xaxis: 'x', yaxis: 'y' },
        { x, y: th.map((r) => r.median_return), type: 'scatter', mode: 'lines+markers', name: 'Median short return', line: { color: k.accent2, width: 2 }, marker: { size: 9, color: k.accent2, line: { color: k.panel, width: 2 } }, hovertemplate: 'Cutoff %{x}<br>Median short return <b>%{y:.1%}</b><extra></extra>', xaxis: 'x2', yaxis: 'y2' },
        { x, y: th.map((r) => r.mean_return), type: 'scatter', mode: 'lines+markers', name: 'Mean short return', line: { color: k.base, width: 2, dash: 'dot' }, marker: { size: 7, color: k.base, line: { color: k.panel, width: 2 } }, hovertemplate: 'Cutoff %{x}<br>Mean short return <b>%{y:.1%}</b><extra></extra>', xaxis: 'x2', yaxis: 'y2' },
      ], baseLayout({
        grid: { rows: 2, columns: 1, pattern: 'independent' },
        xaxis: { ...L.xaxis, type: 'category', showticklabels: false, matches: 'x2' },
        xaxis2: { ...L.xaxis, type: 'category', anchor: 'y2', title: { text: 'Short when the probability is', font: { color: k.muted, size: 12 } } },
        yaxis: { ...L.yaxis, domain: [0.56, 1], title: { text: 'Shorts', font: { color: k.muted, size: 12 } }, tickformat: ',' },
        yaxis2: { ...L.yaxis, domain: [0, 0.44], anchor: 'x2', tickformat: '.0%', title: { text: 'Short return', font: { color: k.muted, size: 12 } } },
        showlegend: false, bargap: 0.45, margin: { ...L.margin, t: 8 },
      }));
    }

    const cum = (data.cumulative ?? []).filter((r) => r && r.date);
    $('#md-cum-card').hidden = !cum.length;
    if (cum.length) {
      const last = cum.at(-1);
      plot($('#md-cum'), [
        { x: cum.map((r) => r.date), y: cum.map((r) => r.spy), name: 'Short SPY', type: 'scatter', mode: 'lines', line: { color: k.base, width: 2 }, hovertemplate: 'Short SPY <b>%{y:.1%}</b><extra></extra>' },
        { x: cum.map((r) => r.date), y: cum.map((r) => r.strategy), name: 'Model shorts', type: 'scatter', mode: 'lines', line: { color: k.accent, width: 2 }, hovertemplate: 'Model shorts <b>%{y:.1%}</b><extra></extra>' },
      ], baseLayout({
        yaxis: { ...L.yaxis, tickformat: '.0%' },
        xaxis: { ...L.xaxis, type: 'date', tickformat: '%b %Y', range: [cum[0].date, last.date] },
        hovermode: 'x unified', margin: { ...L.margin, r: 118 },
        annotations: [['strategy', 'Model shorts', k.accent], ['spy', 'Short SPY', k.base]].filter(([key]) => isNum(last[key])).map(([key, label, color]) => ({
          x: last.date, y: last[key], xanchor: 'left', xshift: 8, showarrow: false, text: `${label} ${pct(last[key])}`, font: { color, size: 12 },
        })),
      }));
    }

    const imp = (data.importance ?? []).filter((r) => r && isNum(r.importance)).sort((a, b) => a.importance - b.importance);
    $('#md-imp-card').hidden = !imp.length;
    if (imp.length) {
      plot($('#md-imp'), [{
        y: imp.map((r) => r.feature), x: imp.map((r) => r.importance), type: 'bar', orientation: 'h', marker: { color: k.accent },
        hovertemplate: '%{y}<br>AP falls by <b>%{x:.4f}</b> when shuffled<extra></extra>',
      }], baseLayout({
        xaxis: { ...L.xaxis, showgrid: true, tickformat: '.3f', title: { text: 'Fall in holdout average precision when the feature is shuffled', font: { color: k.muted, size: 12 } } },
        yaxis: { ...L.yaxis, zeroline: false, tickfont: { color: k.ink, size: 12, family: '"SF Mono", ui-monospace, Menlo, monospace' } },
        bargap: 0.35, margin: { ...L.margin, l: 12 }, showlegend: false,
      }));
    }
  }

  async function poll() {
    const d = await load('model', delayed);
    if (!d) return;
    anchorClock(d.generated_at);
    data = d;
    renderFacts();
    renderMetrics();
    renderCharts();
  }

  const poller = new Poller(poll, 5 * 60 * 1000);
  return {
    show() { poller.start(); if (data) renderCharts(); },
    hide() { poller.stop(); },
    rerender() { if (data) renderCharts(); },
  };
}
