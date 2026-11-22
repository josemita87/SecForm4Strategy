// Shared helpers: data loading, clock, formatting, polling.

export const FIXTURES = new URLSearchParams(location.search).has('fixtures');
const BASE = FIXTURES ? 'fixtures/' : 'data/';

export async function getJSON(name) {
  const res = await fetch(`${BASE}${name}.json`, { cache: 'no-cache' });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status} for ${name}.json`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// Fixture timestamps are fixed; shift the clock once so "ago" values look like a live feed.
let skew = 0;
let skewSet = false;
export function anchorClock(ts) {
  if (!FIXTURES || skewSet || !ts) return;
  const t = Date.parse(ts);
  if (Number.isFinite(t)) { skew = Date.now() - t - 4000; skewSet = true; }
}
export const now = () => Date.now() - skew;

export const t = (ts) => (ts ? Date.parse(ts) : NaN);
export const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
export const DASH = '—';
const MINUS = '−';

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function pct(x, digits = 1, sign = true) {
  if (!isNum(x)) return DASH;
  const v = (x * 100).toFixed(digits);
  if (Number(v) === 0) return `${(0).toFixed(digits)}%`;
  return `${x < 0 ? MINUS : sign ? '+' : ''}${v.replace('-', '')}%`;
}

const moneyFmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money0Fmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
export function money(x, { sign = false, whole = false } = {}) {
  if (!isNum(x)) return DASH;
  const s = (whole ? money0Fmt : moneyFmt).format(Math.abs(x));
  return `${x < 0 ? MINUS : sign && x > 0 ? '+' : ''}${s}`;
}
export function compactMoney(x) {
  if (!isNum(x)) return DASH;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 }).format(x);
}
export function num(x, digits = 0) {
  if (!isNum(x)) return DASH;
  return x.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }).replace('-', MINUS);
}

export function dur(seconds) {
  if (!isNum(seconds)) return DASH;
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 90) return `${m} m ${String(s % 60).padStart(2, '0')} s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ${String(m % 60).padStart(2, '0')} m`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

export function ago(ts) {
  const v = t(ts);
  if (!Number.isFinite(v)) return DASH;
  const s = (now() - v) / 1000;
  if (s < 2) return 'just now';
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 90 * 60) return `${Math.round(s / 60)} min ago`;
  if (s < 48 * 3600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

const ET = 'America/New_York';
const etTimeFmt = new Intl.DateTimeFormat('en-US', { timeZone: ET, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
const etShortFmt = new Intl.DateTimeFormat('en-US', { timeZone: ET, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const etDayFmt = new Intl.DateTimeFormat('en-US', { timeZone: ET, month: 'short', day: 'numeric' });
const utcFmt = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

const valid = (ts) => Number.isFinite(t(ts));
export const etTime = (ts) => (valid(ts) ? `${etTimeFmt.format(t(ts))} ET` : DASH);
export const etShort = (ts) => (valid(ts) ? etShortFmt.format(t(ts)) : DASH);
export const etStamp = (ts) => (valid(ts) ? `${etDayFmt.format(t(ts))}, ${etShortFmt.format(t(ts))} ET` : DASH);
export const utcStamp = (ts) => (valid(ts) ? `${utcFmt.format(t(ts))} UTC` : DASH);

// Model versions are training timestamps such as 20261120T210617Z.
export function versionStamp(v) {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(v ?? '');
  return m ? utcStamp(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`) : v ?? DASH;
}

const etPartsFmt = new Intl.DateTimeFormat('en-US', { timeZone: ET, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const OPEN = 9 * 60 + 30;
const CLOSE = 16 * 60;

// Regular NYSE session, 09:30-16:00 ET on weekdays; exchange holidays are not modelled.
export function marketState(at = Date.now()) {
  const parts = Object.fromEntries(etPartsFmt.formatToParts(at).map((p) => [p.type, p.value]));
  const day = DAYS.indexOf(parts.weekday);
  const mins = Number(parts.hour) * 60 + Number(parts.minute);
  const weekday = day >= 1 && day <= 5;
  if (weekday && mins >= OPEN && mins < CLOSE) return { open: true, detail: `closes in ${dur((CLOSE - mins) * 60).replace(/ 00 s$/, '')}` };
  if (weekday && mins < OPEN) return { open: false, detail: `opens in ${dur((OPEN - mins) * 60).replace(/ 00 s$/, '')}` };
  let ahead = 1;
  while (![1, 2, 3, 4, 5].includes((day + ahead) % 7)) ahead += 1;
  return { open: false, detail: `opens ${ahead === 1 ? 'tomorrow' : DAYS[(day + ahead) % 7]} 09:30 ET` };
}

export function median(xs) {
  const v = xs.filter(isNum).sort((a, b) => a - b);
  if (!v.length) return NaN;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

// Runs fn now and then every `every` ms, never overlapping; errors go to onError.
export class Poller {
  constructor(fn, every) { this.fn = fn; this.every = every; this.timer = null; this.busy = false; }
  async tick() {
    if (this.busy) return;
    this.busy = true;
    try { await this.fn(); } finally { this.busy = false; }
  }
  start() { if (this.timer) return; this.tick(); this.timer = setInterval(() => this.tick(), this.every); }
  stop() { clearInterval(this.timer); this.timer = null; }
}

// Fetches one file and reports to a status badge; returns the data, or null on failure.
export async function load(name, badge) {
  try {
    const data = await getJSON(name);
    badge?.ok();
    return data;
  } catch (err) {
    badge?.fail(err);
    return null;
  }
}

// Seconds after which a data file the publisher rewrites every minute counts as stale.
export const STALE_S = 150;
// Chip tone of an Alpaca order status; unlisted statuses are muted.
export const ORDER_TONE = { filled: 'good', partially_filled: 'good', new: 'warn', accepted: 'warn', pending_new: 'warn', held: 'warn', rejected: 'bad', canceled: 'muted', expired: 'muted' };

// A "data delayed" badge: shown while the last fetch failed or the file is older than its schedule allows.
export function badge(el) {
  let failed = null;
  let staleAge = null;
  const paint = () => {
    el.hidden = !failed && !staleAge;
    el.title = failed ? `Last fetch failed (${failed}); showing the last data received.`
      : staleAge ? `The publisher last wrote this file ${staleAge}.` : '';
  };
  return {
    ok() { failed = null; paint(); },
    fail(err) { failed = err.message; paint(); },
    // Marks the badge stale when generatedAt is older than STALE_S; returns the file's age in seconds.
    staleSince(generatedAt) {
      const age = (now() - t(generatedAt)) / 1000;
      staleAge = Number.isFinite(age) && age > STALE_S ? `${dur(age)} ago` : null;
      paint();
      return age;
    },
  };
}

export function frag(markup) {
  const tpl = document.createElement('template');
  tpl.innerHTML = markup.trim();
  return tpl.content.firstElementChild;
}

export const prefersReducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

let toastTimer = null;
export function toast(message) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4500);
}
