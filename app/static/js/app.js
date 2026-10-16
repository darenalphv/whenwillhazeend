/* app.js — bootstrap, hero, the other ending, the toll, odds, drivers, methods.
 *
 * Every figure on the page comes from the API. Nothing numeric is hardcoded
 * here except the WHO guideline the toll line quotes, which is a citation.
 * Panels flip between [data-state="loading" | "error" | "ok"]; if an endpoint
 * fails the panel says which one and why.
 */

import {
  api, MOCK, QS, REDUCED_MOTION, describeError,
  $, $$, el, panel, setPanelState, withPanel,
  fmtDate, fmtInt, fmtNum, fmtSig, pct, parseISO, animateNumber,
  titleCase, clamp, COLOR, unitLabel,
  horizonOf, exhaustionYearText, neverText, numberWord,
  paintRange, paintAllRanges, rangeOf, foldOf, doiUrl,
} from './util.js';
import { initMap, setMapYear, applyMeteo } from './map.js';
import { initHistoryCharts, renderOddsHistory, renderNowChart, renderFiresByDay, fmtLocalTime } from './charts.js';
import { initSim } from './sim.js';
import { HazeVeil, hazeReading } from './fire.js';

/* ------------------------------------------------------------------ state */

export const store = {
  health: null,
  season: null,       // baseline season simulation
  exhaustion: null,   // baseline exhaustion simulation
  history: null,
  fuel: null,
  regions: null,
  receptors: null,
  meteo: null,
  odds: null,
};

const caveatRegistry = new Map();   // label -> string[]
let provPayload = null;             // /api/provenance response

const TODAY = new Date().toISOString().slice(0, 10);
const SEASON_SIMS = 5000;

// The API's own default. Fuel exhaustion sits beyond two centuries, so a
// shorter horizon truncates below the answer and p50 comes back null.
const DEFAULT_HORIZON_YEARS = 300;

// WHO 2021 Air Quality Guideline, 24-hour mean PM2.5. A citation, not a model output.
const WHO_PM25_24H = 15;

/* ------------------------------------------------------------ status chip */

function setStatus(kind, text) {
  const dot = $('#api-status .status__dot');
  const label = $('#api-status .status__text');
  if (dot) dot.dataset.status = kind;
  if (label) label.textContent = text;
  const box = $('#api-status');
  if (box) box.title = text;
}

/* ------------------------------------------------------------- the plate */

let veil = null;
let countdownTimer = null;

/** Paint the veil immediately, before any data: it is the instant layer. */
function initVeil() {
  const plate = $('#plate');
  if (!plate) return;
  try {
    veil = new HazeVeil(plate, { panel: $('.plate__panel', plate), land: $('.plate__land', plate) });
  } catch (err) {
    // A missing canvas context must never cost anyone the answer.
    console.warn('haze veil unavailable:', err);
    veil = null;
  }
}

function renderHero(season) {
  const days = season.days_remaining || {};
  const ends = season.end_date || {};
  const unit = $('#hero-unit');

  if (season.active === false) {
    $('#hero-days').textContent = '0';
    unit.textContent = 'the season is not running';
    unit.classList.add('answer__unit--text');
    $('#hero-interval').textContent = '—';
    $('#hero-enddate').textContent = '—';
    $('#hero-reason').textContent =
      'No burn season is currently active in Kalimantan. It will be back; it always is.';
  } else {
    unit.textContent = days.p50 === 1 ? 'more day' : 'more days';
    unit.classList.remove('answer__unit--text');
    animateNumber($('#hero-days'), days.p50, { from: 0, format: fmtInt, duration: 900 });
    $('#hero-interval').textContent =
      days.p10 != null && days.p90 != null ? `${fmtInt(days.p10)}–${fmtInt(days.p90)} days` : '—';
    $('#hero-enddate').textContent = fmtDate(ends.p50);
    $('#hero-reason').textContent = reasonLine(season);
  }
  $('#hero-confidence').textContent = season.confidence || 'unstated';
  $('#hero-asof').textContent = fmtDate(season.as_of || TODAY);

  renderFlags(season);
  renderVeilCaption(veil ? veil.setReading(season) : hazeReading(season), season);
  renderSurvival(season);
  startCountdown(season.active === false ? null : ends.p50);
}

/** One plain-language line, built from the model's own top driver. */
function reasonLine(season) {
  const drivers = (season.drivers || []).slice()
    .sort((a, b) => (b.contribution || 0) - (a.contribution || 0));
  const top = drivers[0];
  const days = season.days_remaining || {};
  const wide = days.p10 != null && days.p90 != null && days.p90 - days.p10 > 40;
  const parts = [];
  if (top && top.explain) parts.push(top.explain);
  else parts.push('The model has not reported a dominant driver for this run.');
  if (wide) parts.push('The interval is wide because the rains could arrive over a month apart.');
  return parts.join(' ');
}

let sceneLive = false;

function renderVeilCaption(r, season) {
  const box = $('#veil-caption');
  if (!box) return;
  box.textContent = '';
  const lead = sceneLive
    ? 'Behind the answer: a 3D model run of the island’s smoke; its own label says which day it shows. The hazy sky that loaded first was drawn from this season’s reading — '
    : 'The sky is drawn from the season model’s reading, not photographed — ';
  box.append(document.createTextNode(lead));
  const bits = [];
  if (r.readiness != null) bits.push(['drought readiness ', `${fmtNum(r.readiness, 2)} on a 0–1 scale`]);
  else if (r.kbdi != null) bits.push(['Keetch–Byram Drought Index ', `${fmtInt(r.kbdi)} of 800`]);
  if (r.pStill != null) {
    bits.push([`chance still burning in ${numberWord(r.horizonDays)} days `, pct(r.pStill)]);
  }
  if (!bits.length) {
    box.append(document.createTextNode('this run reported neither a drought index nor a daily curve, so it is drawn at a neutral mid haze.'));
    return;
  }
  bits.forEach(([k, v], i) => {
    if (i) box.append(document.createTextNode(', '));
    box.append(document.createTextNode(k), el('b', { text: v }));
  });
  box.append(document.createTextNode(
    season && season.active === false
      ? '. No season is active, so it is drawn clear.'
      : '. The thicker the haze, the longer the model expects it to last.'));
}

function renderFlags(season) {
  const box = $('#hero-flags');
  if (!box) return;
  box.textContent = '';
  const flags = [];
  if (MOCK) flags.push(['tag--warn', 'mock fixture data']);
  flags.push(season.live_weather
    ? ['tag--ok', 'live weather feed']
    : ['tag--warn', 'no live weather: climatology fallback']);
  if (season.scenario) flags.push(['', `scenario: ${titleCase(season.scenario)}`]);
  if (season.region) flags.push(['', `region: ${titleCase(season.region)}`]);
  for (const [cls, text] of flags) box.append(el('span', { class: `tag ${cls}`.trim(), text }));
}

/* Survival curve: drawn at its real pixel width so the labels stay 12px. */
let survivalSeason = null;
let survivalRO = null;

function renderSurvival(season) {
  survivalSeason = season;
  const holder = $('#survival-spark');
  if (!holder) return;
  if (!survivalRO && 'ResizeObserver' in window) {
    let lastW = 0;
    survivalRO = new ResizeObserver(() => {
      const w = Math.round(holder.clientWidth);
      if (w && Math.abs(w - lastW) > 4) { lastW = w; drawSurvival(); }
    });
    survivalRO.observe(holder);
  }
  drawSurvival();
}

