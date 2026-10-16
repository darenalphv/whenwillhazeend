/**
 * sim3d.js — the simulator page: panels, timeline and controls around the
 * shared 3D scene (./sim3d/scene.js).
 *
 * Every number on this page comes from the model's response: receptor values
 * from receptor_series (exact at each model frame, linearly interpolated
 * between frames during playback), field peak and mixing height from frames[].
 * The particles are drawn, never counted.
 */

import { Scene } from './sim3d/scene.js';
import { BANDS, bandColors, bandIndex, bandOf } from './sim3d/colors.js';
import { VERSIONS } from './sim3d/libs.js';
import { loadWeek, readingAt } from './sim3d/now.js';

const API = '/api/simulate/haze3d';
const $ = (s, r = document) => r.querySelector(s);
const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const compass = (d) => COMPASS[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];
const fmt = (v) => Math.round(v).toLocaleString('en-GB');
const MY_SG = new Set(['Malaysia', 'Singapore']);
const EMPH = ['Kuching', 'Sibu', 'Miri', 'Kuala Lumpur', 'Singapore', 'Batam'];
const NO_SUMATRA = new Set(['Singapore', 'Kuala Lumpur', 'Batam', 'Jambi', 'Pekanbaru']);

const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const small = () => window.innerWidth < 720 || window.matchMedia('(pointer: coarse)').matches;

const state = { scene: null, data: null, seasons: [], running: false, view: 'home', scrubbing: false, mode: 'model', week: null, lastRaw: null };
const FIXTURE = { week: true, 'week-nofield': 'nofield' }[new URLSearchParams(location.search).get('fixture')] || false;
const TZ = 'Asia/Kuala_Lumpur';   // MYT and SGT are both UTC+8

/* ------------------------------------------------------------ status */

function setStatus(kind, text) {
  $('#sim-status .sbar__statusdot').dataset.status = kind;
  $('#sim-status .sbar__statustext').textContent = text;
}

function showError(title, msg, what) {
  $('#stage-loading').hidden = true;
  $('#stage-error-title').textContent = title;
  $('#stage-error-msg').textContent = msg;
  $('#stage-error-what').textContent = what ? `What failed: ${what}` : '';
  $('#stage-error').hidden = false;
}

function notice(text) {
  const n = $('#stage-notice');
  if (!text) { n.hidden = true; return; }
  n.textContent = text;
  n.hidden = false;
}

/* ----------------------------------------------------------- readout */

function clockAt(t) {
  const f0 = state.data.frames[0];
  const abs = (f0.hour_of_day ?? 0) + t;
  const hh = Math.floor(((abs % 24) + 24) % 24);
  const mm = Math.floor((abs - Math.floor(abs)) * 60);
  let date = '';
  if (f0.time) {
    const base = new Date(`${f0.time.slice(0, 10)}T00:00:00Z`);
    base.setUTCHours(Math.floor(abs));
    date = base.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
  }
  return { text: `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`, date, day: Math.floor(abs / 24) + 1 };
}

function weekClock(t) {
  const d = new Date(state.week.t0 + t * 3.6e6);
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: TZ, hour12: false });
  const date = d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: TZ });
  return { time, date, ms: d.getTime() };
}

function onFrameWeek(s) {
  const f = state.scene.field;
  const { t } = s;
  const c = weekClock(t);
  const [k0, k1, a] = s.bracket;
  const exact = a < 0.005 || a > 0.995 || k0 === k1;
  $('#ro-time').textContent = c.time;
  $('#ro-date').textContent = `${c.date} · MYT/SGT`;
  const fr = state.week.hasField ? 'CAMS frame' : '3-hour step';
  $('#ro-sub').textContent = `${exact ? `${fr} ${a > 0.5 ? k1 : k0}` : `between ${fr}s ${k0} and ${k1}`}`;
  const w = state.week;
  $('#ro-mix').textContent = `${fmt(state.scene.fires.active || 0)} bins lit · measured`;
  $('#ro-max').textContent = w.hasField ? `${fmt(f.scalarAt(t, 'max_pm25'))} µg/m³` : 'not available';
  $('#ro-note').textContent = w.hasField
    ? 'Haze: CAMS model (modelled). Fires: NASA FIRMS detections (measured), lit on the days they were seen.'
    : `No haze is drawn: ${w.fieldReason || 'the haze field is unavailable.'} That is missing data, not clean air. Fires: NASA FIRMS detections (measured), lit on the days they were seen.`;
  if (!state.scrubbing) $('#scrub').value = String(t);
  $('#tl-clock').textContent = `${c.date} ${c.time}`;
  setPlayIcon(s.playing);
  renderPlaces(c.ms);
  const fps = s.fps ? Math.round(Math.min(s.fps, 240)) : 0;
  $('#perf').textContent = reduced ? 'Still frame (reduced motion)' : `No drifting smoke in this mode — no wind field to carry it · ${fps} fps`;
}

