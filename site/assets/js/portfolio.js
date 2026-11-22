// Portfolio page: Alpaca paper account, equity history, positions and orders linked to live filings.
import { DASH, ORDER_TONE, Poller, ago, anchorClock, badge, esc, etStamp, isNum, load, money, num, pct, t } from './util.js';
import { alpha, baseLayout, plot, tokens } from './charts.js';

const tone = (x) => (!isNum(x) || x === 0 ? '' : x > 0 ? 'pos' : 'neg');

export function createPortfolio(root) {
  const $ = (sel) => root.querySelector(sel);
  const delayed = badge($('#pf-delayed'));
  let data = null;
  let liveKeys = new Set();

  function renderAccount() {
    const a = data.account ?? {};
    const hist = (data.equity_history ?? []).filter((p) => p && isNum(p.equity) && Number.isFinite(t(p.t)));
    const first = hist[0]?.equity;
    const change = isNum(a.equity) && isNum(first) ? a.equity - first : NaN;
    $('#pf-equity').textContent = money(a.equity);
    $('#pf-change').innerHTML = isNum(change)
      ? `<span class="${tone(change)}">${esc(money(change, { sign: true }))} (${esc(pct(change / first, 2))})</span> since ${esc(etStamp(hist[0].t).split(',')[0])}`
      : DASH;
    $('#pf-cash').textContent = money(a.cash);
    $('#pf-bp').textContent = money(a.buying_power);
    $('#pf-paper').hidden = a.paper === false;
    $('#pf-status').textContent = a.status ?? DASH;
    $('#pf-status').className = `chip ${a.status === 'ACTIVE' ? 'good' : 'warn'}`;
    const positions = (data.positions ?? []).filter(Boolean);
    const upl = positions.reduce((s, p) => s + (isNum(p.unrealized_pl) ? p.unrealized_pl : 0), 0);
    $('#pf-npos').textContent = positions.length;
    $('#pf-upl').innerHTML = positions.length ? `<span class="${tone(upl)}">${esc(money(upl, { sign: true }))}</span> unrealized` : 'no open positions';

    const chart = $('#pf-chart');
    $('#pf-chart-empty').hidden = hist.length > 1;
    chart.hidden = hist.length <= 1;
    if (hist.length > 1) {
      const k = tokens();
      plot(chart, [{
        x: hist.map((p) => p.t), y: hist.map((p) => p.equity), type: 'scatter', mode: 'lines', name: 'Equity',
        line: { color: k.accent, width: 2 },
        fill: 'tozeroy', fillcolor: alpha(k.accent, 0.1),
        hovertemplate: '%{x|%b %d}<br><b>%{y:$,.2f}</b><extra></extra>',
      }], baseLayout({
        yaxis: { ...baseLayout().yaxis, tickprefix: '$', tickformat: ',.0f', range: paddedRange(hist.map((p) => p.equity)), zeroline: false },
        xaxis: { ...baseLayout().xaxis, type: 'date', tickformat: '%b %d' },
        hovermode: 'x unified', showlegend: false,
      }));
    }
  }

  function renderPositions() {
    const rows = (data.positions ?? []).filter(Boolean);
    $('#pf-positions').innerHTML = rows.map((p) => `<tr>
      <th scope="row"><span class="sym">${esc(p.symbol ?? DASH)}</span></th>
      <td><span class="chip ${p.qty < 0 ? 'accent' : 'muted'}">${p.qty < 0 ? 'Short' : 'Long'}</span></td>
      <td class="num">${esc(num(isNum(p.qty) ? Math.abs(p.qty) : NaN))}</td>
      <td class="num">${esc(money(p.avg_entry_price))}</td>
      <td class="num">${esc(money(p.current_price))}</td>
      <td class="num">${esc(money(p.market_value))}</td>
      <td class="num ${tone(p.unrealized_pl)}">${esc(money(p.unrealized_pl, { sign: true }))}</td>
      <td class="num ${tone(p.unrealized_plpc)}">${esc(pct(p.unrealized_plpc, 2))}</td></tr>`).join('')
      || '<tr><td colspan="8" class="empty">No open positions. Shorts appear here once Alpaca fills them.</td></tr>';
  }

  function renderOrders() {
    const rows = (data.orders ?? []).filter(Boolean);
    $('#pf-orders').innerHTML = rows.map((o) => {
      const linked = o.client_order_id && liveKeys.has(o.client_order_id);
      const link = linked ? `<a class="filing-link" href="#/live/${encodeURIComponent(o.client_order_id)}">View filing &rarr;</a>`
        : `<span class="muted" title="${esc(o.client_order_id ?? '')}">${o.client_order_id ? 'outside live window' : DASH}</span>`;
      return `<tr>
        <td>${esc(etStamp(o.submitted_at))}</td>
        <th scope="row"><span class="sym">${esc(o.symbol ?? DASH)}</span></th>
        <td>${esc(o.side === 'sell' ? 'Sell short' : o.side ?? DASH)}</td>
        <td class="num">${esc(num(o.qty))}</td>
        <td><span class="chip ${ORDER_TONE[o.status] ?? 'muted'}">${esc(String(o.status ?? DASH).replace(/_/g, ' '))}</span></td>
        <td class="num">${esc(money(o.filled_avg_price))}</td>
        <td>${esc(o.filled_at ? etStamp(o.filled_at) : DASH)}</td>
        <td>${link}</td></tr>`;
    }).join('') || '<tr><td colspan="8" class="empty">No orders yet.</td></tr>';
  }

  async function poll() {
    const [pf, live] = await Promise.all([load('portfolio', delayed), load('live')]);
    if (live) liveKeys = new Set((live.runs ?? []).flatMap((r) => (r?.filings ?? []).map((f) => f?.key)).filter(Boolean));
    if (!pf) { if (data) renderOrders(); return; }
    anchorClock(pf.generated_at);
    data = pf;
    delayed.staleSince(pf.generated_at);
    Object.assign($('#pf-updated'), { textContent: ago(pf.generated_at) }).dataset.ago = pf.generated_at ?? '';
    renderAccount();
    renderPositions();
    renderOrders();
  }

  const poller = new Poller(poll, 30000);
  return {
    show() { poller.start(); },
    hide() { poller.stop(); },
    rerender() { if (data) renderAccount(); },
  };
}

function paddedRange(values) {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = Math.max((hi - lo) * 0.15, hi * 0.0005);
  return [lo - pad, hi + pad];
}