function drawSurvival() {
  const holder = $('#survival-spark');
  const season = survivalSeason;
  if (!holder || !season) return;
  const daily = season.daily_end_probability || [];
  holder.textContent = '';
  if (!daily.length) {
    holder.append(el('p', { class: 'control__sub', text: 'The model returned no daily curve for this run.' }));
    return;
  }
  const W = Math.max(260, Math.round(holder.clientWidth || 600));
  const H = W < 480 ? 170 : 200;
  const L = 40, R = 12, T = 22, B = 26;
  const iw = W - L - R, ih = H - T - B;
  const n = daily.length;
  const x = (i) => L + (i / Math.max(1, n - 1)) * iw;
  const y = (p) => T + (1 - clamp(Number(p), 0, 1)) * ih;
  const dates = daily.map((d) => d.date);
  const idxOf = (iso) => {
    if (!iso) return -1;
    let k = dates.indexOf(iso);
    if (k < 0) k = dates.findIndex((d) => d > iso);
    return k;
  };

  const ns = 'http://www.w3.org/2000/svg';
  const S = (tag, attrs = {}, text) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    if (text != null) node.textContent = text;
    return node;
  };
  const svg = S('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img' });
  const last = daily[n - 1];
  svg.setAttribute('aria-label',
    `Probability the season is still burning: from ${pct(Number(daily[0].p_still_burning))} tomorrow to ${pct(Number(last.p_still_burning))} by ${fmtDate(last.date)}.`);

  // 80% interval band
  const ends = season.end_date || {};
  const i10 = idxOf(ends.p10), i90 = idxOf(ends.p90);
  if (i10 >= 0) {
    const xa = x(i10), xb = i90 >= 0 ? x(i90) : L + iw;
    svg.append(S('rect', { x: xa, y: T, width: Math.max(1, xb - xa), height: ih, fill: COLOR.hazeTint }));
    svg.append(S('text', { x: xa + 4, y: T - 7 }, '80% of runs end in here'));
  }
  // gridlines
  for (const p of [0, 0.5, 1]) {
    svg.append(S('line', { x1: L, x2: L + iw, y1: y(p), y2: y(p), stroke: COLOR.line, 'stroke-width': 1 }));
    svg.append(S('text', { x: L - 6, y: y(p) + 4, 'text-anchor': 'end' }, `${p * 100}%`));
  }
  // curve
  const pts = daily.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d.p_still_burning).toFixed(1)}`).join('');
  svg.append(S('path', {
    d: `${pts}L${x(n - 1).toFixed(1)},${(T + ih).toFixed(1)}L${L},${(T + ih).toFixed(1)}Z`,
    fill: COLOR.haze, 'fill-opacity': 0.14,
  }));
  svg.append(S('path', { d: pts, fill: 'none', stroke: COLOR.ink, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
  // median marker
  const i50 = idxOf(ends.p50);
  if (i50 >= 0) {
    const xm = x(i50);
    svg.append(S('line', { x1: xm, x2: xm, y1: T, y2: T + ih, stroke: COLOR.ember2, 'stroke-width': 1.5, 'stroke-dasharray': '3 3' }));
    svg.append(S('circle', { cx: xm, cy: y(daily[i50].p_still_burning), r: 4, fill: COLOR.ember2 }));
    const anchor = xm > L + iw * 0.7 ? 'end' : 'start';
    svg.append(S('text', { x: xm + (anchor === 'end' ? -6 : 6), y: T + 12, 'text-anchor': anchor, class: 's-strong' },
      `median ${fmtDate(ends.p50, { withYear: false })}`));
  }
  // x labels: first and last date
  svg.append(S('text', { x: L, y: H - 6 }, fmtDate(daily[0].date, { withYear: false })));
  svg.append(S('text', { x: L + iw, y: H - 6, 'text-anchor': 'end' }, fmtDate(last.date, { withYear: false })));
  holder.append(svg);
}

function startCountdown(endIso) {
  const box = $('#hero-countdown');
  if (!box) return;
  clearInterval(countdownTimer);
  const target = parseISO(endIso);
  const cells = {
    days: $('[data-unit="days"]', box),
    hours: $('[data-unit="hours"]', box),
    minutes: $('[data-unit="minutes"]', box),
    seconds: $('[data-unit="seconds"]', box),
  };
  if (!target) {
    for (const c of Object.values(cells)) c.textContent = '—';
    box.setAttribute('aria-label', 'No median end date: the season is not running.');
    return;
  }
  const tick = () => {
    const ms = target.getTime() - Date.now();
    if (ms <= 0) {
      for (const c of Object.values(cells)) c.textContent = '0';
      box.setAttribute('aria-label', 'The median end date has passed.');
      clearInterval(countdownTimer);
      return;
    }
    const s = Math.floor(ms / 1000);
    cells.days.textContent = String(Math.floor(s / 86400));
    cells.hours.textContent = String(Math.floor((s % 86400) / 3600)).padStart(2, '0');
    cells.minutes.textContent = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
    cells.seconds.textContent = String(s % 60).padStart(2, '0');
  };
  tick();
  box.setAttribute('aria-label', `Time remaining to the median end date, ${fmtDate(endIso)}`);
  countdownTimer = setInterval(tick, 1000);
}

/* ------------------------------------------------ the 3D hero (CONTRACT M1)
 * The answer is text on paper and paints first. The veil is already drawn.
 * Only then, when the browser is idle, the 3D scene is imported; it fades in
 * over the veil when its promise resolves. Any reason not to run it — no
 * WebGL, reduced motion, Save-Data, a 404, a rejected promise — leaves the
 * veil where it is, permanently.                                            */

let sceneHandle = null;

function webglOK() {
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch (_) { return false; }
}

function sceneBlocker() {
  if (QS.get('no3d') === '1') return 'disabled by ?no3d=1';
  if (MOCK) return 'mock mode: the 3D scene reads the live API, not fixtures';
  if (REDUCED_MOTION) return 'reduced motion';
  const conn = navigator.connection;
  if (conn && conn.saveData) return 'Save-Data';
  if (!webglOK()) return 'no WebGL';
  return null;
}

function whenIdle() {
  return new Promise((resolve) => {
    const go = () => {
      if ('requestIdleCallback' in window) requestIdleCallback(() => resolve(), { timeout: 2500 });
      else setTimeout(resolve, 600);
    };
    if (document.readyState === 'complete') go();
    else window.addEventListener('load', go, { once: true });
  });
}

/**
 * Camera padding for the hero scene, so on desktop the area of interest (the
 * Strait, KL and Singapore) sits to the right of the answer panel instead of
 * underneath it. On narrow screens the panel stacks above the map; no padding.
 */
function scenePadding(host) {
  const panel = $('.plate__panel');
  if (!panel || window.innerWidth < 760) return undefined;
  const h = host.getBoundingClientRect();
  const p = panel.getBoundingClientRect();
  const left = Math.max(0, Math.round(p.right - h.left + 24));
  // never pad away more than ~60% of the scene
  return { top: 0, right: 0, bottom: 0, left: Math.min(left, Math.round(h.width * 0.6)) };
}

async function mountHeroScene() {
  const host = $('#hero-scene');
  const plate = $('#plate');
  if (!host || !plate) return;
  const blocked = sceneBlocker();
  if (blocked) {
    plate.dataset.scene = `veil (${blocked})`;
    return;
  }
  plate.dataset.scene = 'loading';
  await whenIdle();
  try {
    const mod = await import('./sim3d/embed.js');
    if (typeof mod.mountBurningMap !== 'function') throw new Error('embed.js has no mountBurningMap');
    const narrow = window.innerWidth < 760;
    const lowMem = navigator.deviceMemory && navigator.deviceMemory < 4;
    sceneHandle = await mod.mountBurningMap(host, {
      mode: 'week',        // the real past 7 days; falls back to a model run and says so
      fallback: true,
      caption: true,       // one line naming what is on screen and its sources
      pins: true,
      autoplay: true,
      orbit: true,
      interactive: false,
      budget: narrow || lowMem ? 'low' : 'med',
      padding: scenePadding(host),
    });
    host.classList.add('is-live');
    sceneLive = true;
    // Record what is actually on screen. A model run is not this week's data,
    // and the embed's caption says so; this keeps the page state honest too.
    const meta = (sceneHandle && sceneHandle.meta) || {};
    plate.dataset.scene = meta.fixture ? '3d (fixture)' : `3d (${meta.mode || 'unknown'})`;
    if (store.season) renderVeilCaption(hazeReading(store.season), store.season);
    // once the fade is done, the veil underneath stops spending frames
    setTimeout(() => { if (veil && sceneLive) veil.setCovered(true); }, 1600);
  } catch (err) {
    console.info('3D hero not available; the haze veil stays.', err && err.message ? err.message : err);
    host.classList.remove('is-live');
    host.textContent = '';
    sceneLive = false;
    plate.dataset.scene = 'veil (3D failed to load)';
    try { if (sceneHandle && sceneHandle.destroy) sceneHandle.destroy(); } catch (_) { /* already gone */ }
    sceneHandle = null;
    if (veil) veil.setCovered(false);
  }
}

/* ------------------------------------------------------- the second answer */

function renderExhaustionHero(ex) {
  // CONTRACT M4: when peat and forest are split, the peat answer leads
  const hl = ex.headlines && ex.headlines.peat && ex.headlines.peat.exhaustion_year ? ex.headlines : null;
  if (hl) ex = { ...ex, exhaustion_year: hl.peat.exhaustion_year };
  const yr = ex.exhaustion_year || {};
  const yearNode = $('#grim-year');
  const answer = exhaustionYearText(ex);
  const { years, lastYear } = horizonOf(ex);

  yearNode.textContent = '';
  if (answer.reached) {
    yearNode.classList.remove('ending__year--text');
    const num = el('span');
    yearNode.append(el('span', { class: 'ending__approx', 'aria-hidden': 'true', text: '≈' }), num);
    animateNumber(num, answer.value, {
      from: new Date().getFullYear(), format: (v) => String(Math.round(v)), duration: 900,
    });
  } else {
    yearNode.classList.add('ending__year--text');
    yearNode.textContent = answer.text;
  }

  $('#grim-interval').textContent =
    yr.p10 != null && yr.p90 != null ? `${yr.p10}–${yr.p90}` : 'not reached on this horizon';
  $('#grim-never').textContent = neverText(ex);
  $('#grim-cum').textContent = ex.cumulative_burned_mha_by_2050 != null
    ? fmtNum(ex.cumulative_burned_mha_by_2050, 2) : '—';

  $('.ending__lead').textContent = hl
    ? `Kalimantan runs out of ${String(hl.peat.label || 'peat').toLowerCase()} to burn in`
    : 'Kalimantan runs out of things to burn in';
  let second = $('#grim-second');
  if (!second) {
    second = el('p', { class: 'ending__second', id: 'grim-second' });
    $('.ending__meta').after(second);
  }
  second.textContent = '';
  if (hl && hl.forest && hl.forest.exhaustion_year) {
    const f = hl.forest.exhaustion_year;
    second.append(`${hl.forest.label || 'Forest'}: `,
      el('b', { text: f.p50 != null ? `≈ ${f.p50}` : `not within ${fmtInt(years)} years` }),
      f.p10 != null && f.p90 != null ? ` (80% interval ${f.p10}–${f.p90})` : '',
      '. Peat is the one that matters for haze: it is what smoulders, and what does not grow back.');
  }
  const note = $('#grim-note');
  if (answer.reached) {
    const gens = Math.max(1, Math.round((answer.value - new Date().getFullYear()) / 30));
    note.textContent =
      'That is the year the fuel runs out, not the year the problem is solved. At the current '
      + `rate the burning outlasts everyone alive today and roughly ${numberWord(gens)} `
      + `generation${gens === 1 ? '' : 's'} after them. Peat that burns does not come back on any `
      + 'timescale that matters to the people breathing it.';
  } else {
    note.textContent =
      `The model was run ${years != null ? fmtInt(years) + ' years' : 'to its horizon'}`
      + `${lastYear ? `, to ${lastYear},` : ''} and the fuel had not run out by the end of it. `
      + 'That is a fact about the horizon, not a reprieve — the burning continues past the edge '
      + 'of the chart.';
  }
}

/* ---------------------------------------------------------- the human cost */

/** Highest mortality figure a season carries: the range's top, else the scalar. */
function deathsKey(s) {
  const r = rangeOf(s, 'excess_deaths');
  if (r) return r.high;
  return s.excess_deaths_est != null ? Number(s.excess_deaths_est) : -1;
}

function renderToll(history) {
  const seasons = (history.seasons || []).slice();
  const worst = seasons.filter((s) => deathsKey(s) >= 0).sort((a, b) => deathsKey(b) - deathsKey(a))[0];
  const node = $('#toll-text');
  const rangesBox = $('#toll-ranges');
  if (!node) return;
  node.textContent = '';
  rangesBox.textContent = '';
  rangesBox.classList.remove('has-two');
  if (!worst) {
    node.textContent = 'The API did not report mortality estimates for any season on record.';
    return;
  }
  const T = (t) => document.createTextNode(t);
  const B = (t) => el('b', { text: t });
  const bits = [T('The worst season on this record is '), B(String(worst.year)), T('. ')];

  const kalR = rangeOf(worst, 'burned_area_mha_kalimantan');
  if (kalR) {
    bits.push(T('Published estimates put its burned area in Kalimantan at between '),
      B(`${fmtNum(kalR.low, 2)} and ${fmtNum(kalR.high, 2)} Mha`), T('. '));
  } else if (worst.burned_area_mha_kalimantan != null) {
    bits.push(T('It burned '), B(`${fmtNum(worst.burned_area_mha_kalimantan, 2)} Mha`),
      T(` in Kalimantan${worst.estimated ? ' (an estimate)' : ''}. `));
  }

  const dR = rangeOf(worst, 'excess_deaths');
  if (dR) {
    const fold = foldOf(dR);
    bits.push(T('Studies attribute between '), B(fmtInt(dR.low)), T(' and '), B(fmtInt(dR.high)),
      T(' excess deaths across the region to its smoke'));
    if (fold) bits.push(T(` — they disagree by a factor of about ${fmtNum(fold, fold < 10 ? 1 : 0)}`));
  } else {
    bits.push(T('It is associated with an estimated '), B(fmtInt(worst.excess_deaths_est)),
      T(' excess deaths across the region'));
  }
  if (worst.economic_loss_usd_bn != null) {
    bits.push(T(', and about '), B(`US$${fmtNum(worst.economic_loss_usd_bn, 1)} bn`), T(' in economic losses'));
  }
  bits.push(T('.'));
  const pm = worst.pm25_peak_ugm3 || {};
  const city = Object.keys(pm).sort((a, b) => (pm[b] || 0) - (pm[a] || 0))[0];
  if (city && pm[city] != null) {
    bits.push(T(' Peak PM2.5 in '), B(city), T(' reached '), B(`${fmtInt(pm[city])} µg/m³`),
      T(`. The WHO 24-hour guideline is ${WHO_PM25_24H}.`));
  }
  for (const b of bits) node.append(b);

  const spreads = [];
  if (dR) spreads.push(spread(`Excess deaths attributed to the ${worst.year} smoke`, dR, worst.excess_deaths_est, fmtInt, ''));
  if (kalR) spreads.push(spread(`Burned area, Kalimantan, ${worst.year}`, kalR, worst.burned_area_mha_kalimantan, (v) => fmtNum(v, 2), ' Mha'));
  const idnR = rangeOf(worst, 'burned_area_mha_indonesia');
  if (idnR) spreads.push(spread(`Burned area, all Indonesia, ${worst.year}`, idnR, worst.burned_area_mha_indonesia, (v) => fmtNum(v, 2), ' Mha'));
  for (const s of spreads) rangesBox.append(s);
  if (spreads.length >= 2) rangesBox.classList.add('has-two');
}

/** A strip showing every published estimate, so the disagreement is visible. */
function spread(title, r, scalar, fmt, unit) {
  const top = r.high * 1.08 || 1;
  const pos = (v) => `${clamp((Number(v) / top) * 100, 0, 100)}%`;
  const axis = el('div', { class: 'spread__axis', 'aria-hidden': 'true' });
  axis.append(el('span', {
    class: 'spread__band',
    style: `left:${pos(r.low)};width:calc(${pos(r.high)} - ${pos(r.low)})`,
  }));
  for (const e of r.estimates) {
    axis.append(el('span', { class: 'spread__tick', style: `left:${pos(e.value)}`, title: `${fmt(e.value)}${unit} — ${e.source || 'unattributed'}` }));
  }
  const scalarShown = scalar != null && Number.isFinite(Number(scalar));
  if (scalarShown) {
    axis.append(el('span', { class: 'spread__tick spread__tick--scalar', style: `left:${pos(scalar)}` }));
  }
  const fold = foldOf(r);
  const list = el('ul', { class: 'spread__list' });
  const sorted = r.estimates.slice().sort((a, b) => Number(a.value) - Number(b.value));
  for (const e of sorted) {
    const link = doiUrl(e.doi);
    const src = el('span', { class: 'spread__src' }, [
      link ? el('a', { href: link, rel: 'noopener noreferrer', target: '_blank', text: e.source || e.doi })
        : (e.source || 'source not given'),
      e.region ? el('span', { class: 'spread__region', text: ` · ${e.region}` }) : null,
      !link && e.doi ? el('span', { class: 'spread__region', text: ` · doi ${e.doi}` }) : null,
    ]);
    list.append(el('li', {}, [el('span', { class: 'spread__val', text: `${fmt(e.value)}${unit}` }), src]));
  }
  return el('figure', { class: 'spread' }, [
    el('figcaption', {}, [
      el('p', { class: 'spread__title', text: title }),
      el('p', { class: 'spread__range' }, [
        el('b', { text: `${fmt(r.low)}–${fmt(r.high)}${unit}` }),
        fold ? el('span', { class: 'spread__fold', text: `  ·  ${fmtNum(fold, fold < 10 ? 1 : 0)}× apart` }) : null,
        r.central != null ? ` · central ${fmt(r.central)}${unit}` : null,
      ]),
    ]),
    axis,
    el('div', { class: 'spread__scale', 'aria-hidden': 'true' }, [
      el('span', { text: '0' }),
      el('span', { text: `${fmt(top)}${unit}` }),
    ]),
    el('p', { class: 'spread__key' }, [
      el('span', { class: 'spread__swatch', 'aria-hidden': 'true' }),
      'Black ticks: each study.',
      scalarShown ? [' ', el('span', { class: 'spread__swatch spread__swatch--scalar', 'aria-hidden': 'true' }),
        `Red: the single figure the season record carries, ${fmt(scalar)}${unit}.`] : null,
    ].flat()),
    sorted.length ? list : el('p', { class: 'spread__note', text: 'The API gave the range without its individual estimates.' }),
    r.note ? el('p', { class: 'spread__note', text: r.note }) : null,
  ]);
}

function renderHistoryStats(history) {
  const box = $('#history-stats');
  if (!box) return;
  box.textContent = '';
  const s = history.summary || {};
  const seasons = history.seasons || [];
  const el_nino = seasons.filter((x) => x.enso_phase === 'el_nino');
  const la_nina = seasons.filter((x) => x.enso_phase === 'la_nina');
  const meanDur = (arr) => (arr.length
    ? Math.round(arr.reduce((a, x) => a + (x.duration_days || 0), 0) / arr.length) : null);
  const first = s.first_year || (seasons[0] && seasons[0].year);
  if (first) $('#hist-h').textContent = `Every season since ${first}`;

  const stats = [
    ['Seasons on record', s.n_seasons != null ? fmtInt(s.n_seasons) : fmtInt(seasons.length), ''],
    ['Mean season length', fmtInt(s.mean_duration_days != null ? s.mean_duration_days : meanDur(seasons)), 'days'],
    ['Mean, El Niño years', fmtInt(s.mean_duration_el_nino != null ? s.mean_duration_el_nino : meanDur(el_nino)), 'days'],
    ['Mean, La Niña years', fmtInt(s.mean_duration_la_nina != null ? s.mean_duration_la_nina : meanDur(la_nina)), 'days'],
    ['Total burned, Kalimantan',
      fmtNum(s.total_burned_mha_kalimantan != null ? s.total_burned_mha_kalimantan
        : seasons.reduce((a, x) => a + (x.burned_area_mha_kalimantan || 0), 0), 2),
      first ? `Mha since ${first}` : 'Mha'],
  ];
  for (const [label, value, unit] of stats) {
    box.append(el('div', { class: 'stat' }, [
      el('dt', { text: label }),
      el('dd', {}, [value, unit ? el('small', { text: unit }) : null]),
    ]));
  }
}

/* ---------------------------------------------------------------- drivers */

function renderDrivers(season, scopeLabel) {
  const p = panel('drivers');
  const list = $('#drivers-list');
  if (!list) return;
  const drivers = (season.drivers || []).filter(Boolean).slice()
    .sort((a, b) => (b.contribution || 0) - (a.contribution || 0));
  if (!drivers.length) {
    setPanelState(p, 'error', 'The season model returned no driver breakdown for this run.');
    return;
  }
  const max = Math.max(...drivers.map((d) => d.contribution || 0), 0.0001);
  list.textContent = '';
  for (const d of drivers) {
    const dir = String(d.direction || '').toLowerCase();
    const kind = dir === 'shortens' ? 'shortens' : 'prolongs';
    const unit = unitLabel(d.unit);
    const li = el('li', { class: `driver driver--${kind}`, 'data-driver': d.id || '' }, [
      el('div', { class: 'driver__head' }, [
        el('span', { class: 'driver__name', text: d.name || titleCase(d.id || 'unnamed driver') }),
        el('span', { class: 'driver__dir', text: dir ? `${dir} the season` : 'direction unstated' }),
      ]),
      el('span', { class: 'driver__bar', role: 'presentation' }, [el('i', { class: 'driver__fill' })]),
      el('span', { class: 'driver__value' }, [
        `${pct(d.contribution, 0)}`,
        el('small', { text: d.value != null ? `reading ${fmtSig(Number(d.value))}${unit ? ' ' + unit : ''}` : 'no reading' }),
      ]),
      el('p', { class: 'driver__explain', text: d.explain || 'No explanation supplied for this driver.' }),
    ]);
    list.append(li);
    const fill = $('.driver__fill', li);
    const w = `${((d.contribution || 0) / max) * 100}%`;
    requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = w; }));
  }
  const total = drivers.reduce((a, d) => a + (d.contribution || 0), 0);
  const oni = season.oni != null ? season.oni : (drivers.find((d) => d.id === 'oni') || {}).value;
  $('#drivers-scope').textContent =
    `${scopeLabel} · scenario “${titleCase(season.scenario || 'baseline')}”, ` +
    `ONI ${fmtSig(oni)} · ${drivers.length} drivers, together ${fmtNum(total, 2)}.`;
  setPanelState(p, 'ok');
}


/* ------------------------------------------------- this week (CONTRACT M5)
 * Rules: every reading carries its own index, averaging and measured/
 * modelled status; PSI, API (IPU) and modelled US AQI never share an axis;
 * official measurements lead; nothing here is hardcoded — no data, no claim. */

const INDEX_NAME = {
  PSI: 'PSI',
  API: 'API (IPU)',
};
const BANDS = {
  // PSI (SG) and API/IPU (MY) share the same band edges, each under its own name
  official: [[50, 'Good'], [100, 'Moderate'], [200, 'Unhealthy'], [300, 'Very Unhealthy'], [Infinity, 'Hazardous']],
  usaqi: [[50, 'Good'], [100, 'Moderate'], [150, 'Unhealthy for Sensitive Groups'], [200, 'Unhealthy'], [300, 'Very Unhealthy'], [Infinity, 'Hazardous']],
};
function bandOf(kind, v) {
  if (v == null || !Number.isFinite(Number(v))) return null;
  for (const [edge, name] of BANDS[kind]) if (Number(v) <= edge) return name;
  return null;
}
const MY_NAMES = { 'my-kuala-lumpur': 'Kuala Lumpur', 'my-shah-alam': 'Shah Alam', 'my-klang': 'Klang', 'my-petaling-jaya': 'Petaling Jaya' };
const placeShort = (p) => (p.country === 'SG' ? String(p.name || p.id).replace(/^Singapore\s*[-—–:]?\s*/i, '') || p.name : p.name || MY_NAMES[p.id] || p.id);

function renderHeadline(obs) {
  const p = panel('now-headline');
  const h = obs && obs.headline;
  if (!h || !h.text) { setPanelState(p, 'error'); return; }   // no data, no claim
  $('#local-text').textContent = h.text;
  const meta = $('#local-meta');
  meta.textContent = '';
  meta.append(
    el('span', { class: `tag ${h.measured ? 'tag--ok' : 'tag--warn'}`, text: h.measured ? 'measured' : 'modelled' }),
    el('span', { text: h.index ? (INDEX_NAME[h.index] || h.index) : '' }),
    el('a', { class: 'local__more', href: '#now', text: 'See the week' }),
  );
  setPanelState(p, 'ok');
}

function nowCard({ place, indexLabel, measured, source, averaging, lines, maxPt, maxWho, bandKind, unit, note, threshold }) {
  const idx = `now-chart-${Math.random().toString(36).slice(2, 8)}`;
  const band = maxPt ? bandOf(bandKind, maxPt.v) : null;
  const legend = el('div', { class: 'legend' });
  const canvas = el('canvas', { id: idx, role: 'img', 'aria-label': `${indexLabel} over the last seven days, ${place}` });
  const card = el('article', { class: 'now-card' }, [
    el('div', { class: 'now-card__head' }, [el('h3', { class: 'now-card__place', text: place })]),
    el('p', { class: 'now-card__index' }, [
      el('span', { class: `tag ${measured ? 'tag--ok' : 'tag--warn'}`, text: measured ? 'measured' : 'modelled' }),
      `${indexLabel}${averaging ? `, ${averaging} average` : ''}${source ? ` · ${source}` : ''}`,
    ]),
    maxPt ? el('p', { class: 'now-card__peak' }, [
      el('b', { text: fmtInt(maxPt.v) }),
      el('span', {}, [
        `highest ${indexLabel}${maxWho ? `, ${maxWho}` : ''}, ${fmtLocalTime(maxPt.t)}`,
        band ? [' · ', el('span', { class: `now-card__band${Number(maxPt.v) > 200 ? ' now-card__band--high' : ''}`, text: band })] : null,
      ].flat()),
    ]) : el('p', { class: 'now-card__note', text: 'No maximum reported for this window.' }),
    el('div', { class: 'chart-holder chart-holder--now' }, [canvas]),
    legend,
    note ? el('p', { class: 'now-card__note', text: note }) : null,
  ]);
  return { card, draw: () => renderNowChart(canvas, legend, { lines, threshold, thresholdLabel: `${threshold} · Very Unhealthy above`, unit }) };
}

function maxOf(lines) {
  let best = null, who = null;
  for (const l of lines) for (const pt of l.points) {
    if (pt.v != null && (!best || Number(pt.v) > Number(best.v))) { best = pt; who = l.label; }
  }
  return { best, who };
}

function renderNow(obs) {
  const box = $('#now-charts');
  box.textContent = '';
  const places = (obs.places || []).filter(Boolean);
  const w = obs.window || {};
  $('#now-window').textContent = w.start && w.end
    ? `Seven days, ${fmtLocalTime(w.start)} to ${fmtLocalTime(w.end)}, Malaysia and Singapore time.`
    : (obs.as_of ? `As of ${fmtLocalTime(obs.as_of)}.` : '');

  const cards = [];
  // official readings first, one card per index and country
  const groups = new Map();
  for (const p of places) {
    const o = p.official;
    if (!o || !Array.isArray(o.series) || !o.series.length) continue;
    const key = `${p.country}|${o.index}|${o.averaging || ''}`;
    if (!groups.has(key)) groups.set(key, { country: p.country, o, members: [] });
    groups.get(key).members.push(p);
  }
  for (const g of groups.values()) {
    const lines = g.members.map((p) => ({ label: placeShort(p), points: p.official.series }));
    const { best, who } = maxOf(lines);
    // PM2.5 alongside, in words only: it is a concentration, not an index
    const pm = g.members.map((p) => p.pm25 && p.pm25.max ? { ...p.pm25.max, who: placeShort(p) } : null).filter(Boolean)
      .sort((a, b) => b.v - a.v)[0];
    cards.push(nowCard({
      place: g.country === 'SG' ? 'Singapore' : 'Klang Valley',
      indexLabel: INDEX_NAME[g.o.index] || g.o.index,
      measured: true,
      source: g.o.source,
      averaging: g.o.averaging,
      lines, maxPt: best, maxWho: lines.length > 1 ? who : null,
      bandKind: 'official', unit: '', threshold: 200,
      note: pm ? `Highest 1-hour PM2.5: ${fmtInt(pm.v)} µg/m³ (${pm.who}, ${fmtLocalTime(pm.t)}). That is a concentration, not a ${INDEX_NAME[g.o.index] || g.o.index} value.` : null,
    }));
  }
  // modelled values only where there is no official reading, merged per identical grid cell
  const gaps = places.filter((p) => !(p.official && p.official.series && p.official.series.length) && p.modelled && Array.isArray(p.modelled.series) && p.modelled.series.length);
  const byCountry = new Map();
  for (const p of gaps) {
    if (!byCountry.has(p.country)) byCountry.set(p.country, []);
    byCountry.get(p.country).push(p);
  }
  for (const [country, members] of byCountry) {
    const cells = new Map();
    for (const p of members) {
      const sig = JSON.stringify(p.modelled.series.map((x) => x.v));
      if (!cells.has(sig)) cells.set(sig, []);
      cells.get(sig).push(p);
    }
    const lines = [...cells.values()].map((ps) => ({ label: ps.map(placeShort).join(', '), points: ps[0].modelled.series }));
    const { best, who } = maxOf(lines);
    const merged = [...cells.values()].some((ps) => ps.length > 1);
    const grid = members[0].modelled.grid_km;
    cards.push(nowCard({
      place: country === 'SG' ? 'Singapore' : 'Kuala Lumpur and Selangor',
      indexLabel: 'US AQI (modelled, CAMS)',
      measured: false,
      source: members[0].modelled.source,
      averaging: null,
      lines, maxPt: best, maxWho: lines.length > 1 ? who : null,
      bandKind: 'usaqi', unit: '', threshold: 200,
      note: [
        'A forecast model’s estimate, not an official Malaysian API (IPU) reading. US AQI uses different bands from the API.',
        merged && grid ? `The model’s grid is about ${fmtInt(grid)} km, so these places share one value.` : null,
        country === 'MY' ? 'Official readings: APIMS, Department of Environment Malaysia.' : null,
      ].filter(Boolean).join(' '),
    }));
  }
  if (!cards.length) throw new Error('the endpoint returned no readings for any place');
  for (const c of cards) box.append(c.card);
  setPanelState(panel('now'), 'ok');
  for (const c of cards) c.draw();
  if (Array.isArray(obs.caveats) && obs.caveats.length) registerCaveats('This week’s readings — /api/now/observed', obs.caveats);
}

async function loadNow() {
  setPanelState(panel('now'), 'loading');
  setPanelState(panel('now-headline'), 'loading');
  try {
    if (await routeMissing('/api/now/observed')) throw notYet('/api/now/observed');
    const obs = await api('/api/now/observed?days=7');
    renderHeadline(obs);
    renderNow(obs);
  } catch (e) {
    setPanelState(panel('now-headline'), 'error');
    setPanelState(panel('now'), 'error', e && e.status === 404
      ? 'The live readings service is not deployed yet, so this page makes no claim about this week.'
      : `${describeError(e, 'this week’s readings')} No claim is made about this week without them.`);
  }
}

async function loadHotspots() {
  const p = panel('now-hotspots');
  setPanelState(p, 'loading');
  try {
    if (await routeMissing('/api/now/hotspots')) throw notYet('/api/now/hotspots');
    const h = await api('/api/now/hotspots?days=7');
    const counts = h.count_by_region || {};
    const frp = h.frp_gw_by_region || {};
    const order = ['kalimantan', 'sumatra', 'peninsular_malaysia'];
    const ids = [...order.filter((k) => k in counts), ...Object.keys(counts).filter((k) => !order.includes(k))];
    if (!ids.length) throw new Error('no regional counts returned');
    const dl = $('#fires-counts');
    dl.textContent = '';
    const label = (k) => titleCase(k).replace('Peninsular Malaysia', 'Peninsular Malaysia');
    for (const k of ids) {
      dl.append(el('div', {}, [
        el('dt', {}, [el('i', { style: `background:${({ kalimantan: COLOR.ember, sumatra: COLOR.amber, peninsular_malaysia: COLOR.cool })[k] || COLOR.haze}`, 'aria-hidden': 'true' }), label(k)]),
        el('dd', {}, [fmtInt(counts[k]), el('small', { text: frp[k] != null ? `detections · ${fmtSig(frp[k], 2)} GW fire radiative power` : 'detections' })]),
      ]));
    }
    setPanelState(p, 'ok');
    if (Array.isArray(h.by_day) && h.by_day.length) {
      renderFiresByDay('#chart-fires', h.by_day, ids.map((id) => ({ id, label: label(id) })));
    }
    $('#fires-note').textContent = [
      'Satellite hotspot detections, not individual fires: one burning peat dome can produce many.',
      h.attribution || h.source || '',
    ].filter(Boolean).join(' ');
    if (Array.isArray(h.caveats) && h.caveats.length) registerCaveats('Fire detections — /api/now/hotspots', h.caveats);
  } catch (e) {
    setPanelState(p, 'error', e && e.status === 404
      ? 'The fire-detection feed is not deployed yet.'
      : describeError(e, 'fire detections'));
  }
}

/* ------------------------------------------------------------ odds (M2)
 * Prediction-market presentation of the model's own probabilities. No
 * market, no money: the wording below is deliberate, see CONTRACT_V2 M2.   */

const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

function isoAdd(iso, n) {
  const d = parseISO(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Provisional odds read straight off the season model's daily curve. */
function deriveOdds(season) {
  const daily = (season && season.daily_end_probability) || [];
  if (!daily.length) throw new Error('the season model returned no daily curve to read odds from');
  const asOf = season.as_of || TODAY;
  const lastDate = daily[daily.length - 1].date;
  const stillAt = (iso) => {
    let v = null;
    for (const d of daily) { if (d.date <= iso) v = Number(d.p_still_burning); else break; }
    return v == null ? 1 : v;
  };
  // month ends after as_of that the curve reaches
  const markets = [];
  const start = parseISO(asOf);
  for (let k = 0; k < 6 && markets.length < 3; k += 1) {
    const end = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1 + k, 0));
    const iso = end.toISOString().slice(0, 10);
    if (iso <= asOf) continue;
    if (iso > lastDate) break;
    const yes = clamp(1 - stillAt(iso), 0, 1);
    markets.push({
      id: `end-by-${iso}`,
      question: `Will the haze season end by ${fmtDate(iso)}?`,
      threshold_date: iso,
      yes,
      no: 1 - yes,
    });
  }
  // weekly distribution of the end date
  const outcomes = [];
  let cursor = isoAdd(asOf, 1);
  while (cursor <= lastDate) {
    const wEnd = isoAdd(cursor, 6);
    const p = daily.filter((d) => d.date >= cursor && d.date <= wEnd)
      .reduce((a, d) => a + (Number(d.p_end) || 0), 0);
    outcomes.push({ label: weekLabel(cursor, wEnd), start: cursor, end: wEnd, p });
    cursor = isoAdd(wEnd, 1);
  }
  const later = clamp(Number(daily[daily.length - 1].p_still_burning) || 0, 0, 1);
  if (later > 0.0005) outcomes.push({ label: `After ${fmtDate(lastDate, { withYear: false })}`, start: lastDate, end: null, p: later });
  return {
    provisional: true,
    as_of: asOf,
    n_sims: SEASON_SIMS,
    markets,
    distribution: { bucket: 'week', outcomes },
    resolution: null,
    model_error_days: null,
    caveats: [],
  };
}

function weekLabel(a, b) {
  const da = parseISO(a), db = b ? parseISO(b) : null;
  if (!db) return `From ${fmtDate(a, { withYear: false })}`;
  const same = da.getUTCMonth() === db.getUTCMonth();
  return same
    ? `${da.getUTCDate()}–${db.getUTCDate()} ${fmtDate(b, { withYear: false }).split(' ')[1]}`
    : `${fmtDate(a, { withYear: false })} – ${fmtDate(b, { withYear: false })}`;
}

function normalizeOdds(raw) {
  if (!raw || !Array.isArray(raw.markets)) throw new Error('odds response has no markets');
  const markets = raw.markets.filter((m) => m && Number.isFinite(Number(m.yes))).map((m) => {
    const yes = clamp(Number(m.yes), 0, 1);
    return { ...m, yes, no: Number.isFinite(Number(m.no)) ? clamp(Number(m.no), 0, 1) : 1 - yes };
  });
  return { ...raw, provisional: false, markets };
}

function compactPct(p) {
  if (p == null || !Number.isFinite(p)) return '—';
  if (p > 0 && p < 0.01) return '<1%';
  if (p < 1 && p > 0.99) return '>99%';
  return `${Math.round(p * 100)}%`;
}

function renderOdds(odds, season) {
  store.odds = odds;
  const nSims = odds.n_sims || SEASON_SIMS;
  $('#odds-n').textContent = fmtInt(nSims);

  const src = $('#odds-source');
  src.textContent = '';
  if (odds.provisional) {
    src.append(el('b', { text: 'Provisional. ' }), document.createTextNode(
      'The dedicated odds service is not live yet, so these are read directly off the season '
      + `model’s daily curve — the same ${fmtInt(nSims)} simulated seasons as the answer at the top, `
      + `as of ${fmtDate(odds.as_of)}. They cannot disagree with it.`));
  } else {
    src.append(document.createTextNode(`As of ${fmtDate(odds.as_of)}.`));
    if (odds.model_error_days != null) {
      src.append(document.createTextNode(' The underlying model’s published forecast error is about '),
        el('b', { text: `${fmtNum(odds.model_error_days, 0)} days` }),
        document.createTextNode(' — read the percentages with that in mind.'));
    }
    if (odds.season_active === false) {
      src.append(document.createTextNode(' No season is active right now.'));
    }
  }

  const grid = $('#odds-markets');
  grid.textContent = '';
  if (!odds.markets.length) {
    grid.append(el('p', { class: 'odds-empty', text: 'The model returned no yes-or-no questions for this run.' }));
  }
  for (const m of odds.markets) grid.append(marketCard(m, odds));

  renderDistribution(odds);
  if (Array.isArray(odds.caveats) && odds.caveats.length) {
    registerCaveats('Forecast odds — /api/forecast/odds', odds.caveats);
  }
}

function marketCard(m, odds) {
  const id = `market-${String(m.id).replace(/[^a-z0-9-]/gi, '-')}`;
  const yesPct = compactPct(m.yes);
  const noPct = compactPct(m.no);
  const bar = el('div', { class: 'market__bar', role: 'img', 'aria-label': `Yes ${yesPct}, no ${noPct}` }, [
    el('span', { class: 'is-yes', style: `flex:0 0 ${(m.yes * 100).toFixed(1)}%`, text: m.yes >= 0.16 ? `Yes ${yesPct}` : '' }),
    el('span', { class: 'is-no', style: `flex:1 1 auto`, text: m.no >= 0.16 ? `No ${noPct}` : '' }),
  ]);
  const res = el('div', { class: 'market__resolve' }, [el('h4', { text: 'How this resolves' })]);
  if (odds.resolution && (odds.resolution.definition || odds.resolution.source)) {
    if (odds.resolution.definition) res.append(el('p', { text: odds.resolution.definition }));
    res.append(el('p', {}, [
      `Yes if that happens on or before ${fmtDate(m.threshold_date)}. `,
      odds.resolution.source ? `Source: ${odds.resolution.source}.` : 'No resolution source was given.',
    ]));
  } else {
    res.append(
      el('p', { text: `“Ends” here means the season model’s simulated burning episode ends on or before ${fmtDate(m.threshold_date)} — the burning, not the haze: the air can stay bad for days afterwards.` }),
      el('p', { text: 'Nothing external resolves these provisional odds yet. The odds service, when it is live, publishes the resolution source.' }),
    );
  }
  const status = el('span', { class: 'market__copied', role: 'status', 'aria-live': 'polite' });
  const send = el('button', { type: 'button', class: 'btn btn--ghost', text: 'Send link' });
  send.addEventListener('click', () => shareMarket(m, id, status));

  return el('article', { class: 'market', id }, [
    el('h3', { class: 'market__q', text: m.question }),
    el('div', { class: 'market__head' }, [
      el('p', { class: 'market__yes' }, [yesPct, el('small', { text: 'yes' })]),
    ]),
    bar,
    el('p', { class: 'market__legend' }, [
      el('span', {}, ['Yes ', el('b', { text: yesPct })]),
      el('span', {}, ['No ', el('b', { text: noPct })]),
    ]),
    res,
    el('div', { class: 'market__actions' }, [send, status]),
  ]);
}

async function shareMarket(m, id, status) {
  const url = `${location.origin}${location.pathname}#${id}`;
  const text = `${m.question} The model says ${compactPct(m.yes)} yes, from its simulated seasons. Model odds, not a market.`;
  try {
    if (navigator.share) {
      await navigator.share({ title: m.question, text, url });
      return;
    }
  } catch (e) {
    if (e && e.name === 'AbortError') return;
  }
  try {
    await navigator.clipboard.writeText(url);
    status.textContent = 'Link copied.';
  } catch (_) {
    status.textContent = url;
  }
  setTimeout(() => { status.textContent = ''; }, 4000);
}