function onFrame(s) {
  // the scene may still hold the other mode's data mid-switch
  if (state.mode !== state.scene?.mode) return;
  if (state.mode === 'week') { if (state.week) onFrameWeek(s); return; }
  const d = state.data;
  if (!d) return;
  const f = state.scene.field;
  const { t } = s;
  const c = clockAt(t);
  const [k0, k1, a] = s.bracket;
  const exact = a < 0.005 || a > 0.995 || k0 === k1;
  const kNear = a > 0.5 ? k1 : k0;
  const fr = d.frames[kNear];

  $('#ro-time').textContent = c.text;
  $('#ro-date').textContent = c.date ? `${c.date} · day ${c.day}` : `day ${c.day}`;
  $('#ro-sub').textContent = `+${t.toFixed(1)} h · ${exact ? `model frame ${kNear}` : `between frames ${k0} and ${k1}`}`;
  $('#ro-mix').textContent = `${fmt(f.scalarAt(t, 'mixing_height_m'))} m`;
  $('#ro-max').textContent = `${fmt(f.scalarAt(t, 'max_pm25'))} µg/m³`;
  $('#ro-wind').textContent = `${fr.wind_speed_ms} m/s from ${Math.round(fr.wind_dir_deg)}° ${compass(fr.wind_dir_deg)}`;
  updateArrow(fr.wind_dir_deg);
  const H = f.scalarAt(t, 'mixing_height_m');
  $('#ro-note').textContent = d.conditions.diurnal
    ? (H < d.conditions.mean_mixing_height_m
      ? 'The layer has collapsed. Same fires, less air to dilute into.'
      : 'Convection has lifted the layer. Same fires, more air.')
    : 'Diurnal cycle off — the mixing height is held flat.';

  if (!state.scrubbing) $('#scrub').value = String(t);
  $('#tl-clock').textContent = `${c.text} · +${t.toFixed(1)} h`;
  setPlayIcon(s.playing);
  renderReceptors(t);
  updateArrivalStates(t);

  const fps = s.fps ? Math.round(Math.min(s.fps, 240)) : 0;
  $('#perf').textContent = reduced
    ? `${fmt(s.particles)} particles · still frame (reduced motion)`
    : `${fmt(s.particles)} particles (${fmt(s.visible)} in smoke) · ${fps} fps`;
}

/** Arrow points where the wind blows TO, in screen space (map may be rotated). */
function updateArrow(dirFrom) {
  const b = state.scene?.map?.getBearing() || 0;
  const rot = ((dirFrom + 180 - b) % 360 + 360) % 360;
  $('#ro-arrow').style.transform = `rotate(${rot}deg)`;
  $('#ro-arrow').dataset.dir = String(dirFrom);
}

function setPlayIcon(playing) {
  const b = $('#play-btn');
  if (b.dataset.playing === String(playing)) return;
  b.dataset.playing = String(playing);
  $('#play-icon').setAttribute('d', playing ? 'M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z' : 'M7 4.5v15l12-7.5z');
  b.setAttribute('aria-label', playing ? 'Pause' : 'Play');
}

/* --------------------------------------------------------- receptors */

function receptorRow(r) {
  const li = document.createElement('li');
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'rbtn';
  b.innerHTML = '<i class="r__dot"></i><span class="r__name"></span><b class="r__v"></b><span class="r__cat"></span>';
  b.addEventListener('click', () => flyToCity(r));
  li.appendChild(b);
  li._els = { dot: b.querySelector('.r__dot'), name: b.querySelector('.r__name'), v: b.querySelector('.r__v'), cat: b.querySelector('.r__cat') };
  li._els.name.textContent = r.name;
  li._r = r;
  return li;
}

function buildReceptorLists() {
  const rec = state.data.receptor_series || [];
  const my = $('#receptor-my');
  const id = $('#receptor-id');
  my.textContent = '';
  id.textContent = '';
  const order = (a, b) => {
    const ia = EMPH.indexOf(a.name);
    const ib = EMPH.indexOf(b.name);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  };
  for (const r of rec.filter((x) => MY_SG.has(x.country)).sort(order)) my.appendChild(receptorRow(r));
  for (const r of rec.filter((x) => !MY_SG.has(x.country)).sort(order)) id.appendChild(receptorRow(r));
  my.classList.add('rlist--em');
  $('#receptor-block').hidden = rec.length === 0;
}

function renderReceptors(t) {
  const rgb = bandColors();
  const sumatra = state.data.sources.some((s) => s.region === 'sumatra');
  for (const li of document.querySelectorAll('#receptor-my li, #receptor-id li')) {
    const v = state.scene.receptorAt(li._r, t);
    const rv = Math.round(v);
    if (li._last === rv) continue;
    li._last = rv;
    const c = rgb[bandIndex(v)];
    li._els.dot.style.background = `rgb(${c.join(',')})`;
    li._els.v.innerHTML = `${fmt(v)} <small>µg/m³ PM2.5 · modelled</small>`;
    li._els.cat.textContent = `${bandOf(v).name}${!sumatra && NO_SUMATRA.has(li._r.name) ? ' · Sumatran fires not modelled' : ''}`;
  }
}

