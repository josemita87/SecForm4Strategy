// Live page: run feed with per-filing stage animation, replay mode, status strip and health panel.
import {
  DASH, ORDER_TONE, Poller, STALE_S, anchorClock, badge, dur, esc, etShort, etTime, frag, isNum, load, marketState, median,
  now, num, pct, prefersReducedMotion, t, toast, versionStamp, money,
} from './util.js';

const REPLAY_EVERY_MS = 3000;
const FAILED = new Set(['FAILED', 'TIMED_OUT', 'ABORTED']);
const STEP = { priced: 'yahoo-connect', labelled: 'target', scored: 'inference' };

const step = (run, name) => (run.steps ?? []).find((s) => s?.name === name) ?? null;
const stepStop = (run, name) => t(step(run, name)?.stopped_at);

function filingTimes(run, f) {
  const accepted = t(f.accepted_at);
  const scored = stepStop(run, STEP.scored);
  return { accepted, stored: t(run.started_at), priced: stepStop(run, STEP.priced), labelled: stepStop(run, STEP.labelled), scored, decided: scored };
}

const latencyS = (run, f) => (stepStop(run, STEP.scored) - t(f.accepted_at)) / 1000;

const prob = (x, digits = 1) => pct(x, digits, false);

// Probability of a short winner on a 0-100% scale; the shaded zone at and above the cutoff is shorted
function gauge(p, cutoff) {
  const pos = (x) => Math.max(0, Math.min(1, x)) * 100;
  const cut = isNum(cutoff) ? pos(cutoff) : null;
  return `<div class="gauge" role="img" aria-label="Probability ${esc(prob(p))}${isNum(cutoff) ? `, cutoff ${esc(prob(cutoff, 0))}` : ''}">
    ${cut !== null ? `<span class="gauge-zone upper" style="left:${cut}%"></span><span class="gauge-thr" style="left:${cut}%"><i>${esc(prob(cutoff, 0))}</i></span>` : ''}
    <span class="gauge-zero"></span>
    ${isNum(p) ? `<span class="gauge-dot" style="left:${pos(p)}%"></span>` : ''}
  </div><div class="gauge-scale"><span>0%</span><span>50%</span><span>100%</span></div>`;
}

function orderChip(f) {
  const o = f.order;
  if (o) {
    const status = String(o.status ?? 'unknown');
    const price = isNum(o.filled_avg_price) ? ` @ ${money(o.filled_avg_price)}` : '';
    const label = status === 'filled' ? `Filled${price}` : ['new', 'accepted', 'pending_new'].includes(status) ? 'Queued for the open' : status.replace(/_/g, ' ');
    return `<span class="chip ${ORDER_TONE[status] ?? 'muted'}" title="Alpaca order ${esc(o.id ?? '')}: ${esc(status)}"><b>Alpaca</b> ${esc(label)}</span>`;
  }
  return f.shorted ? '<span class="chip warn"><b>Alpaca</b> order not reported yet</span>' : '<span class="chip muted">No order</span>';
}

function verdictText(f, cutoff) {
  if (f.shorted) return 'Shorted';
  if (isNum(f.probability) && isNum(cutoff) && f.probability < cutoff) return 'Skipped';
  return 'Not traded';
}

