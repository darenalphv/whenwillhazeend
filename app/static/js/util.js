/* util.js — fetch layer, formatting, panel state helpers.
 *
 * Mock switch: append ?mock=1 to the URL and every request is served from
 * ./__mocks__/fixtures.js instead of the network. Extra knobs:
 *   ?mock=1&mockfail=history,dispersion   -> those endpoints throw a 503
 *   ?mock=1&mockdelay=1200                -> artificial latency, to see skeletons
 * Default is OFF; with no query string the page talks to the real API.
 */

export const QS = new URLSearchParams(location.search);
export const MOCK = QS.get('mock') === '1';
const MOCK_FAIL = (QS.get('mockfail') || '').split(',').map(s => s.trim()).filter(Boolean);
const MOCK_DELAY = Number(QS.get('mockdelay') || 0);

export const REDUCED_MOTION =
  window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ------------------------------------------------------------------ errors */

export class ApiError extends Error {
  constructor(message, { status = 0, path = '', detail = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.path = path;
    this.detail = detail;
  }
}

/** Human sentence for an error, naming what actually failed. */
export function describeError(err, what) {
  const label = what ? `${what} ` : '';
  const tail = (s) => String(s).replace(/\s*[.。]\s*$/, '');
  if (err instanceof ApiError) {
    const detail = err.detail ? ` — ${tail(err.detail)}` : '';
    if (err.status === 503) return `The ${label}endpoint is unavailable (503)${detail}.`;
    if (err.status) return `The ${label}request failed with HTTP ${err.status}${detail}.`;
    return `Could not reach the ${label}API — ${tail(err.message)}.`;
  }
  return `Something went wrong loading the ${label}data — ${tail(err && err.message ? err.message : err)}.`;
}

/* ------------------------------------------------------------------- fetch */

let _fixtures = null;
async function fixtures() {
  if (!_fixtures) _fixtures = await import('./__mocks__/fixtures.js');
  return _fixtures;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Call the API. `path` is an absolute API path such as "/api/history".
 * Options: { method, body, timeout }
 */
export async function api(path, { method = 'GET', body = null, timeout = 30000 } = {}) {
  if (MOCK) {
    if (MOCK_DELAY) await sleep(MOCK_DELAY);
    const key = path.split('?')[0].replace(/^\/api\//, '').split('/')[0];
    if (MOCK_FAIL.includes(key) || MOCK_FAIL.includes(path.split('?')[0])) {
      throw new ApiError('mock failure (forced by ?mockfail)', {
        status: 503, path, detail: 'Simulated outage for UI testing.',
      });
    }
    const mod = await fixtures();
    return mod.mockResponse(path, method, body);
  }

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method,
      signal: ctrl.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    clearTimeout(timer);
    throw new ApiError(e.name === 'AbortError' ? 'the request timed out' : 'network error', {
      path,
    });
  }
  clearTimeout(timer);

  if (!res.ok) {
    let detail = null;
    try {
      const j = await res.json();
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch (_) { /* body was not JSON */ }
    throw new ApiError(`HTTP ${res.status}`, { status: res.status, path, detail });
  }
  return res.json();
}

/* -------------------------------------------------------------- formatting */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-10-15" -> "15 Oct 2026". Never uses the local timezone. */
export function fmtDate(iso, { withYear = true } = {}) {
  if (!iso || typeof iso !== 'string') return '—';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const [, y, mo, d] = m;
  return `${Number(d)} ${MONTHS[Number(mo) - 1]}${withYear ? ' ' + y : ''}`;
}

export function parseISO(iso) {
  if (!iso) return null;
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
}

/** Day of year, 1-366, from an ISO date. */
export function dayOfYear(iso) {
  const d = parseISO(iso);
  if (!d) return null;
  const start = Date.UTC(d.getUTCFullYear(), 0, 1);
  return Math.round((d.getTime() - start) / 86400000) + 1;
}

export function fmtNum(n, digits = 1) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Number(n).toLocaleString('en-GB', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

export function fmtInt(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Math.round(Number(n)).toLocaleString('en-GB');
}

/** Compact but honest: 129813 -> "129,813"; 1.75 -> "1.75". */
export function fmtSig(n, sig = 3) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1000) return fmtInt(n);
  if (abs === 0) return '0';
  const digits = Math.max(0, sig - Math.floor(Math.log10(abs)) - 1);
  return fmtNum(n, Math.min(digits, 3));
}

export function pct(n, digits = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return `${fmtNum(n * 100, digits)}%`;
}

export function titleCase(s) {
  return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export const ENSO_LABEL = {
  el_nino: 'El Niño',
  la_nina: 'La Niña',
  neutral: 'Neutral',
};

export function ensoLabel(phase) {
  return ENSO_LABEL[phase] || titleCase(phase);
}

export function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

/** Render an API unit string for display. Dimensionless ones print as nothing. */
const UNIT_LABEL = {
  degC: '\u00b0C', C: '\u00b0C', index: '', '0-1': '', fraction: '', '': '',
  ugm3: '\u00b5g/m\u00b3', 'ug/m3': '\u00b5g/m\u00b3',
};
export function unitLabel(unit) {
  const u = String(unit || '');
  return UNIT_LABEL[u] !== undefined ? UNIT_LABEL[u] : u;
}

/* -------------------------------------------------------------- DOM helpers */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/**
 * Panels declare three children: [data-state="loading"], [data-state="error"],
 * [data-state="ok"]. This flips between them and writes the error message.
 */
export function setPanelState(panel, state, message) {
  if (!panel) return;
  panel.dataset.panelState = state;
  for (const child of $$('[data-state]', panel)) {
    if (child.closest('[data-panel]') !== panel) continue;
    child.hidden = child.dataset.state !== state;
  }
  if (state === 'error') {
    const box = $('[data-state="error"] .panel-error__msg', panel);
    if (box) box.textContent = message || 'Unknown error.';
  }
}

export function panel(name, root = document) {
  return $(`[data-panel="${name}"]`, root);
}

/** Wrap an async load with automatic loading/error panel states. */
export async function withPanel(name, what, fn, root = document) {
  const p = panel(name, root);
  setPanelState(p, 'loading');
  try {
    const result = await fn();
    setPanelState(p, 'ok');
    return result;
  } catch (err) {
    console.error(`[${name}]`, err);
    setPanelState(p, 'error', describeError(err, what));
    throw err;
  }
}

/* ------------------------------------------------------------------ motion */

/** Tween a number into a node. Instant when the user prefers reduced motion. */
export function animateNumber(node, to, { from = null, duration = 700, format = fmtInt } = {}) {
  if (!node) return;
  const start = from !== null ? from : Number(node.dataset.value || 0);
  if (REDUCED_MOTION || !Number.isFinite(start) || start === to) {
    node.textContent = format(to);
    node.dataset.value = String(to);
    return;
  }
  const t0 = performance.now();
  node.dataset.value = String(to);
  function step(now) {
    const t = clamp((now - t0) / duration, 0, 1);
    const e = 1 - Math.pow(1 - t, 3);
    node.textContent = format(start + (to - start) * e);
    if (t < 1) requestAnimationFrame(step);
    else node.textContent = format(to);
  }
  requestAnimationFrame(step);
}

export function debounce(fn, ms = 400) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/* ------------------------------------------------------------------ colours */

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export const COLOR = {
  get ink() { return cssVar('--ink', '#f2ede6'); },
  get ink2() { return cssVar('--ink-2', '#c3bbb1'); },
  get ink3() { return cssVar('--ink-3', '#948c83'); },
  get line() { return cssVar('--line', '#2a2624'); },
  get ember() { return cssVar('--ember', '#ff7a45'); },
  get amber() { return cssVar('--amber', '#f5b942'); },
  get cool() { return cssVar('--cool', '#5ec8e5'); },
  enso(phase) {
    return cssVar(`--enso-${(phase || 'neutral').replace('_', '-')}`, '#9b938a');
  },
  pool(id) {
    return cssVar(`--pool-${String(id || '').replace(/_/g, '-')}`, '#c9772f');
  },
  aqi(category) {
    const key = String(category || '').toLowerCase()
      .replace(/[^a-z]+/g, '-').replace(/^-|-$/g, '');
    return cssVar(`--aqi-${key}`, '#9b938a');
  },
};

export const POOL_ORDER = [
  'peat_drained', 'peat_intact', 'primary_forest', 'secondary_forest', 'scrub_grass',
];

/* ------------------------------------------- fuel-exhaustion horizon copy */

/**
 * The horizon of an exhaustion run, as a length and as its final calendar year.
 * `never_pct` is horizon-relative: 100 means "not before the horizon ends",
 * which is a different claim from "never".
 */
export function horizonOf(ex) {
  const a = ex.assumptions || {};
  const years = (ex.trajectories && ex.trajectories.years) || [];
  const lastYear = years.length ? years[years.length - 1] : null;
  const n = a.horizon_years != null ? a.horizon_years : (years.length || null);
  return { years: n, lastYear };
}

/** The exhaustion year, or an honest statement of what the horizon could not reach. */
export function exhaustionYearText(ex) {
  const yr = (ex.exhaustion_year || {});
  const { years, lastYear } = horizonOf(ex);
  if (yr.p50 != null) return { value: yr.p50, text: String(yr.p50), reached: true };
  const span = years != null ? `${fmtInt(years)} years` : 'the modelled horizon';
  return {
    value: null,
    reached: false,
    text: lastYear ? `not within ${span}, to ${lastYear}` : `not within ${span}`,
    short: lastYear ? `not by ${lastYear}` : `not within ${span}`,
  };
}

/** never_pct read out with its horizon attached — never a bare "never". */
export function neverText(ex) {
  const yr = ex.exhaustion_year || {};
  const { years } = horizonOf(ex);
  const span = years != null ? `${fmtInt(years)} years` : 'the horizon';
  if (yr.never_pct == null) return 'the share of runs that do not reach it was not reported';
  if (yr.never_pct <= 0) return `every simulated run reaches it inside ${span}`;
  if (yr.never_pct >= 100) return `no simulated run reaches it inside ${span}`;
  return `${fmtNum(yr.never_pct, 1)}% of runs do not reach it inside ${span}`;
}

const NUMBER_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
  'eight', 'nine', 'ten', 'eleven', 'twelve'];
export function numberWord(n) {
  return NUMBER_WORDS[n] !== undefined ? NUMBER_WORDS[n] : fmtInt(n);
}