function flyToCity(r) {
  const m = state.scene.map;
  const opts = { center: [r.lon, r.lat], zoom: 7.3, pitch: 58, bearing: m.getBearing() };
  if (reduced) m.jumpTo(opts); else m.flyTo({ ...opts, duration: 2200 });
  setView(NO_SUMATRA.has(r.name) ? 'strait-city' : null);
}

/* ---------------------------------------------------------- arrivals */

/*
 * The model's arrival time is along-wind distance / wind speed and ignores
 * how far off the plume axis a city is, so it can promise "+2 h" to a city
 * the smoke never reaches. Only show an arrival when the city's modelled
 * concentration actually rises above background during the run.
 */
const ARRIVAL_MIN_EXCESS = 5;   // µg/m³ above background
function arrives(r) {
  const bg = state.data?.scale?.background_pm25 ?? 12;
  return r.downwind && r.arrival_hours != null && (r.peak_pm25 ?? 0) > bg + ARRIVAL_MIN_EXCESS;
}

function renderArrivals() {
  const d = state.data;
  const ul = $('#arrival-list');
  ul.textContent = '';
  const downwind = (d.receptor_series || [])
    .filter(arrives)
    .sort((a, b) => {
      const am = MY_SG.has(a.country) ? 0 : 1;
      const bm = MY_SG.has(b.country) ? 0 : 1;
      return am - bm || a.arrival_hours - b.arrival_hours;
    });
  $('#arrival-block').hidden = false;
  if (!downwind.length) {
    $('#arrival-note').textContent = 'On this wind no city\'s modelled PM2.5 rises above background during the run. Turn the wind and the same smoke goes somewhere else entirely.';
    return;
  }
  const runH = d.conditions.hours;
  for (const r of downwind.slice(0, 10)) {
    const li = document.createElement('li');
    li.className = 'arrival';
    const within = r.arrival_hours <= runH;
    li.innerHTML = '<span class="arrival__name"></span><span class="arrival__t"></span><span class="arrival__peak"></span>';
    const nm = li.querySelector('.arrival__name');
    nm.textContent = `${r.name} `;
    const sm = document.createElement('small');
    sm.textContent = r.country || '';
    nm.appendChild(sm);
    li.querySelector('.arrival__t').textContent = within ? `+${r.arrival_hours.toFixed(1)} h` : `+${Math.round(r.arrival_hours)} h · past this run`;
    li.querySelector('.arrival__peak').textContent = `${fmt(r.distance_km)} km from the fires · run peak ${fmt(r.peak_pm25)} µg/m³, ${(r.peak_category || bandOf(r.peak_pm25).name).toLowerCase()}`;
    if (!within) li.classList.add('arrival--beyond');
    li._r = r;
    ul.appendChild(li);
  }
  $('#arrival-note').textContent = d.conditions.release_from_start
    ? 'Release mode: the front leaves the fires at hour 0 and travels at the wind speed. Each badge flips as the timeline passes that arrival.'
    : 'Times are how long smoke takes to cover the distance. The field itself is steady state — use release mode to watch the front move.';
}

function updateArrivalStates(t) {
  if (!state.data.conditions.release_from_start) return;
  for (const li of document.querySelectorAll('#arrival-list li')) {
    if (li._r.arrival_hours > state.data.conditions.hours) continue;
    const here = t >= li._r.arrival_hours;
    if (li._here === here) continue;
    li._here = here;
    let b = li.querySelector('.arrival__state');
    if (!b) {
      b = document.createElement('span');
      li.querySelector('.arrival__name').appendChild(b);
    }
    b.className = `arrival__state arrival__state--${here ? 'here' : 'coming'}`;
    b.textContent = here ? 'arrived' : 'on its way';
  }
}

/* ------------------------------------------------- legend, validation */

function renderLegend() {
  const ul = $('#legend-scale');
  ul.textContent = '';
  const rgb = bandColors();
  BANDS.forEach((b, i) => {
    const li = document.createElement('li');
    const lo = i === 0 ? 0 : Math.ceil(BANDS[i - 1].max);
    li.innerHTML = `<i style="background:rgb(${rgb[i].join(',')})"></i><span></span><span></span>`;
    li.children[1].textContent = b.name;
    li.children[2].textContent = b.max === Infinity ? '251+' : `${lo}–${Math.floor(b.max)}`;
    ul.appendChild(li);
  });
  $('#legend').hidden = false;
  $('#legend-sumatra').hidden = state.mode === 'week' || !state.data.sources.some((s) => s.region === 'sumatra');
}