function renderDistribution(odds) {
  const list = $('#odds-dist');
  list.textContent = '';
  const outs = ((odds.distribution && odds.distribution.outcomes) || [])
    .filter((o) => o && Number.isFinite(Number(o.p)));
  const fig = $('#odds-dist-fig');
  if (!outs.length) {
    list.append(el('li', {}, [el('span', { class: 'dist__label', text: 'No distribution returned.' })]));
    return;
  }
  // collapse long thin tails into the first and last rows that matter
  const sig = outs.map((o) => Number(o.p) >= 0.01);
  const first = sig.indexOf(true), last = sig.lastIndexOf(true);
  let rows = outs;
  if (first > 0 || (last >= 0 && last < outs.length - 1)) {
    rows = [];
    const head = outs.slice(0, Math.max(0, first)).reduce((a, o) => a + Number(o.p), 0);
    if (head >= 0.0005) rows.push({ label: `Before ${fmtDate(outs[first].start, { withYear: false })}`, p: head });
    rows.push(...outs.slice(Math.max(0, first), last + 1));
    const tail = outs.slice(last + 1).reduce((a, o) => a + Number(o.p), 0);
    if (tail >= 0.0005) rows.push({ label: `Later`, p: tail });
  }
  const max = Math.max(...rows.map((o) => Number(o.p)), 0.0001);
  const mode = rows.reduce((b, o) => (Number(o.p) > Number(b.p) ? o : b), rows[0]);
  for (const o of rows) {
    const w = `${(Number(o.p) / max) * 100}%`;
    const fill = el('i');
    list.append(el('li', { class: o === mode ? 'is-mode' : null }, [
      el('span', { class: 'dist__label', text: o.label || weekLabel(o.start, o.end) }),
      el('span', { class: 'dist__bar', 'aria-hidden': 'true' }, [fill]),
      el('span', { class: 'dist__p', text: compactPct(Number(o.p)) }),
    ]));
    requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = w; }));
  }
  $('#odds-dist-sub').textContent = `Share of the ${fmtInt(odds.n_sims || SEASON_SIMS)} simulated seasons ending in each week. The darkest bar is the most likely week.`;
  if (fig) fig.hidden = false;
}

