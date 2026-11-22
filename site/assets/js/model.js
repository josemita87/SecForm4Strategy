// Model page: training facts, walk-forward folds, holdout metrics, thresholds, cumulative return, importance.
import { DASH, Poller, anchorClock, badge, esc, isNum, load, num, pct, utcStamp, versionStamp } from './util.js';
import { alpha, baseLayout, plot, tokens } from './charts.js';

const MODEL_LABELS = { model: 'Gradient-boosted model', zero: 'Zero baseline (always predicts 0%)' };

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
      ['Short threshold', isNum(d.prediction_threshold) ? `predicted return < ${pct(d.prediction_threshold)}` : DASH],
    ];
    $('#md-facts').innerHTML = facts.map(([k, v]) => `<div class="fact"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
    const params = Object.entries(d.params ?? {});
    $('#md-params').innerHTML = params.map(([k, v]) => `<span class="param"><span>${esc(k)}</span><b>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</b></span>`).join('')
      || '<span class="muted">No parameters recorded.</span>';

    const m = d.metrics?.model;
    const z = d.metrics?.zero;
    const parts = [];
    if (isNum(m?.mae) && isNum(z?.mae)) {
      parts.push(`On the holdout, the model's mean absolute error is ${pct(m.mae, 2, false)}, against ${pct(z.mae, 2, false)} for always predicting zero${m.mae > z.mae ? '; the model does not beat that baseline on average error' : ''}.`);
    }
    if (isNum(m?.short_return) && isNum(m?.n_shorts)) {
      parts.push(`The ${num(m.n_shorts)} rows it would short returned ${pct(m.short_return)} on average as shorts.`);
    }
    $('#md-verdict').textContent = parts.join(' ');
  }

  function renderMetrics() {
    const rows = Object.entries(data.metrics ?? {}).filter(([, v]) => v);
    $('#md-metrics').innerHTML = rows.map(([k, v]) => `<tr class="${k === 'model' ? 'hl' : ''}">
      <th scope="row">${esc(MODEL_LABELS[k] ?? k)}</th>
      <td class="num">${esc(pct(v.mae, 2, false))}</td>
      <td class="num ${isNum(v.short_return) && v.short_return > 0 ? 'pos' : isNum(v.short_return) && v.short_return < 0 ? 'neg' : ''}">${esc(pct(v.short_return, 1))}</td>
      <td class="num">${esc(num(v.n_shorts))}</td></tr>`).join('')
      || '<tr><td colspan="4" class="empty">No metrics recorded.</td></tr>';
  }

  function renderCharts() {
    const k = tokens();
    const L = baseLayout();

    const folds = (data.folds ?? []).filter((f) => f && isNum(f.model_mae));
    $('#md-folds-card').hidden = !folds.length;
    if (folds.length) {
      const hm = data.metrics?.model?.mae;
      const hz = data.metrics?.zero?.mae;
      const x = [...folds.map((f) => `Fold ${f.fold}`), ...(isNum(hm) ? ['Holdout'] : [])];
      const model = [...folds.map((f) => f.model_mae), ...(isNum(hm) ? [hm] : [])];
      const zero = [...folds.map((f) => f.zero_mae ?? null), ...(isNum(hm) ? [hz ?? null] : [])];
      const sym = x.map((v) => (v === 'Holdout' ? 'diamond' : 'circle'));
      plot($('#md-folds'), [
        { x, y: zero, name: 'Zero baseline', type: 'scatter', mode: 'lines+markers', line: { color: k.base, width: 2, dash: 'dot' }, marker: { size: 9, symbol: sym, color: k.base, line: { color: k.panel, width: 2 } }, hovertemplate: '%{x}<br>Zero baseline MAE <b>%{y:.2%}</b><extra></extra>' },
        { x, y: model, name: 'Model', type: 'scatter', mode: 'lines+markers', line: { color: k.accent, width: 2 }, marker: { size: 10, symbol: sym, color: k.accent, line: { color: k.panel, width: 2 } }, hovertemplate: '%{x}<br>Model MAE <b>%{y:.2%}</b><extra></extra>' },
      ], baseLayout({
        yaxis: { ...L.yaxis, tickformat: '.1%', title: { text: 'MAE (lower is better)', font: { color: k.muted, size: 12 } }, zeroline: false },
        xaxis: { ...L.xaxis, type: 'category' },
        shapes: isNum(hm) ? [{ type: 'line', xref: 'x', yref: 'paper', x0: folds.length - 0.5, x1: folds.length - 0.5, y0: 0, y1: 1, line: { color: k.line, width: 1, dash: 'dash' } }] : [],
        hovermode: 'closest',
      }));
    }

    const th = (data.thresholds ?? []).filter((r) => r && isNum(r.threshold)).sort((a, b) => b.threshold - a.threshold);
    $('#md-thr-card').hidden = !th.length;
    if (th.length) {
      const active = data.prediction_threshold;
      const x = th.map((r) => `< ${pct(r.threshold, 0)}`);
      const isActive = th.map((r) => Math.abs(r.threshold - active) < 1e-9);
      plot($('#md-thr'), [
        { x, y: th.map((r) => r.n_shorts), type: 'bar', name: 'Shorts', text: th.map((r) => (isNum(r.n_shorts) ? r.n_shorts.toLocaleString('en-US') : '')), textposition: 'outside', cliponaxis: false, textfont: { color: k.muted, size: 12 }, marker: { color: isActive.map((a) => (a ? k.accent : alpha(k.accent, 0.4))) }, hovertemplate: 'Threshold %{x}<br><b>%{y:,}</b> shorts<extra></extra>', xaxis: 'x', yaxis: 'y' },
        { x, y: th.map((r) => r.mean_return), type: 'scatter', mode: 'lines+markers', name: 'Mean short return', line: { color: k.accent2, width: 2 }, marker: { size: 9, color: k.accent2, line: { color: k.panel, width: 2 } }, hovertemplate: 'Threshold %{x}<br>Mean short return <b>%{y:.1%}</b><extra></extra>', xaxis: 'x2', yaxis: 'y2' },
      ], baseLayout({
        grid: { rows: 2, columns: 1, pattern: 'independent' },
        xaxis: { ...L.xaxis, type: 'category', showticklabels: false, matches: 'x2' },
        xaxis2: { ...L.xaxis, type: 'category', anchor: 'y2', title: { text: 'Short when the predicted return is', font: { color: k.muted, size: 12 } } },
        yaxis: { ...L.yaxis, domain: [0.56, 1], title: { text: 'Shorts', font: { color: k.muted, size: 12 } }, tickformat: ',' },
        yaxis2: { ...L.yaxis, domain: [0, 0.44], anchor: 'x2', tickformat: '.0%', title: { text: 'Mean return', font: { color: k.muted, size: 12 } } },
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
        hovertemplate: '%{y}<br>MAE rises by <b>%{x:.2%}</b> when shuffled<extra></extra>',
      }], baseLayout({
        xaxis: { ...L.xaxis, showgrid: true, tickformat: '.1%', title: { text: 'Increase in holdout MAE when the feature is shuffled', font: { color: k.muted, size: 12 } } },
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