function filingCard(run, f, cutoff) {
  const times = filingTimes(run, f);
  const lat = latencyS(run, f);
  const verdict = verdictText(f, cutoff);
  const reason = f.shorted ? `probability ${prob(f.probability)} reaches the ${prob(cutoff, 0)} cutoff`
    : verdict === 'Skipped' ? `probability ${prob(f.probability)} is below the ${prob(cutoff, 0)} cutoff` : isNum(f.probability) ? 'no short order was placed' : 'no probability recorded';
  const stages = [
    ['accepted', 'SEC accepted'], ['stored', 'Parsed & stored'], ['priced', 'Priced'], ['labelled', 'Features built'], ['scored', 'Scored'], ['decided', verdict],
  ];
  const offset = (k) => (k === 'accepted' ? etShort(f.accepted_at) : Number.isFinite(times[k]) && Number.isFinite(times.accepted) ? `+${dur((times[k] - times.accepted) / 1000)}` : DASH);
  const total = Math.max(1, ...stages.map(([k]) => times[k] - times.accepted).filter(Number.isFinite));
  const delays = stages.map(([k], i) => {
    const off = times[k] - times.accepted;
    return Math.round(250 + (Number.isFinite(off) ? Math.max(0, off) / total : i / 5) * 2100);
  });
  for (let i = 1; i < delays.length; i += 1) delays[i] = Math.max(delays[i], delays[i - 1] + 180);
  return `<article class="card filing ${f.shorted ? 'is-short' : 'is-skip'}" data-key="${esc(f.key)}" data-delays="${delays.join(',')}" tabindex="-1">
    <div class="filing-head">
      <div class="ticker">${esc(f.ticker ?? DASH)}</div>
      <div class="filing-meta"><span>Form 4 sale</span><span>accepted ${esc(etTime(f.accepted_at))}</span></div>
      <div class="verdict ${f.shorted ? 'short' : 'skip'}">${f.shorted ? '<span class="verdict-arrow" aria-hidden="true">&#8600;</span>' : ''}${esc(verdict.toUpperCase())}</div>
    </div>
    <div class="filing-body">
      <div class="pred">
        <div class="label">Probability of a fall of more than 5% in 30 days</div>
        <div class="pred-value">${esc(prob(f.probability))}</div>
        ${gauge(f.probability, cutoff)}
        <div class="reason">${esc(reason)}</div>
      </div>
      <div class="latency">
        <div class="latency-value">${Number.isFinite(lat) ? esc(dur(lat)) : DASH}</div>
        <div class="label">SEC acceptance &rarr; decision</div>
      </div>
    </div>
    <ol class="stages">${stages.map(([k, label]) => `<li class="stage s-${k}"><span class="dot"></span><span class="stage-name">${esc(label)}</span><span class="stage-time">${esc(offset(k))}</span></li>`).join('')}</ol>
    <div class="filing-foot">${orderChip(f)}<span class="run-id" title="Step Functions execution">run ${esc(run.id ?? DASH)}</span></div>
  </article>`;
}

function runningCard(run) {
  const phases = [['yahoo-connect', 'Pricing'], ['target', 'Building features'], ['inference', 'Scoring']];
  const li = phases.map(([name, label]) => {
    const s = step(run, name);
    const state = s?.stopped_at ? 'done' : s?.started_at ? 'active' : 'pending';
    return `<li class="phase ${state}"><span class="dot"></span>${esc(label)}</li>`;
  }).join('');
  return `<article class="card running">
    <div class="running-head"><span class="spinner" aria-hidden="true"></span>
      <div><b>${esc((run.tickers ?? []).join(', ') || 'New filings')}</b><span class="muted"> in flight &middot; started ${esc(etTime(run.started_at))}</span></div>
      <span class="elapsed" data-since="${esc(run.started_at ?? '')}">${esc(dur((now() - t(run.started_at)) / 1000))}</span></div>
    <ol class="phases">${li}</ol>
  </article>`;
}

function lineItem(run, tone, text) {
  const tickers = (run.tickers ?? []).join(', ') || DASH;
  const took = (t(run.stopped_at) - t(run.started_at)) / 1000;
  return `<div class="run-line ${tone}">
    <span class="line-icon" aria-hidden="true">${tone === 'fail' ? '&#10005;' : '&#10003;'}</span>
    <span class="line-time">${esc(etShort(run.started_at))}</span>
    <b>${esc(tickers)}</b><span class="line-text">${text}</span>
    ${Number.isFinite(took) ? `<span class="line-dur">${esc(dur(took))}</span>` : ''}
  </div>`;
}

// Why a finished run scored nothing, from the publisher's skip field; older live.json has none
const SKIP = {
  no_sale: ['Not a sale', 'only insider sales are scored'],
  stale: ['Older than 60 min', ''],
};

function skippedCard(run) {
  const [title, sub] = SKIP[run.skip] ?? ['Nothing to score', ''];
  const tickers = (run.tickers ?? []).join(', ');
  const took = (t(run.stopped_at) - t(run.started_at)) / 1000;
  return `<article class="card skipped">
    ${tickers ? `<div class="skipped-ticker">${esc(tickers)}</div>` : ''}
    <div class="skipped-text"><b>${esc(title)} &middot; skipped</b>${sub ? `<span>${esc(sub)}</span>` : ''}</div>
    <div class="skipped-meta"><span>${esc(etTime(run.started_at))}${Number.isFinite(took) ? ` &middot; ${esc(dur(took))}` : ''}</span><span class="pill skipped-pill">Skipped</span></div>
  </article>`;
}

