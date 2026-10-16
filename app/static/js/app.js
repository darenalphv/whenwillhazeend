/* app.js — bootstrap, hero countdown, fetch orchestration, drivers, methods.
 *
 * Every figure on the page comes from the API. Nothing numeric is hardcoded here.
 * Panels flip between [data-state="loading" | "error" | "ok"]; if an endpoint
 * fails the panel says which one and why.
 */

import {
  api, MOCK, REDUCED_MOTION, describeError,
  $, $$, el, panel, setPanelState, withPanel,
  fmtDate, fmtInt, fmtNum, fmtSig, pct, parseISO, animateNumber,
  ensoLabel, titleCase, clamp, COLOR, unitLabel,
  horizonOf, exhaustionYearText, neverText, numberWord,
} from './util.js';
import { initMap, setMapYear } from './map.js';
import { initHistoryCharts } from './charts.js';
import { initSim } from './sim.js';
import { EmberBed } from './fire.js';

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
};

const caveatRegistry = new Map();   // label -> string[]
let provPayload = null;             // /api/provenance response

const TODAY = new Date().toISOString().slice(0, 10);

// The API's own default. Fuel exhaustion sits around 2264, so a shorter horizon
// truncates below the answer and the endpoint correctly returns p50: null.
const DEFAULT_HORIZON_YEARS = 300;

/* ------------------------------------------------------------ status chip */

function setStatus(kind, text) {
  const dot = $('#api-status .status__dot');
  const label = $('#api-status .status__text');
  if (dot) dot.dataset.status = kind;
  if (label) label.textContent = text;
}

/* ------------------------------------------------------------------- hero */

let countdownTimer = null;
let emberBed = null;

/** Lazily create the hero's ember bed; it needs the card to have a size. */
function ensureEmberBed() {
  if (emberBed) return emberBed;
  const card = $('.hero__card[data-panel="season"]') || $('.hero__card');
  if (!card) return null;
  try {
    emberBed = new EmberBed(card);
  } catch (err) {
    // A missing canvas context must never cost anyone the answer.
    console.warn('ember bed unavailable:', err);
    emberBed = null;
  }
  return emberBed;
}