function renderValidation() {
  const v = state.data.validation;
  const body = $('#validation-body');
  body.textContent = '';
  $('#validation-block').hidden = false;
  if (!v || !v.rows || !v.rows.length) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = 'This year has no reported station peak in the dataset, so this run has not been checked against anything. That is not the same as it being right.';
    body.appendChild(p);
    return;
  }
  for (const row of v.rows) {
    const p = document.createElement('p');
    p.className = 'vrow';
    p.innerHTML = '<span></span>: modelled <b></b> vs reported <b></b> µg/m³<span class="ratio"></span>';
    p.children[0].textContent = row.receptor;
    p.children[1].textContent = fmt(row.modelled_peak_ugm3);
    p.children[2].textContent = fmt(row.reported_peak_ugm3);
    p.querySelector('.ratio').textContent = row.ratio ? `This run is ${row.ratio}× the reported peak.` : '';
    body.appendChild(p);
  }
  const note = document.createElement('p');
  note.className = 'muted';
  note.textContent = v.note || '';
  body.appendChild(note);
}

function renderProvenance() {
  const d = state.data;
  const a = d.adapted;
  const ul = $('#provenance');
  ul.textContent = '';
  const add = (text, filled) => {
    const li = document.createElement('li');
    li.textContent = text;
    if (filled) li.className = 'filled';
    ul.appendChild(li);
  };
  add(`Concentrations, city values, peaks and arrival times: the model's (haze3d response v${d.version}).`, false);
  add(a.wind
    ? 'Wind field: uniform over the domain, built on this page from the run\'s wind speed and its hourly direction. The model has not published a gridded wind field for this run.'
    : `Wind field: the model's, ${d.wind.nx}×${d.wind.ny} at ${d.wind.levels_m.join(' / ')} m.`, a.wind);
  add(a.emission
    ? 'Particle release rates: each cluster\'s PM2.5 spread evenly over a 14-day episode.'
    : 'Particle release rates: the model\'s emission rate per cluster.', a.emission);
  add(a.injection
    ? 'Injection heights: 0–500 m for peat, 0–1,500 m for other fuels (contract defaults).'
    : 'Injection heights: the model\'s, per cluster.', a.injection);
  if (a.flaming) add('Flame versus smoulder appearance: assumed from fuel type. It changes how a fire looks, not any number.', true);
  add(d.hasAloft
    ? 'Elevated smoke layer: the model\'s, drawn above the mixing layer.'
    : 'No elevated layer in this response, so all smoke is kept under the mixing height.', !d.hasAloft);
  add('Particles are drawn, not counted. No number on this page comes from them; their shade is the model\'s local value.', false);
  $('#provenance-block').hidden = false;
}

function renderCaveats() {
  const ul = $('#sim-caveats');
  ul.textContent = '';
  for (const c of state.data.caveats || []) {
    const li = document.createElement('li');
    li.textContent = c;
    ul.appendChild(li);
  }
}

function renderSumatra() {
  const n = state.data.sources.filter((s) => s.region === 'sumatra').length;
  const ex = $('#sumatra-extra');
  if (n) {
    ex.textContent = `This run does include ${n} Sumatran fire${n > 1 ? 's' : ''}, ringed in blue on the map. Check the model's caveats for how complete that set is.`;
    ex.hidden = false;
  } else ex.hidden = true;
}

function renderTicks() {
  const d = state.data;
  const tEnd = state.scene.field.tEnd || 1;
  const box = $('#ticks');
  box.textContent = '';
  const hod0 = d.frames[0].hour_of_day ?? 0;
  for (const f of d.frames) {
    const isDay = Math.round((hod0 + f.hour) % 24) === 0;
    if (!isDay && d.frames.length > 30 && f.index % 3) continue;
    const i = document.createElement('i');
    i.className = isDay ? 'day' : '';
    i.style.left = `${(f.hour / tEnd) * 100}%`;
    box.appendChild(i);
  }
  if (d.conditions.release_from_start) {
    for (const r of d.receptor_series || []) {
      if (!EMPH.includes(r.name) || !arrives(r) || r.arrival_hours > tEnd) continue;
      const i = document.createElement('i');
      i.className = 'arr';
      i.style.left = `${(r.arrival_hours / tEnd) * 100}%`;
      box.appendChild(i);
    }
  }
}

/* ------------------------------------------------------------ views */

function setView(name) {
  state.view = name;
  for (const b of document.querySelectorAll('.vbtn')) b.setAttribute('aria-pressed', String(b.dataset.view === name));
  $('#sumatra-chip').hidden = state.mode !== 'model' || !(name === 'singapore' || name === 'home' || name === 'strait-city');
}

/* -------------------------------------------------------------- run */

function params() {
  const hours = Number($('#ctl-hours').value);
  return {
    year: Number($('#ctl-year').value),
    start_date: $('#ctl-date').value || null,
    hours,
    step_hours: hours > 48 ? 2 : 1,
    release_from_start: $('#ctl-release').checked,
    grid_n: Number($('#ctl-grid').value),
    wind_speed_ms: Number($('#ctl-wind-speed').value),
    wind_dir_deg: Number($('#ctl-wind-dir').value),
    wind_veer_deg_per_h: Number($('#ctl-veer').value),
    mixing_height_m: Number($('#ctl-mix').value),
    rain_mm: Number($('#ctl-rain').value),
    diurnal: $('#ctl-diurnal').checked,
  };
}

