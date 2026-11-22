// Composition root: theme, hash router, page lifecycle and the one-second clock for relative times.
import { FIXTURES, anchorClock, ago, dur, getJSON, now, t } from './util.js';
import { createLive } from './live.js';
import { createPortfolio } from './portfolio.js';
import { createModel } from './model.js';
import { createExplore } from './explore.js';
import { resizeAll } from './charts.js';

const THEME_KEY = 'sf4-theme';
const DEFAULT_ROUTE = 'live';

const pages = {
  story: { el: document.getElementById('page-story'), show() {
      const f = document.getElementById('story-frame');
      if (!f.getAttribute('src')) {
        f.addEventListener('load', () => { applyTheme(document.documentElement.dataset.theme); f.focus(); });
        f.src = 'story/index.html';
      } else f.focus();
    },
    hide() {},
  },
  live: { el: document.getElementById('page-live'), ...createLive(document.getElementById('page-live')) },
  portfolio: { el: document.getElementById('page-portfolio'), ...createPortfolio(document.getElementById('page-portfolio')) },
  model: { el: document.getElementById('page-model'), ...createModel(document.getElementById('page-model')) },
  explore: { el: document.getElementById('page-explore'), ...createExplore(document.getElementById('page-explore')) },
};
let current = null;
let exploreAvailable = true;

function parseHash() {
  const [, name = '', ...rest] = location.hash.split('/');
  return { name, arg: rest.length ? decodeURIComponent(rest.join('/')) : null };
}

function route() {
  let { name, arg } = parseHash();
  if (!pages[name] || (name === 'explore' && !exploreAvailable)) {
    history.replaceState(null, '', `${location.pathname}${location.search}#/${DEFAULT_ROUTE}`);
    ({ name, arg } = { name: DEFAULT_ROUTE, arg: null });
  }
  if (current !== name) {
    if (current) { pages[current].hide(); pages[current].el.hidden = true; }
    current = name;
    pages[name].el.hidden = false;
    document.body.dataset.page = name;
    document.querySelectorAll('.nav-link').forEach((a) => {
      if (a.dataset.route === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    window.scrollTo(0, 0);
  }
  pages[name].show(arg);
  requestAnimationFrame(() => resizeAll(pages[name].el));
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById('theme-toggle');
  btn.setAttribute('aria-label', theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
  btn.dataset.theme = theme;
  try { document.getElementById('story-frame').contentDocument?.documentElement.setAttribute('data-theme', theme); } catch { /* deck not loaded */ }
}

document.getElementById('theme-toggle').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(THEME_KEY, next); } catch { /* storage unavailable */ }
  applyTheme(next);
  Object.values(pages).forEach((p) => p.rerender?.());
});

function tick() {
  document.querySelectorAll('[data-ago]').forEach((el) => { el.textContent = el.dataset.ago ? ago(el.dataset.ago) : ''; });
  document.querySelectorAll('[data-since]').forEach((el) => {
    const s = (now() - t(el.dataset.since)) / 1000;
    el.textContent = Number.isFinite(s) ? dur(s) : '';
  });
}

async function checkExplore() {
  try {
    await getJSON('explore');
  } catch (err) {
    if (err.status === 404) {
      exploreAvailable = false;
      document.querySelector('.nav-link[data-route="explore"]').hidden = true;
      if (current === 'explore') route();
    }
  }
}

async function start() {
  applyTheme(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
  document.getElementById('fixtures-pill').hidden = !FIXTURES;
  if (FIXTURES) {
    try { anchorClock((await getJSON('live')).generated_at); } catch { /* the page shows its own delayed badge */ }
  }
  window.addEventListener('hashchange', route);
  route();
  checkExplore();
  setInterval(tick, 1000);
  tick();
}

start();