function renderHero(season) {
  const days = season.days_remaining || {};
  const ends = season.end_date || {};

  if (season.active === false) {
    $('#hero-days').textContent = '0';
    $('.hero__unit').textContent = 'the season is not running';
    $('#hero-interval').textContent = '—';
    $('#hero-enddate').textContent = '—';
    $('#hero-confidence').textContent = season.confidence || '—';
    $('#hero-reason').textContent =
      'No burn season is currently active in Kalimantan. It will be back; it always is.';
  } else {
    animateNumber($('#hero-days'), days.p50, { from: 0, format: fmtInt });
    $('#hero-interval').textContent =
      days.p10 != null && days.p90 != null ? `${fmtInt(days.p10)}–${fmtInt(days.p90)} days` : '—';
    $('#hero-enddate').textContent = fmtDate(ends.p50);
    $('#hero-confidence').textContent = season.confidence || 'unstated';
    $('#hero-reason').textContent = reasonLine(season);
  }

  renderFlags(season);
  // The fire's intensity is the model's KBDI reading, not a mood setting.
  const bed = ensureEmberBed();
  if (bed) bed.setIntensity(season);
  renderSurvival(season.daily_end_probability || []);
  startCountdown(ends.p50);

  $('#hero-asof').textContent = fmtDate(season.as_of || TODAY);
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

function renderFlags(season) {
  const box = $('#hero-flags');
  if (!box) return;
  box.textContent = '';
  const flags = [];
  if (MOCK) flags.push(['tag--warn', 'mock fixture data']);
  flags.push(season.live_weather
    ? ['tag--ok', 'live weather feed']
    : ['tag--warn', 'no live weather — climatology fallback']);
  if (season.scenario) flags.push(['', `scenario: ${titleCase(season.scenario)}`]);
  if (season.region) flags.push(['', `region: ${titleCase(season.region)}`]);
  for (const [cls, text] of flags) {
    box.append(el('span', { class: `tag ${cls}`.trim(), text }));
  }
}

/** Inline SVG sparkline of P(still burning). No chart library needed. */
function renderSurvival(daily) {
  const holder = $('#survival-spark');
  if (!holder) return;
  holder.textContent = '';
  if (!daily.length) {
    holder.append(el('p', { class: 'control__hint', text: 'no daily curve returned' }));
    return;
  }
  const W = 320, H = 62;
  const pts = daily.map((d, i) => {
    const x = (i / Math.max(1, daily.length - 1)) * W;
    const y = H - clamp(Number(d.p_still_burning), 0, 1) * (H - 4) - 2;
    return [x, y];
  });
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('');
  const area = `${line}L${W},${H}L0,${H}Z`;

  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img');
  const last = daily[daily.length - 1];
  svg.setAttribute('aria-label',
    `Probability the season is still burning falls from 100% today to ${pct(Number(last.p_still_burning))} by ${fmtDate(last.date)}.`);

  const defs = document.createElementNS(ns, 'defs');
  defs.innerHTML =
    `<linearGradient id="sparkfill" x1="0" y1="0" x2="0" y2="1">
       <stop offset="0%" stop-color="${COLOR.ember}" stop-opacity=".45"/>
       <stop offset="100%" stop-color="${COLOR.ember}" stop-opacity="0"/>
     </linearGradient>`;
  svg.append(defs);

  const fill = document.createElementNS(ns, 'path');
  fill.setAttribute('d', area);
  fill.setAttribute('fill', 'url(#sparkfill)');
  svg.append(fill);

  const stroke = document.createElementNS(ns, 'path');
  stroke.setAttribute('d', line);
  stroke.setAttribute('fill', 'none');
  stroke.setAttribute('stroke', COLOR.ember);
  stroke.setAttribute('stroke-width', '1.6');
  stroke.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.append(stroke);

  holder.append(svg);
}

function startCountdown(endIso) {
  const box = $('#hero-countdown');
  if (!box) return;
  clearInterval(countdownTimer);
  const target = parseISO(endIso);
  if (!target) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const cells = {
    days: $('[data-unit="days"]', box),
    hours: $('[data-unit="hours"]', box),
    minutes: $('[data-unit="minutes"]', box),
    seconds: $('[data-unit="seconds"]', box),
  };
  const tick = () => {
    let ms = target.getTime() - Date.now();
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
  countdownTimer = setInterval(tick, 1000);
}

/* ------------------------------------------------------- the second answer */

function renderExhaustionHero(ex) {
  const yr = ex.exhaustion_year || {};
  const yearNode = $('#grim-year');
  const answer = exhaustionYearText(ex);
  const { years, lastYear } = horizonOf(ex);

  yearNode.textContent = '';
  if (answer.reached) {
    yearNode.classList.remove('grim__year--text');
    const num = el('span');
    yearNode.append(el('span', { class: 'grim__approx', 'aria-hidden': 'true', text: '≈ ' }), num);
    animateNumber(num, answer.value, {
      from: new Date().getFullYear(), format: (v) => String(Math.round(v)),
    });
  } else {
    yearNode.classList.add('grim__year--text');
    yearNode.textContent = answer.text;
  }

  $('#grim-interval').textContent =
    yr.p10 != null && yr.p90 != null ? `${yr.p10}–${yr.p90}` : 'not reached on this horizon';
  $('#grim-never').textContent = neverText(ex);
  $('#grim-cum').textContent = ex.cumulative_burned_mha_by_2050 != null
    ? fmtNum(ex.cumulative_burned_mha_by_2050, 2) : '—';

  const note = $('#grim-note');
  note.textContent = '';
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

function renderToll(history) {
  const seasons = history.seasons || [];
  const withDeaths = seasons.filter((s) => s.excess_deaths_est != null);
  const worst = withDeaths.sort((a, b) => b.excess_deaths_est - a.excess_deaths_est)[0];
  const node = $('#toll-text');
  if (!node) return;
  node.textContent = '';
  if (!worst) {
    node.textContent = 'The API did not report mortality estimates for any season on record.';
    return;
  }
  const bits = [
    document.createTextNode('The worst season on this record is '),
    el('b', { text: String(worst.year) }),
    document.createTextNode('. It burned '),
    el('b', { text: `${fmtNum(worst.burned_area_mha_kalimantan, 2)} Mha` }),
    document.createTextNode(' in Kalimantan and is associated with an estimated '),
    el('b', { text: fmtInt(worst.excess_deaths_est) }),
    document.createTextNode(' excess deaths across the region'),
  ];
  if (worst.economic_loss_usd_bn != null) {
    bits.push(document.createTextNode(', and about '));
    bits.push(el('b', { text: `US$${fmtNum(worst.economic_loss_usd_bn, 1)} bn` }));
    bits.push(document.createTextNode(' in economic losses'));
  }
  bits.push(document.createTextNode('. Peak PM2.5 in '));
  const pm = worst.pm25_peak_ugm3 || {};
  // whichever receptor the API reports highest for that season
  const city = Object.keys(pm).sort((a, b) => (pm[b] || 0) - (pm[a] || 0))[0];
  if (city) {
    bits.push(el('b', { text: city }));
    bits.push(document.createTextNode(' reached '));
    bits.push(el('b', { text: `${fmtInt(pm[city])} µg/m³` }));
    bits.push(document.createTextNode('. The WHO 24-hour guideline is 15.'));
  } else {
    bits.pop();
  }
  for (const b of bits) node.append(b);
}

function renderHistoryStats(history) {
  const box = $('#history-stats');
  if (!box) return;
  box.textContent = '';
  const s = history.summary || {};
  const seasons = history.seasons || [];
  const el_nino = seasons.filter((x) => x.enso_phase === 'el_nino');
  const la_nina = seasons.filter((x) => x.enso_phase === 'la_nina');
  const meanDur = (arr) => arr.length
    ? Math.round(arr.reduce((a, x) => a + (x.duration_days || 0), 0) / arr.length) : null;

  const stats = [
    ['Seasons on record', s.n_seasons != null ? fmtInt(s.n_seasons) : fmtInt(seasons.length), ''],
    ['Mean season length',
      `${fmtInt(s.mean_duration_days != null ? s.mean_duration_days : meanDur(seasons))}`, 'days'],
    ['Mean, El Niño years', `${fmtInt(s.mean_duration_el_nino != null ? s.mean_duration_el_nino : meanDur(el_nino))}`, 'days'],
    ['Mean, La Niña years', `${fmtInt(s.mean_duration_la_nina != null ? s.mean_duration_la_nina : meanDur(la_nina))}`, 'days'],
    ['Total burned, Kalimantan',
      fmtNum(s.total_burned_mha_kalimantan != null ? s.total_burned_mha_kalimantan
        : seasons.reduce((a, x) => a + (x.burned_area_mha_kalimantan || 0), 0), 2), 'Mha since ' + (s.first_year || seasons[0]?.year || '')],
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
  const drivers = (season.drivers || []).slice()
    .sort((a, b) => (b.contribution || 0) - (a.contribution || 0));
  if (!drivers.length) {
    setPanelState(p, 'error', 'The season model returned no driver breakdown for this run.');
    return;
  }
  const max = Math.max(...drivers.map((d) => d.contribution || 0), 0.0001);
  list.textContent = '';
  for (const d of drivers) {
    const dir = (d.direction || '').toLowerCase();
    const li = el('li', { class: `driver driver--${dir === 'shortens' ? 'shortens' : 'prolongs'}` }, [
      el('div', {}, [
        el('span', { class: 'driver__name', text: d.name || 'unnamed driver' }),
        el('span', { class: 'driver__dir', text: dir || 'unstated' }),
      ]),
      el('span', {
        class: 'driver__value',
        text: `${fmtSig(d.value)}${unitLabel(d.unit) ? ' ' + unitLabel(d.unit) : ''} · ${pct(d.contribution, 0)}`,
      }),
      el('span', { class: 'driver__bar' }, [el('i', { class: 'driver__fill' })]),
      el('p', { class: 'driver__explain', text: d.explain || 'No explanation supplied for this driver.' }),
    ]);
    list.append(li);
    const fill = $('.driver__fill', li);
    const w = `${((d.contribution || 0) / max) * 100}%`;
    if (REDUCED_MOTION) fill.style.width = w;
    else requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = w; }));
  }
  const total = drivers.reduce((a, d) => a + (d.contribution || 0), 0);
  // the season response echoes the resolved ONI at the top level
  const oni = season.oni != null
    ? season.oni
    : (drivers.find((d) => d.id === 'oni') || {}).value;
  $('#drivers-scope').textContent =
    `${scopeLabel} · scenario “${titleCase(season.scenario || 'baseline')}”, ` +
    `ONI ${fmtSig(oni)} · shares sum to ${fmtNum(total, 2)}.`;
  setPanelState(p, 'ok');
}

/* --------------------------------------------------- caveats & provenance */

export function registerCaveats(label, caveats) {
  if (!Array.isArray(caveats) || !caveats.length) return;
  // the playground re-runs the same models; don't print an identical list twice
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

  const note = $('#prov-note');
  note.textContent = provPayload.note
    || 'Status is reported by the API for each dataset it serves.';

  // status counts, so the balance of the table is visible before reading it
  const counts = new Map();
  for (const r of rows) counts.set(r.status, (counts.get(r.status) || 0) + 1);
  const countBox = $('#prov-counts');
  countBox.textContent = '';
  countBox.append(el('span', { class: 'prov-counts__total', text: `${fmtInt(rows.length)} datasets:` }));
  for (const [status, n] of [...counts].sort((a, b) => b[1] - a[1])) {
    countBox.append(el('span', {
      class: `tag ${STATUS_TAG[status] || ''}`.trim(),
      text: `${fmtInt(n)} ${status}`,
    }));
  }

  // the synthesised rows are the ones a reader must not mistake for observations
  const synth = rows.filter((r) => r.status === 'synthesised');
  const synthBox = $('#prov-synth');
  synthBox.textContent = '';
  for (const r of synth) {
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
        ? el('a', { href: r.url, rel: 'noopener noreferrer', target: '_blank', text: r.source })
        : document.createTextNode(r.source || 'no source string returned'),
    ]);
    if (r.notes) {
      sourceCell.append(el('p', { class: 'prov-table__notes', text: r.notes }));
    }
    tbody.append(el('tr', { class: r.status === 'synthesised' ? 'prov-row--synth' : null }, [
      el('td', {}, [
        el('b', { text: r.label || r.id }),
        r.coverage ? el('small', { class: 'prov-table__cov', text: r.coverage }) : null,
      ]),
      sourceCell,
      el('td', {}, [
        el('span', {
          class: `tag ${STATUS_TAG[r.status] || ''}`.trim(),
          text: r.status || 'unstated',
        }),
      ]),
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
        body: { as_of: TODAY, region: 'kalimantan', n_sims: 5000, scenario: 'baseline', seed: 7 },
      });
      store.season = season;
      renderHero(season);
      renderDrivers(season, 'Baseline run');
      registerCaveats('Season model — /api/simulate/season', season.caveats);
      return season;
    });
  } catch (e) {
    // the drivers panel is fed by this same response
    setPanelState(panel('drivers'), 'error', describeError(e, 'season model'));
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
      // caveats are a sibling of summary; older builds nested them inside it
      const caveats = Array.isArray(h.caveats) ? h.caveats
        : (h.summary && h.summary.caveats) || [];
      registerCaveats('Historical record — /api/history', caveats);
      return h;
    });
  } catch (e) {
    // the toll line lives in the hero but depends on history
    setPanelState(panel('toll'), 'error', describeError(e, 'history'));
    throw e;
  }
  try {
    renderToll(history);
    setPanelState(panel('toll'), 'ok');
  } catch (e) {
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
    const m = await api(`/api/meteo?lat=-2.2&lon=113.9&start=${TODAY}&end=${end}`);
    store.meteo = m;
  } catch (e) {
    console.warn('meteo unavailable, plume falls back to slider defaults', e);
    store.meteo = null;
  }
}