/* The odds routes land after this page does. Asking the API's own schema
 * first means a missing route is a quiet "not yet", not a 404 in the console. */
let routesP = null;
function apiRoutes() {
  if (MOCK) return Promise.resolve(null);
  if (!routesP) {
    routesP = fetch('/api/openapi.json')
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => (j && j.paths ? new Set(Object.keys(j.paths)) : null))
      .catch(() => null);
  }
  return routesP;
}
async function routeMissing(path) {
  const routes = await apiRoutes();
  return !!routes && !routes.has(path);
}
function notYet(path) {
  return Object.assign(new Error(`${path} is not deployed yet`), { status: 404 });
}

async function loadOdds() {
  const p = panel('odds');
  setPanelState(p, 'loading');
  let odds = null;
  let liveErr = null;
  try {
    if (await routeMissing('/api/forecast/odds')) throw notYet('/api/forecast/odds');
    odds = normalizeOdds(await api('/api/forecast/odds'));
  } catch (e) {
    liveErr = e;
  }
  if (!odds) {
    if (!store.season) {
      setPanelState(p, 'error',
        `${describeError(liveErr, 'forecast odds')} The season model did not answer either, so there is nothing to read provisional odds from.`);
      setPanelState(panel('odds-history'), 'error', 'Odds history is not available yet.');
      return;
    }
    try {
      odds = deriveOdds(store.season);
    } catch (e) {
      setPanelState(p, 'error', describeError(e, 'forecast odds'));
      return;
    }
  }
  setPanelState(p, 'ok');
  renderOdds(odds, store.season);
  loadOddsHistory(odds);
}