function itemsFor(run, fallbackCutoff) {
  const cutoff = isNum(run.probability_cutoff) ? run.probability_cutoff : fallbackCutoff;
  const status = run.status ?? 'UNKNOWN';
  const filings = [...(run.filings ?? [])].filter(Boolean).sort((a, b) => (t(b.accepted_at) || 0) - (t(a.accepted_at) || 0));
  const items = [];
  if (status === 'RUNNING') items.push({ id: `${run.id}|run`, sig: JSON.stringify([run.steps, run.tickers]), markup: () => runningCard(run) });
  if (FAILED.has(status)) {
    const at = (run.steps ?? []).find((s) => FAILED.has(s?.status))?.name;
    const text = `<span class="fail-tag">${esc(status.replace('_', ' '))}${at ? ` at ${esc(at)}` : ''}</span> ${esc(run.error ?? 'no error message')}`;
    items.push({ id: `${run.id}|fail`, sig: JSON.stringify([status, run.error, run.steps]), markup: () => lineItem(run, 'fail', text) });
  }
  for (const f of filings) {
    items.push({ id: `${run.id}|${f.key}`, sig: JSON.stringify([f, cutoff, run.steps, run.started_at]), markup: () => filingCard(run, f, cutoff), card: true });
  }
  if (!filings.length && status === 'SUCCEEDED') {
    items.push({ id: `${run.id}|empty`, sig: JSON.stringify([status, run.stopped_at, run.skip, run.tickers]), markup: () => skippedCard(run) });
  } else if (!filings.length && !FAILED.has(status) && status !== 'RUNNING') {
    items.push({ id: `${run.id}|empty`, sig: JSON.stringify([status, run.stopped_at]), markup: () => lineItem(run, 'quiet', `run ${esc(status.toLowerCase())}`) });
  }
  return items;
}