/* ------------------------------------------------------------------- boot */

function wireRetries() {
  const map = {
    season: loadSeason,
    exhaustion: loadExhaustion,
    history: loadHistory,
    map: loadMapData,
    provenance: loadProvenance,
  };
  for (const btn of $$('[data-retry]')) {
    btn.addEventListener('click', () => {
      const fn = map[btn.dataset.retry];
      if (fn) fn().catch(() => { /* panel already shows the error */ });
    });
  }
}

function wireCrossLinks() {
  // charts.js dispatches this when a year's bar is clicked
  document.addEventListener('wwhe:select-year', (e) => {
    setMapYear(e.detail.year, { scroll: true });
  });
  // sim.js dispatches its season result so the drivers panel follows it
  document.addEventListener('wwhe:sim-season', (e) => {
    renderDrivers(e.detail.season, 'Playground run');
  });
  document.addEventListener('wwhe:caveats', (e) => {
    registerCaveats(e.detail.label, e.detail.caveats);
  });
}

async function boot() {
  if (MOCK) {
    const banner = $('#mock-banner');
    if (banner) banner.hidden = false;
    setStatus('mock', 'mock fixtures');
  } else {
    setStatus('pending', 'connecting…');
  }

  if (window.Chart) {
    Chart.defaults.color = COLOR.ink3;
    Chart.defaults.borderColor = COLOR.line;
    Chart.defaults.font.family =
      getComputedStyle(document.body).fontFamily || 'system-ui, sans-serif';
    Chart.defaults.font.size = 11;
    if (REDUCED_MOTION) Chart.defaults.animation = false;
  }

  $('#hero-asof').textContent = fmtDate(TODAY);

  wireRetries();
  wireCrossLinks();

  // health first, but never block the rest of the page on it
  api('/api/health').then((h) => {
    store.health = h;
    if (!MOCK) {
      setStatus('ok', `api ok${h.version ? ' · ' + h.version : ''}${h.live_weather_available ? ' · live wx' : ' · no live wx'}`);
    }
  }).catch((e) => {
    if (!MOCK) setStatus('error', 'api unreachable');
    console.warn('health check failed', e);
  });

  const quiet = (p) => p.catch(() => null);

  await quiet(loadMeteo());

  // history first: the map's year slider needs the list of seasons
  await quiet(loadHistory());

  await Promise.all([
    quiet(loadSeason()),
    quiet(loadExhaustion()),
    quiet(loadMapData()),
    quiet(loadProvenance()),
  ]);

  initSim({ fuel: store.fuel, season: store.season, exhaustion: store.exhaustion, today: TODAY });

  renderCaveats();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
