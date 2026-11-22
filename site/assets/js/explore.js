// Explore page: largest insider sales and insider clusters of the last 7 days.
import { DASH, Poller, ago, badge, compactMoney, esc, etStamp, load, num } from './util.js';

export function createExplore(root) {
  const $ = (sel) => root.querySelector(sel);
  const delayed = badge($('#ex-delayed'));

  async function poll() {
    const d = await load('explore', delayed);
    if (!d) return;
    Object.assign($('#ex-updated'), { textContent: ago(d.generated_at) }).dataset.ago = d.generated_at ?? '';
    $('#ex-sales').innerHTML = (d.largest_sales_7d ?? []).filter(Boolean).map((r) => `<tr>
      <th scope="row"><span class="sym">${esc(r.ticker ?? DASH)}</span></th>
      <td>${esc(r.insider ?? DASH)}</td><td class="muted">${esc(r.role ?? DASH)}</td>
      <td class="num">${esc(num(r.shares))}</td><td class="num"><b>${esc(compactMoney(r.value))}</b></td>
      <td>${esc(etStamp(r.accepted_at))}</td></tr>`).join('')
      || '<tr><td colspan="6" class="empty">No sales in the last 7 days.</td></tr>';
    $('#ex-clusters').innerHTML = (d.clusters_7d ?? []).filter(Boolean).map((r) => `<tr>
      <th scope="row"><span class="sym">${esc(r.ticker ?? DASH)}</span></th>
      <td class="num"><b>${esc(num(r.insiders))}</b></td><td class="num">${esc(compactMoney(r.value))}</td>
      <td>${esc(etStamp(r.first_accepted_at))}</td><td>${esc(etStamp(r.last_accepted_at))}</td></tr>`).join('')
      || '<tr><td colspan="5" class="empty">No clusters in the last 7 days.</td></tr>';
  }

  const poller = new Poller(poll, 15 * 60 * 1000);
  return { show() { poller.start(); }, hide() { poller.stop(); } };
}