async function loadOddsHistory(odds) {
  const p = panel('odds-history');
  setPanelState(p, 'loading');
  try {
    if (await routeMissing('/api/forecast/history')) throw notYet('/api/forecast/history');
    const h = await api('/api/forecast/history');
    const series = (h && Array.isArray(h.series)) ? h.series : [];
    if (series.length < 2) throw new Error('fewer than two days of history so far');
    setPanelState(p, 'ok');
    renderOddsHistory('#chart-odds', '#odds-history-legend', h, odds.markets);
    $('#odds-history-method').textContent = h.method
      ? `${h.method}${h.computed_at ? ` Computed ${fmtDate(String(h.computed_at).slice(0, 10))}.` : ''}`
      : '';
    if (Array.isArray(h.caveats) && h.caveats.length) {
      registerCaveats('Odds history — /api/forecast/history', h.caveats);
    }
  } catch (e) {
    const status = e && e.status;
    setPanelState(p, 'error',
      (status === 404 || status === 503 || !status
        ? 'Odds history is not available yet. '
        : `${describeError(e, 'odds history')} `)
      + 'It has to be recomputed day by day using only the weather known on each day, so it '
      + 'cannot be drawn from today’s run without pretending to a skill the model does not have.');
  }
}

/* --------------------------------------------------- caveats & provenance */