export async function fetchField(body) {
  let res;
  try {
    res = await fetch(API, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  } catch {
    throw Object.assign(new Error('The model API could not be reached.'), { what: `POST ${API}` });
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') detail = j.detail;
      else if (Array.isArray(j.detail) && j.detail[0]?.msg) detail = j.detail[0].msg;
    } catch { /* keep the status line */ }
    throw Object.assign(new Error(detail), { what: `POST ${API} (${res.status})` });
  }
  const field = await res.json();
  if (!field.grid || !field.frames?.length) {
    throw Object.assign(new Error(field.note || 'The model returned no field for those inputs.'), { what: 'model output' });
  }
  return field;
}

async function run() {
  if (state.running || !state.scene) return;
  state.running = true;
  state.scene.pause();
  $('#stage-error').hidden = true;
  $('#stage-loading').hidden = false;
  $('#stage-loading-text').textContent = 'Running the dispersion model…';
  setStatus('pending', 'running the model…');
  $('#run-btn').disabled = true;
  try {
    const raw = await fetchField(params());
    state.lastRaw = raw;
    applyMode('model');
    const data = state.scene.setData(raw);
    state.data = data;
    const f = state.scene.field;
    $('#scrub').max = String(f.tEnd);
    $('#scrub').step = 'any';
    for (const id of ['#readout', '#timeline', '#views']) $(id).hidden = false;
    renderLegend();
    buildReceptorLists();
    renderArrivals();
    renderValidation();
    renderProvenance();
    renderCaveats();
    renderSumatra();
    renderTicks();
    const c = data.conditions;
    $('#hint-active').textContent = `${data.sources_used} clusters burning on this date`
      + (data.sources_inactive_on_date ? `, ${data.sources_inactive_on_date} more elsewhere in the season` : '')
      + (data.sources_dropped ? ` · ${data.note}` : '')
      + ` · layer ${c.night_mixing_height_m}–${c.day_mixing_height_m} m`;
    const sp = c.hours >= 72 ? 4 : 2;     // a run plays through in ~12-25 s
    $('#speed').value = String(sp);
    state.scene.speed = sp;
    setStatus('ok', `${data.sources_used} fires · ${c.hours} h · ${c.release_from_start ? 'release' : 'steady state'}`);
    onFrame(state.scene.state());
    notice(state.scene.notices.length ? state.scene.notices.join(' ') : null);
    if (!reduced) state.scene.play();
  } catch (err) {
    showError('The model run failed.', String(err.message || err), err.what || 'the model API');
    setStatus('error', 'model failed');
  } finally {
    $('#stage-loading').hidden = true;
    $('#run-btn').disabled = false;
    state.running = false;
  }
}


/* -------------------------------------------------------- this week */

function applyMode(m) {
  state.mode = m;
  document.body.dataset.mode = m;
  for (const b of document.querySelectorAll('.mode')) b.setAttribute('aria-pressed', String(b.dataset.mode === m));
  const week = m === 'week';
  const noField = week && state.week?.available && !state.week.hasField;
  $('#legend-title').textContent = noField ? 'Fires this week · measured' : (week ? 'PM2.5, µg/m³ · modelled (CAMS)' : 'Surface PM2.5, µg/m³ · modelled');
  $('#legend-scale').hidden = noField;
  $('#legend-contour').hidden = noField;
  $('#legend-nofield').hidden = !noField;
  $('#legend-edge').hidden = noField;
  $('#mode-week-sub').textContent = noField ? 'Real fires · official readings' : 'Real fires · CAMS haze · official readings';
  $('#legend-fire').textContent = week ? 'Fire detected by satellite (NASA FIRMS, measured), lit on the days it was seen' : 'Fire, sized by emission';
  $('#legend-smoke').hidden = week;
  $('#legend-grid').textContent = week ? 'CAMS extract' : 'model grid';
  $('#ro-mix-dt').textContent = week ? 'Fires today' : 'Mixing layer';
  $('#ro-max-dt').textContent = noField ? 'Haze field' : 'Peak PM2.5 · modelled';
  $('#ro-wind-dt').parentElement.hidden = week;
  const attr = $('#legend-attr');
  attr.hidden = !week || !state.week?.available;
  if (week && state.week?.available) attr.textContent = state.week.attribution.join(' · ');
  $('#fixture-banner').hidden = !(week && state.week?.fixture);
  setView(state.view);
}

function placeRow(p) {
  const li = document.createElement('li');
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'rbtn';
  b.innerHTML = '<i class="r__dot"></i><span class="r__name"></span><b class="r__v"></b><span class="r__cat"></span>';
  b.querySelector('.r__name').textContent = p.name;
  b.addEventListener('click', () => flyToCity(p));
  li.appendChild(b);
  li._p = p;
  li._els = { dot: b.querySelector('.r__dot'), v: b.querySelector('.r__v'), cat: b.querySelector('.r__cat') };
  return li;
}