export function createLive(root) {
  const $ = (sel) => root.querySelector(sel);
  const feed = $('#feed');
  const nodes = new Map();
  const liveBadge = badge($('#live-delayed'));
  const healthBadge = badge($('#health-delayed'));
  let live = null;
  let health = null;
  let rendered = false;
  let replay = null;
  let focusKey = null;
  let clock = null;

  function settle(node) {
    node.querySelectorAll('.stage').forEach((s) => s.classList.add('lit'));
    node.classList.add('decided');
  }

  function animate(node) {
    node.classList.add('enter');
    node.addEventListener('animationend', () => node.classList.remove('enter'), { once: true });
    if (!node.classList.contains('filing')) return;
    if (prefersReducedMotion()) { settle(node); return; }
    const delays = (node.dataset.delays ?? '').split(',').map(Number);
    node.querySelectorAll('.stage').forEach((s, i) => setTimeout(() => s.classList.add('lit'), delays[i] ?? i * 300));
    setTimeout(() => node.classList.add('decided'), (delays.at(-1) ?? 1500) + 120);
  }

  function render(items, animateCount) {
    const keep = new Set();
    let animated = 0;
    items.forEach((it, i) => {
      let node = nodes.get(it.id);
      const isNew = !node;
      if (node && node.dataset.sig !== it.sig) {
        const next = frag(it.markup());
        node.replaceWith(next);
        node = next;
        settle(node);
      }
      if (!node) node = frag(it.markup());
      node.dataset.sig = it.sig;
      nodes.set(it.id, node);
      keep.add(it.id);
      const ref = feed.children[i] ?? null;
      if (ref !== node) feed.insertBefore(node, ref);
      if (isNew) {
        if (animated < animateCount) { animated += it.card ? 1 : 0; animate(node); } else settle(node);
      }
    });
    for (const [id, node] of nodes) if (!keep.has(id)) { node.remove(); nodes.delete(id); }
    $('#feed-empty').hidden = items.length > 0;
  }

  const itemsForRuns = (runs) => runs.flatMap((r) => (r ? itemsFor(r, live?.probability_cutoff) : []));

  function renderLive() {
    if (!live || replay) return;
    render(itemsForRuns(live.runs ?? []), rendered ? Infinity : 2);
    rendered = true;
    if (focusKey) focus();
  }

  function focus() {
    const node = [...feed.querySelectorAll('.filing')].find((n) => n.dataset.key === focusKey);
    if (!node) { toast(`Filing ${focusKey} is not in the latest 50 runs.`); focusKey = null; return; }
    focusKey = null;
    node.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'center' });
    node.classList.remove('flash');
    void node.offsetWidth;
    node.classList.add('flash');
    node.focus({ preventScroll: true });
  }

  function renderSummary() {
    const runs = (live?.runs ?? []).filter(Boolean);
    const pairs = runs.flatMap((r) => (r.filings ?? []).filter(Boolean).map((f) => [r, f]));
    const shorts = pairs.filter(([, f]) => f.shorted).length;
    const lat = median(pairs.map(([r, f]) => latencyS(r, f)));
    const failed = runs.filter((r) => FAILED.has(r.status)).length;
    $('#sum-filings').textContent = pairs.length;
    $('#sum-shorts').textContent = shorts;
    $('#sum-latency').textContent = Number.isFinite(lat) ? dur(lat) : DASH;
    $('#sum-runs').textContent = `${runs.length - failed} / ${runs.length}`;
  }

  function renderStrip() {
    const enabled = live?.poller_enabled ?? health?.switches?.live_poller;
    const poller = $('#st-poller');
    poller.dataset.state = enabled === true ? 'on' : enabled === false ? 'off' : 'unknown';
    $('#st-poller-text').textContent = enabled === true ? 'Polling SEC every minute' : enabled === false ? 'Poller switched off' : 'Poller state unknown';
    $('#st-threshold').textContent = isNum(live?.probability_cutoff) ? `p \u2265 ${prob(live.probability_cutoff, 0)}` : DASH;
    const model = health?.model;
    $('#st-model').textContent = model?.version ?? DASH;
    $('#st-model').title = model ? `Trained ${versionStamp(model.version)}, git ${model.git_sha ?? DASH}` : '';
    tickStrip();
  }

  function tickStrip() {
    const m = marketState();
    const market = $('#st-market');
    market.dataset.state = m.open ? 'on' : 'off';
    $('#st-market-text').textContent = m.open ? 'NYSE open' : 'NYSE closed';
    $('#st-market-detail').textContent = m.detail;
    const age = liveBadge.staleSince(live?.generated_at);
    const fresh = $('#st-fresh');
    fresh.textContent = Number.isFinite(age) ? (age < 2 ? 'just now' : `${dur(age)} ago`) : DASH;
    fresh.dataset.tone = !Number.isFinite(age) ? '' : age > STALE_S ? 'bad' : age > 75 ? 'warn' : 'good';
    healthBadge.staleSince(health?.generated_at);
  }

  function renderHealth() {
    const h = health;
    if (!h) return;
    const sw = h.switches ?? {};
    $('#h-switches').innerHTML = [['live_poller', 'Live poller'], ['weekly_retrain', 'Weekly retrain']]
      .map(([k, label]) => `<span class="chip ${sw[k] === true ? 'good' : 'muted'}">${esc(label)} ${sw[k] === true ? 'on' : sw[k] === false ? 'off' : DASH}</span>`).join('');
    const r = h.live_runs_24h ?? {};
    $('#h-ok').textContent = num(r.succeeded);
    $('#h-failed').textContent = num(r.failed);
    $('#h-failed').closest('.mini').dataset.tone = r.failed > 0 ? 'bad' : '';
    $('#h-running').textContent = num(r.running);
    const lambdas = (h.lambdas ?? []).filter(Boolean);
    const errs = lambdas.reduce((a, l) => a + (isNum(l.errors_1h) ? l.errors_1h : 0), 0);
    $('#h-errors').textContent = lambdas.length ? errs : DASH;
    $('#h-errors').closest('.mini').dataset.tone = errs > 0 ? 'bad' : '';
    $('#h-queues').innerHTML = (h.queues ?? []).filter(Boolean).map((q) => `<tr>
      <th scope="row">${esc(q.name)}</th><td>${num(q.depth)}</td><td>${num(q.in_flight)}</td>
      <td><span class="count ${q.dlq_depth > 0 ? 'bad' : 'ok'}">${num(q.dlq_depth)}</span></td></tr>`).join('')
      || `<tr><td colspan="4" class="muted">No queue data</td></tr>`;
    $('#h-lambdas').innerHTML = lambdas.map((l) => `<tr>
      <th scope="row">${esc(l.name)}</th><td>${num(l.invocations_1h)}</td>
      <td><span class="count ${l.errors_1h > 0 ? 'bad' : 'ok'}">${num(l.errors_1h)}</span></td>
      <td>${isNum(l.p90_duration_ms) ? esc(`${(l.p90_duration_ms / 1000).toFixed(1)} s`) : DASH}</td></tr>`).join('')
      || `<tr><td colspan="4" class="muted">No Lambda data</td></tr>`;
    const rt = h.retrain;
    $('#h-retrain').innerHTML = rt ? `
      <div class="retrain-row"><b>${esc(rt.name ?? DASH)}</b><span class="chip ${rt.status === 'RUNNING' ? 'accent' : rt.status === 'SUCCEEDED' ? 'good' : FAILED.has(rt.status) ? 'bad' : 'muted'}">${esc(rt.status ?? DASH)}</span></div>
      <div class="retrain-row muted">${rt.status === 'RUNNING' ? `step <b class="ink">${esc(rt.step ?? DASH)}</b> &middot; running <span data-since="${esc(rt.started_at ?? '')}">${esc(dur((now() - t(rt.started_at)) / 1000))}</span>`
        : `last step ${esc(rt.step ?? DASH)} &middot; took ${esc(dur((t(rt.stopped_at) - t(rt.started_at)) / 1000))} &middot; ended ${esc(etTime(rt.stopped_at))}`}</div>`
      : '<div class="muted">No retrain run yet.</div>';
  }

  async function pollLive() {
    const data = await load('live', liveBadge);
    if (!data) return;
    anchorClock(data.generated_at);
    live = data;
    renderStrip();
    renderSummary();
    renderLive();
  }

  async function pollHealth() {
    const data = await load('health', healthBadge);
    if (!data) return;
    anchorClock(data.generated_at);
    health = data;
    renderHealth();
    renderStrip();
  }

  const livePoller = new Poller(pollLive, 5000);
  const healthPoller = new Poller(pollHealth, 15000);

  // Replay: the runs in live.json, oldest first, one every REPLAY_EVERY_MS, through the same feed.
  const banner = $('#replay-banner');
  const toggle = $('#replay-toggle');

  function replayStep() {
    if (!replay) return;
    if (replay.i >= replay.runs.length) {
      clearInterval(replay.timer);
      banner.classList.add('done');
      $('#replay-text').textContent = `Replay finished: ${replay.runs.length} runs.`;
      return;
    }
    replay.i += 1;
    const run = replay.runs[replay.i - 1];
    render(itemsForRuns(replay.runs.slice(0, replay.i).reverse()), Infinity);
    const y = feed.getBoundingClientRect().top + window.scrollY - banner.getBoundingClientRect().bottom - 16;
    if (window.scrollY > y) window.scrollTo({ top: Math.max(0, y), behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
    $('#replay-text').textContent = `Run ${replay.i} of ${replay.runs.length}, originally ${etTime(run.started_at)}`;
    $('#replay-bar').style.width = `${(replay.i / replay.runs.length) * 100}%`;
  }

  function startReplay() {
    const runs = (live?.runs ?? []).filter(Boolean);
    if (!runs.length) { toast('There are no runs to replay yet.'); return; }
    stopReplay(false);
    replay = { runs: [...runs].sort((a, b) => (t(a.started_at) || 0) - (t(b.started_at) || 0)), i: 0, timer: null };
    feed.replaceChildren();
    nodes.clear();
    document.body.classList.add('replaying');
    banner.hidden = false;
    banner.classList.remove('done');
    toggle.setAttribute('aria-pressed', 'true');
    toggle.querySelector('span').textContent = 'Exit replay';
    $('#replay-bar').style.width = '0';
    replayStep();
    replay.timer = setInterval(replayStep, REPLAY_EVERY_MS);
  }

  function stopReplay(restore = true) {
    if (!replay) return;
    clearInterval(replay.timer);
    replay = null;
    document.body.classList.remove('replaying');
    banner.hidden = true;
    toggle.setAttribute('aria-pressed', 'false');
    toggle.querySelector('span').textContent = 'Replay';
    if (restore) {
      feed.replaceChildren();
      nodes.clear();
      if (live) render(itemsForRuns(live.runs ?? []), 0);
    }
  }

  toggle.addEventListener('click', () => (replay ? stopReplay() : startReplay()));
  $('#replay-again').addEventListener('click', startReplay);
  $('#replay-exit').addEventListener('click', () => stopReplay());

  return {
    show(arg) {
      if (arg) {
        focusKey = arg;
        stopReplay();
        if (rendered) focus();
      }
      livePoller.start();
      healthPoller.start();
      clearInterval(clock);
      clock = setInterval(tickStrip, 1000);
    },
    hide() {
      livePoller.stop();
      healthPoller.stop();
      clearInterval(clock);
      stopReplay();
    },
  };
}