export function registerCaveats(label, caveats) {
  if (!Array.isArray(caveats) || !caveats.length) return;
  const fingerprint = caveats.join('\u0000');
  for (const [existing, items] of caveatRegistry) {
    if (existing !== label && items.join('\u0000') === fingerprint) return;
  }
  caveatRegistry.set(label, caveats);
  renderCaveats();
}

function renderCaveats() {
  const p = panel('caveats');
  const box = $('#caveats-groups');
  if (!box) return;
  box.textContent = '';
  if (!caveatRegistry.size) {
    setPanelState(p, 'error', 'No endpoint has returned a caveats list yet.');
    return;
  }
  for (const [label, items] of caveatRegistry) {
    box.append(el('section', { class: 'caveat-group' }, [
      el('h4', { text: label }),
      el('ul', { class: 'caveat-list' }, items.map((c) => el('li', { text: String(c) }))),
    ]));
  }
  setPanelState(p, 'ok');
}

export function registerProvenance(payload) {
  provPayload = payload;
  renderProvenance();
}

const STATUS_TAG = {
  measured: 'tag--ok',
  estimated: 'tag--est',
  synthesised: 'tag--synth',
  modelled: 'tag--modelled',
};

function renderProvenance() {
  const p = panel('provenance');
  const tbody = $('#prov-table tbody');
  if (!tbody) return;
  tbody.textContent = '';
  const rows = (provPayload && provPayload.datasets) || [];
  if (!rows.length) {
    setPanelState(p, 'error', 'The provenance endpoint returned no datasets.');
    return;
  }
  $('#prov-note').textContent = provPayload.note
    || 'Status is reported by the API for each dataset it serves.';

  const counts = new Map();
  for (const r of rows) counts.set(r.status, (counts.get(r.status) || 0) + 1);
  const countBox = $('#prov-counts');
  countBox.textContent = '';
  countBox.append(el('span', { class: 'prov-counts__total', text: `${fmtInt(rows.length)} datasets:` }));
  for (const [status, n] of [...counts].sort((a, b) => b[1] - a[1])) {
    countBox.append(el('span', { class: `tag ${STATUS_TAG[status] || ''}`.trim(), text: `${fmtInt(n)} ${status}` }));
  }

  // the synthesised rows are the ones a reader must not mistake for observations
  const synthBox = $('#prov-synth');
  synthBox.textContent = '';
  for (const r of rows.filter((x) => x.status === 'synthesised')) {
    synthBox.append(el('div', { class: 'prov-synth' }, [
      el('p', { class: 'prov-synth__head' }, [
        el('span', { class: 'tag tag--synth', text: 'synthesised' }),
        el('b', { text: r.label || r.id }),
      ]),
      el('p', { class: 'prov-synth__body', text: r.notes || r.source || '' }),
    ]));
  }

  for (const r of rows) {
    const sourceCell = el('td', {}, [
      r.url
        ? el('a', { href: r.url, rel: 'noopener noreferrer', target: '_blank', text: r.source || r.url })
        : document.createTextNode(r.source || 'no source string returned'),
    ]);
    if (r.notes) sourceCell.append(el('p', { class: 'prov-table__notes', text: r.notes }));
    tbody.append(el('tr', { class: r.status === 'synthesised' ? 'prov-row--synth' : null }, [
      el('td', {}, [
        el('b', { text: r.label || r.id }),
        r.coverage ? el('span', { class: 'prov-table__cov', text: r.coverage }) : null,
      ]),
      sourceCell,
      el('td', {}, [el('span', { class: `tag ${STATUS_TAG[r.status] || ''}`.trim(), text: r.status || 'unstated' })]),
    ]));
  }
  setPanelState(p, 'ok');
}