function renderPlaces(ms) {
  for (const li of document.querySelectorAll('#week-my li, #week-sg li')) {
    if (!li._p) continue;           // the APIMS link row
    const r = readingAt(li._p, ms);
    const key = r ? `${r.value}|${r.label}` : 'none';
    if (li._last === key) continue;
    li._last = key;
    if (!r || r.noReading) {
      li._els.v.innerHTML = '— <small>no public reading</small>';
      li._els.dot.style.background = 'transparent';
      li._els.dot.style.boxShadow = 'inset 0 0 0 2px var(--ink-4)';
      li._els.cat.textContent = r
        ? 'This site has no licensed feed for Malaysia\'s official API (IPU). No data here is not clean air.'
        : 'No reading for this hour.';
      continue;
    }
    li._els.dot.style.boxShadow = '';
    li._els.dot.style.background = getComputedStyle(document.documentElement).getPropertyValue(r.band.token).trim() || r.band.hex;
    li._els.v.innerHTML = '';
    li._els.v.append(`${fmt(r.value)} `);
    const sm = document.createElement('small');
    sm.textContent = `${r.label} · ${r.tag}`;
    li._els.v.appendChild(sm);
    li._els.cat.textContent = `${r.band.name} on the ${r.index === 'USAQI' ? 'US AQI' : r.index} scale${r.measured ? '' : ' · CAMS ~40 km grid, cannot separate nearby towns'}`;
  }
}

