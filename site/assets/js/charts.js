// Plotly wrapper: theme from CSS tokens, one config, graceful fallback when the CDN is unreachable.

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function tokens() {
  return {
    ink: css('--ink'), muted: css('--muted'), faint: css('--faint'), line: css('--line'), edge: css('--edge'), panel: css('--panel'),
    accent: css('--accent'), accent2: css('--g2'), good: css('--good'), bad: css('--bad'), base: css('--baseline'),
  };
}

export function baseLayout(extra = {}) {
  const k = tokens();
  const axis = { gridcolor: k.line, zerolinecolor: k.edge, linecolor: k.line, tickcolor: k.line, tickfont: { color: k.muted, size: 12 }, title: { font: { color: k.muted, size: 12 } }, automargin: true };
  return {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', color: k.ink, size: 13 },
    margin: { l: 56, r: 16, t: 12, b: 44 },
    hoverlabel: { bgcolor: k.panel, bordercolor: k.line, font: { color: k.ink, family: 'Inter, sans-serif', size: 13 } },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { color: k.muted, size: 12 } },
    xaxis: { ...axis, showgrid: false }, yaxis: { ...axis, zeroline: true },
    ...extra,
  };
}

export function plot(el, traces, layout) {
  if (!window.Plotly) {
    el.innerHTML = '<p class="chart-fallback">The chart library did not load. The tables on this page hold the same numbers.</p>';
    return;
  }
  window.Plotly.react(el, traces, layout, { displayModeBar: false, responsive: true });
}

export function resizeAll(root) {
  if (!window.Plotly) return;
  root.querySelectorAll('.js-plotly-plot').forEach((el) => window.Plotly.Plots.resize(el));
}

export function alpha(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