/* ---------------------------------------------------------------- loaders */

async function loadSeason() {
  try {
    return await withPanel('season', 'season model', async () => {
      const season = await api('/api/simulate/season', {
        method: 'POST',
        body: { as_of: TODAY, region: 'kalimantan', n_sims: SEASON_SIMS, scenario: 'baseline', seed: 7 },
      });
      store.season = season;
      setPanelState(panel('season'), 'ok');   // reveal before measuring the curve
      renderHero(season);
      renderDrivers(season, 'Baseline run');
      registerCaveats('Season model — /api/simulate/season', season.caveats);
      return season;
    });
  } catch (e) {
    setPanelState(panel('drivers'), 'error', describeError(e, 'season model'));
    $('#veil-caption').textContent =
      'The sky above is drawn at a neutral haze because the season model did not answer.';
    throw e;
  }
}

async function loadExhaustion() {
  return withPanel('exhaustion-hero', 'fuel-exhaustion model', async () => {
    const ex = await api('/api/simulate/exhaustion', {
      method: 'POST',
      body: { n_sims: 2000, horizon_years: DEFAULT_HORIZON_YEARS, burn_reduction_pct: 0, seed: 7 },
    });
    store.exhaustion = ex;
    renderExhaustionHero(ex);
    registerCaveats('Fuel-exhaustion model — /api/simulate/exhaustion', ex.caveats);
    return ex;
  });
}