function renderWeekPanels() {
  const w = state.week;
  const ok = w && w.available;
  $('#week-unavailable').hidden = ok;
  if (!ok) {
    $('#week-unavailable-msg').textContent = `${w?.reason || "This week's haze data is not available right now."} The model run below still works.`;
  }
  // headline: generated by the API from the data; shown with its label
  const h = ok ? w.headline : null;
  $('#week-headline').hidden = !h;
  if (h) {
    $('#week-headline-text').textContent = h.text;
    $('#week-headline-meta').textContent = [h.index, h.measured ? 'measured' : 'modelled', h.t ? new Date(h.t).toLocaleString('en-GB', { timeZone: TZ, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : null].filter(Boolean).join(' · ');
  }
  const places = ok ? w.places : [];
  $('#week-places').hidden = !places.length;
  const my = $('#week-my');
  const sg = $('#week-sg');
  my.textContent = '';
  sg.textContent = '';
  for (const p of places) (p.country === 'SG' ? sg : my).appendChild(placeRow(p));
  const portal = ok ? (w.portals?.MY || places.find((p) => p.official_portal)?.official_portal) : null;
  if (portal && places.some((p) => p.country === 'MY' && !p.official && !p.modelled)) {
    const li = document.createElement('li');
    li.className = 'rlist__portal';
    const a = document.createElement('a');
    a.href = portal.url;
    a.rel = 'noopener';
    a.textContent = 'Official API (IPU) readings for KL and Selangor: APIMS, DOE Malaysia';
    li.appendChild(a);
    my.appendChild(li);
  }

  const hs = ok ? w.hotspots : null;
  $('#week-fires').hidden = !hs;
  if (hs) {
    const ul = $('#week-fires-list');
    ul.textContent = '';
    const names = { kalimantan: 'Kalimantan', sumatra: 'Sumatra', peninsular_malaysia: 'Peninsular Malaysia' };
    const counts = hs.count_by_region || {};
    const keys = Object.keys(counts).sort((a, b) => (counts[b] || 0) - (counts[a] || 0));
    for (const k of keys) {
      const li = document.createElement('li');
      const gw = hs.frp_gw_by_region?.[k];
      li.innerHTML = '<span></span><b></b>';
      li.children[0].textContent = names[k] || k.replace(/_/g, ' ');
      li.children[1].textContent = `${fmt(counts[k])} hotspots${gw != null ? ` · ${gw} GW FRP` : ''}`;
      ul.appendChild(li);
    }
    $('#week-fires-note').textContent = `${w.sources.length} aggregated fire bins on the map. Hotspots are satellite detections (measured); FRP is fire radiative power. ${hs.as_of ? `As of ${new Date(hs.as_of).toLocaleString('en-GB', { timeZone: TZ, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })} MYT.` : ''}`;
  }
  const attr = $('#week-attr');
  attr.textContent = '';
  for (const a of ok ? (w.attributionFull?.length ? w.attributionFull : w.attribution) : []) {
    const li = document.createElement('li');
    li.textContent = a;
    attr.appendChild(li);
  }
  $('#week-attr-block').hidden = !ok;
}

function renderWeekCaveats() {
  const ul = $('#sim-caveats');
  ul.textContent = '';
  const list = [
    state.week.hasField
      ? 'This mode draws no drifting smoke particles. The haze you see is the CAMS model\'s PM2.5 field, interpolated between its 3-hourly frames.'
      : `No haze is drawn in this mode. ${state.week.fieldReason || 'The haze field is unavailable.'} Its absence on the map is missing data, not clean air; the official readings in the panel are the measure of the air.`,
    'Fire markers are satellite detections binned for display. Their size follows fire radiative power; their column heights are illustrative.',
    ...(state.week.caveats || []),
  ];
  for (const c of list) {
    const li = document.createElement('li');
    li.textContent = c;
    ul.appendChild(li);
  }
}

function enterWeek() {
  const w = state.week;
  applyMode('week');
  renderWeekPanels();
  if (!w || !w.available) {
    setStatus('error', 'this week unavailable');
    return false;
  }
  state.scene.pause();
  state.scene.setWeek(w);
  state.data = state.scene.data;
  const t1 = state.scene.firstReadingT();
  if (t1 > 0) state.scene.seek(t1);
  const f = state.scene.field;
  $('#scrub').max = String(f.tEnd);
  $('#scrub').step = 'any';
  for (const id of ['#readout', '#timeline', '#views']) $(id).hidden = false;
  renderLegend();
  renderWeekCaveats();
  renderWeekTicks();
  $('#speed').value = '8';
  state.scene.speed = 8;
  setStatus('ok', `this week · ${w.sources.length} fire bins${w.hasField ? '' : ' · no haze field'}${w.fixture ? ' · FIXTURE' : ''}`);
  onFrame(state.scene.state());
  notice(state.scene.notices.length ? state.scene.notices.join(' ') : null);
  if (!reduced) state.scene.play();
  return true;
}

function renderWeekTicks() {
  const box = $('#ticks');
  box.textContent = '';
  const tEnd = state.scene.field.tEnd || 1;
  for (let h = 0; h <= tEnd; h += 3) {
    const d = new Date(state.week.t0 + h * 3.6e6);
    const local = (d.getUTCHours() + 8) % 24;
    if (local !== 0 && h % 12) continue;
    const i = document.createElement('i');
    i.className = local === 0 ? 'day' : '';
    i.style.left = `${(h / tEnd) * 100}%`;
    box.appendChild(i);
  }
}

/* ----------------------------------------------------------- wiring */

function bindOutputs() {
  const bind = (id, out, f) => {
    const el = $(id);
    const upd = () => { $(out).textContent = f(Number(el.value)); };
    el.addEventListener('input', upd);
    upd();
  };
  bind('#ctl-wind-speed', '#out-wind-speed', (v) => `${v.toFixed(1)} m/s`);
  bind('#ctl-wind-dir', '#out-wind-dir', (v) => `${v}° ${compass(v)}`);
  bind('#ctl-veer', '#out-veer', (v) => `${v > 0 ? '+' : ''}${v}°/h`);
  bind('#ctl-mix', '#out-mix', (v) => `${fmt(v)} m`);
  bind('#ctl-rain', '#out-rain', (v) => `${v} mm`);
  bind('#ctl-hours', '#out-hours', (v) => `${v} h`);
  bind('#ctl-vex', '#out-vex', (v) => `${v}×`);
  bind('#ctl-floor', '#out-floor', (v) => `${v} µg/m³`);
  bind('#ctl-grid', '#out-grid', (v) => `${v} × ${v}`);
}

function wire() {
  bindOutputs();
  $('#run-btn').addEventListener('click', run);
  $('#stage-retry').addEventListener('click', () => (state.scene ? run() : boot()));
  $('#play-btn').addEventListener('click', () => {
    const s = state.scene;
    if (!s?.field) return;
    if (s.playing) s.pause(); else s.play();
    onFrame(s.state());
  });
  const scrub = $('#scrub');
  scrub.addEventListener('pointerdown', () => { state.scrubbing = true; });
  scrub.addEventListener('pointerup', () => { state.scrubbing = false; });
  let pending = 0;
  scrub.addEventListener('input', () => {
    const s = state.scene;
    if (!s?.field) return;
    if (s.playing) s.pause();
    // re-integrating every particle is ~10-40 ms; coalesce to one per frame
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(() => s.seek(Number(scrub.value)));
  });
  $('#speed').addEventListener('change', (e) => { if (state.scene) state.scene.speed = Number(e.target.value); });
  for (const b of document.querySelectorAll('.vbtn')) {
    b.addEventListener('click', () => { state.scene?.flyTo(b.dataset.view); setView(b.dataset.view); });
  }
  $('#ctl-vex').addEventListener('input', (e) => {
    state.scene?.setVex(Number(e.target.value));
    $('#legend-vex').textContent = e.target.value;
  });
  $('#ctl-floor').addEventListener('input', (e) => state.scene?.setFloor(Number(e.target.value)));
  for (const [id, key] of [['#show-smoke', 'smoke'], ['#show-field', 'field'], ['#show-contours', 'contours'], ['#show-fires', 'fires']]) {
    $(id).addEventListener('change', (e) => state.scene?.setShow(key, e.target.checked));
  }
  const lt = $('#legend-toggle');
  lt.addEventListener('click', () => {
    const open = lt.getAttribute('aria-expanded') !== 'true';
    lt.setAttribute('aria-expanded', String(open));
    $('#legend').dataset.collapsed = String(!open);
    $('#legend-body').hidden = !open;
  });
  if (window.innerWidth < 720) lt.click();

  const preset = (o) => {
    $('#ctl-hours').value = o.hours;
    $('#ctl-wind-dir').value = o.dir;
    $('#ctl-wind-speed').value = o.speed;
    $('#ctl-release').checked = o.release;
    for (const id of ['#ctl-hours', '#ctl-wind-dir', '#ctl-wind-speed']) $(id).dispatchEvent(new Event('input'));
    run();
  };
  $('#mode-week').addEventListener('click', () => { if (state.mode !== 'week') enterWeek(); });
  $('#mode-model').addEventListener('click', () => {
    if (state.mode === 'model') return;
    if (state.lastRaw) {
      applyMode('model');
      state.scene.pause();
      state.data = state.scene.setData(state.lastRaw);
      renderLegend(); buildReceptorLists(); renderArrivals(); renderValidation();
      renderProvenance(); renderCaveats(); renderSumatra(); renderTicks();
      $('#scrub').max = String(state.scene.field.tEnd);
      onFrame(state.scene.state());
      if (!reduced) state.scene.play();
    } else run();
  });
  $('#preset-release').addEventListener('click', () => preset({ hours: 96, dir: 150, speed: 4.5, release: true }));
  $('#preset-steady').addEventListener('click', () => preset({ hours: 24, dir: 190, speed: 3, release: false }));
  $('#preset-west').addEventListener('click', () => preset({ hours: 96, dir: 110, speed: 4.5, release: true }));
}

async function loadSeasons() {
  let res;
  try { res = await fetch('/api/history'); } catch {
    throw Object.assign(new Error('The model API could not be reached.'), { what: 'GET /api/history' });
  }
  if (!res.ok) throw Object.assign(new Error(`/api/history returned ${res.status}`), { what: 'GET /api/history' });
  const { seasons } = await res.json();
  state.seasons = (seasons || []).filter((s) => (s.burned_area_mha_kalimantan || 0) > 0);
  const sel = $('#ctl-year');
  sel.textContent = '';
  for (const s of state.seasons.slice().reverse()) {
    const o = document.createElement('option');
    o.value = String(s.year);
    o.textContent = `${s.year} · ${String(s.enso_phase || '').replace('_', ' ')} · ${s.burned_area_mha_kalimantan.toFixed(2)} Mha`;
    sel.appendChild(o);
  }
  sel.value = state.seasons.some((s) => s.year === 2015) ? '2015' : sel.options[0]?.value;
  syncDate();
  if (sel.value === '2015') $('#ctl-date').value = '2015-10-14';
  sel.addEventListener('change', syncDate);
}

function syncDate() {
  const y = Number($('#ctl-year').value);
  const s = state.seasons.find((x) => x.year === y);
  const d = $('#ctl-date');
  if (!s) return;
  d.min = s.start_date || `${y}-01-01`;
  d.max = s.end_date || `${y}-12-31`;
  d.value = s.peak_date || s.start_date || `${y}-09-15`;
}

async function boot() {
  $('#stage-error').hidden = true;
  $('#stage-loading').hidden = false;
  $('#stage-loading-text').textContent = 'Loading the 3D map…';
  setStatus('pending', 'loading map…');
  const seasons = loadSeasons().catch((e) => e);
  const week = loadWeek({ fixture: FIXTURE }).catch((e) => ({ available: false, reason: String(e.message || e) }));
  try {
    if (!state.scene) {
      const scene = new Scene($('#map'), { budget: small() ? 'med' : 'high', interactive: true, receptors: true, vex: 10 });
      await scene.init();
      scene.onFrame = onFrame;
      scene.onReceptorClick = flyToCity;
      scene.map.on('rotate', () => { const d = $('#ro-arrow').dataset.dir; if (d) updateArrow(Number(d)); });
      state.scene = scene;
      window.__hazeScene = scene;    // handle for debugging from the console
      setView('home');
    }
  } catch (err) {
    const what = err.what || err.kind || 'the 3D map';
    showError(
      err.kind === 'webgl' ? 'This browser cannot draw the 3D map.' : 'The 3D map could not load.',
      String(err.message || err),
      `${what}${err.kind === 'lib' ? ` (MapLibre ${VERSIONS.maplibre}, deck.gl ${VERSIONS.deck}, from unpkg.com)` : ''}`,
    );
    setStatus('error', `${what} failed`);
    return;
  }
  state.week = await week;
  if (state.week.available) {
    $('#stage-loading').hidden = true;
    enterWeek();
  }
  const s = await seasons;
  if (s instanceof Error) {
    if (state.week.available) return;     // this week still works without the model
    showError('The model API did not answer.', s.message, s.what);
    setStatus('error', 'API failed');
    return;
  }
  // Model-run defaults are the original ones (steady day, wind from 190° at
  // 3 m/s). They are not tuned to aim smoke at any audience; the presets
  // make the wind's role explicit instead.
  if (state.week.available) return;      // model runs on demand from here
  renderWeekPanels();                    // says why "this week" is unavailable
  await run();
  if (!state.week.available) notice(`${state.week.reason || "This week's data is not available."} Showing the model run.`);
}

wire();
boot();