async function loadHistory() {
  let history;
  try {
    history = await withPanel('history', 'history', async () => {
      const h = await api('/api/history');
      store.history = h;
      renderHistoryStats(h);
      initHistoryCharts(h);
      const caveats = Array.isArray(h.caveats) ? h.caveats : (h.summary && h.summary.caveats) || [];
      registerCaveats('Historical record — /api/history', caveats);
      return h;
    });
  } catch (e) {
    setPanelState(panel('toll'), 'error', describeError(e, 'history'));
    throw e;
  }
  try {
    renderToll(history);
    setPanelState(panel('toll'), 'ok');
  } catch (e) {
    console.error('[toll]', e);
    setPanelState(panel('toll'), 'error', describeError(e, 'history'));
  }
  return history;
}

async function loadMapData() {
  return withPanel('map', 'map data', async () => {
    const [regions, fuel, receptors] = await Promise.all([
      api('/api/regions'), api('/api/fuel'), api('/api/receptors'),
    ]);
    store.regions = regions;
    store.fuel = fuel;
    store.receptors = receptors;
    registerCaveats('Fuel stock — /api/fuel', fuel.caveats);
    await initMap({ regions, fuel, receptors, history: store.history, meteo: store.meteo });
    return { regions, fuel, receptors };
  });
}

async function loadProvenance() {
  return withPanel('provenance', 'provenance', async () => {
    const p = await api('/api/provenance');
    registerProvenance(p);
    return p;
  });
}

async function loadMeteo() {
  // Optional: only used to pre-fill the plume's wind vector.
  try {
    const end = new Date(Date.now() + 6 * 86400000).toISOString().slice(0, 10);
    store.meteo = await api(`/api/meteo?lat=-2.2&lon=113.9&start=${TODAY}&end=${end}`);
  } catch (e) {
    console.info('meteo unavailable, plume uses slider defaults');
    store.meteo = null;
  }
}

/* ------------------------------------------------------------------- boot */

function wireRetries() {
  const map = {
    season: async () => { await loadSeason(); return loadOdds(); },
    exhaustion: loadExhaustion,
    history: loadHistory,
    map: loadMapData,
    provenance: loadProvenance,
    odds: loadOdds,
    now: () => Promise.all([loadNow(), loadHotspots()]),
  };
  for (const btn of $$('[data-retry]')) {
    btn.addEventListener('click', () => {
      const fn = map[btn.dataset.retry];
      if (fn) fn().catch(() => { /* panel already shows the error */ });
    });
  }
}

function wireCrossLinks() {
  document.addEventListener('wwhe:select-year', (e) => setMapYear(e.detail.year, { scroll: true }));
  document.addEventListener('wwhe:sim-season', (e) => renderDrivers(e.detail.season, 'Playground run'));
  document.addEventListener('wwhe:caveats', (e) => registerCaveats(e.detail.label, e.detail.caveats));
  document.addEventListener('input', (e) => { if (e.target && e.target.type === 'range') paintRange(e.target); });
  let busyTimer = null;
  document.addEventListener('wwhe:inflight', (e) => {
    const box = $('#api-status');
    if (!box) return;
    clearTimeout(busyTimer);
    if (e.detail.count > 0) box.dataset.busy = 'true';
    // a short tail so sub-frame requests do not flicker the dot
    else busyTimer = setTimeout(() => { box.dataset.busy = 'false'; }, 150);
  });
}

async function boot() {
  if (MOCK) {
    $('#mock-banner').hidden = false;
    $('#mock-strip').hidden = false;
    setStatus('mock', 'mock fixtures');
  } else {
    setStatus('pending', 'connecting…');
  }

  initVeil();

  if (window.Chart) {
    Chart.defaults.color = COLOR.ink3;
    Chart.defaults.borderColor = COLOR.line;
    Chart.defaults.font.family = '"Public Sans", system-ui, sans-serif';
    Chart.defaults.font.size = 12;
    if (REDUCED_MOTION) Chart.defaults.animation = { duration: 120 };
  }

  wireRetries();
  wireCrossLinks();
  paintAllRanges();

  api('/api/health').then((h) => {
    store.health = h;
    if (!MOCK) {
      setStatus('ok', `API ok${h.version ? ' · ' + h.version : ''}${h.live_weather_available ? ' · live weather' : ' · no live weather'}`);
    }
  }).catch(() => {
    if (!MOCK) setStatus('error', 'API unreachable');
  });

  const quiet = (p) => p.catch(() => null);

  // The answer first: nothing else is allowed to delay it. This week's
  // readings run alongside, because they lead the hero for local readers.
  const seasonP = quiet(loadSeason());
  quiet(loadNow());
  quiet(loadHotspots());
  const exP = quiet(loadExhaustion());
  quiet(loadMeteo()).then(() => { if (store.meteo) applyMeteo(store.meteo); });

  seasonP.then(() => {
    quiet(loadOdds());
    mountHeroScene();
  });

  // history before the map: the year slider needs the list of seasons
  const histP = quiet(loadHistory());
  await histP;
  await Promise.all([seasonP, exP, quiet(loadMapData()), quiet(loadProvenance())]);

  initSim({ fuel: store.fuel, season: store.season, exhaustion: store.exhaustion, today: TODAY });
  renderCaveats();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
