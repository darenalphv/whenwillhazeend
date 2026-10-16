/**
 * sim3d.js — the 3D haze simulator.
 *
 * Renders /api/simulate/haze3d as extruded columns over a dark basemap. The
 * grid, the concentrations, the receptor readouts and the caveats all come
 * from the model; nothing numeric is invented here.
 *
 * The one thing worth understanding about this view: across the simulated day
 * the fires do not change. The emission rate of a smouldering peat cluster is
 * effectively constant. What changes is the mixing height, and the columns
 * rise and fall with it. That is the actual mechanism behind "the haze is
 * worse in the morning".
 */

const API = '/api/simulate/haze3d';

/* AQI breakpoints, matching the model's own bands. Colours are the palette's
   AQI ramp read from CSS so this page cannot drift from the main one. */
const BANDS = [
  { max: 12, name: 'Good' },
  { max: 35.4, name: 'Moderate' },
  { max: 55.4, name: 'Unhealthy for sensitive groups' },
  { max: 150.4, name: 'Unhealthy' },
  { max: 250.4, name: 'Very unhealthy' },
  { max: Infinity, name: 'Hazardous' },
];
const BAND_RGB = [
  [99, 211, 166], [240, 180, 41], [255, 138, 60],
  [255, 106, 106], [198, 84, 198], [156, 42, 60],
];

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

const $ = (s, r = document) => r.querySelector(s);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const compass = (d) => COMPASS[Math.round(((d % 360) + 360) % 360 / 22.5) % 16];

function bandIndex(v) {
  for (let i = 0; i < BANDS.length; i += 1) if (v <= BANDS[i].max) return i;
  return BANDS.length - 1;
}

/* ------------------------------------------------------------------ state */

const state = {
  field: null,
  frame: 0,
  playing: false,
  timer: null,
  deck: null,
  cells: [],           // {lon, lat, i} once per grid cell, reused every frame
  running: false,
  seasons: [],
  t: 0,            // seconds since load, drives the fire flicker
  anim: 0,
  reduced: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
};

const INITIAL_VIEW = {
  longitude: 113.5, latitude: -1.6, zoom: 5.1,
  pitch: 52, bearing: -18, maxZoom: 11, minZoom: 3,
};

/* ------------------------------------------------------------------- deck */

function basemapLayer() {
  const { TileLayer } = deck;
  const { BitmapLayer } = deck;
  return new TileLayer({
    id: 'basemap',
    // Esri Dark Gray Canvas: free, no API key. CARTO's dark_all now watermarks
    // unkeyed tiles, so it is not an option here.
    data: 'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    minZoom: 0,
    maxZoom: 16,
    tileSize: 256,
    renderSubLayers: (props) => {
      const { boundingBox } = props.tile;
      return new BitmapLayer(props, {
        data: null,
        image: props.data,
        bounds: [boundingBox[0][0], boundingBox[0][1],
          boundingBox[1][0], boundingBox[1][1]],
      });
    },
  });
}

/** Cell centres, computed once per field. */
function buildCells(grid) {
  const cells = [];
  const { nx, ny, lon_min, lat_min, dlon, dlat } = grid;
  for (let r = 0; r < ny; r += 1) {
    for (let c = 0; c < nx; c += 1) {
      cells.push({ lon: lon_min + c * dlon, lat: lat_min + r * dlat, i: r * nx + c });
    }
  }
  return cells;
}

function currentValues() {
  const f = state.field && state.field.frames[state.frame];
  return f ? f.values : null;
}

function layers() {
  const out = [basemapLayer()];
  if (!state.field || !state.field.grid) return out;

  const { ColumnLayer, ScatterplotLayer, TextLayer } = deck;
  const vals = currentValues();
  const floor = Number($('#ctl-floor').value);
  const exag = Number($('#ctl-exag').value);
  const grid = state.field.grid;
  // Cell radius in metres, from the grid spacing, so columns tile the domain.
  const cellM = grid.dlon * 111320 * Math.cos(grid.lat_min * Math.PI / 180) * 0.5;

  out.push(new ColumnLayer({
    id: 'haze',
    data: state.cells,
    diskResolution: 4,
    radius: Math.max(1200, cellM),
    angle: 45,
    extruded: true,
    pickable: true,
    getPosition: (d) => [d.lon, d.lat],
    // sqrt keeps a 5 -> 8000 range legible; linear would bury everything
    // under one spike, log would flatter the low end into looking dangerous.
    getElevation: (d) => {
      const v = vals[d.i];
      return v < floor ? 0 : Math.sqrt(v) * 1250 * exag;
    },
    // Alpha ramps with concentration. A flat alpha made the whole domain a
    // solid rectangle whose edge was the grid boundary, not the haze -- which
    // read as a wall of smoke stopping dead in mid-air. Fading the thin edges
    // out lets the plume have a shape.
    getFillColor: (d) => {
      const v = vals[d.i];
      if (v < floor) return [0, 0, 0, 0];
      const rgb = BAND_RGB[bandIndex(v)];
      const t = Math.min(1, Math.sqrt((v - floor) / 220));
      return [rgb[0], rgb[1], rgb[2], Math.round(26 + t * 184)];
    },
    updateTriggers: {
      getElevation: [state.frame, floor, exag],
      getFillColor: [state.frame, floor],
    },
    transitions: { getElevation: 320, getFillColor: 320 },
  }));

  /* The fires themselves. Three stacked layers so a cluster reads as burning
     rather than as a marker: an outer heat halo that breathes, a flame column
     that flickers, and a hot core. Each source gets its own phase from its
     coordinates, so they never pulse in unison -- synchronised fire is the
     tell that you are looking at a UI element rather than a landscape. */
  const T = state.t;
  const phase = (d) => (d.lon * 7.3 + d.lat * 11.7);
  const flicker = (d, rate, depth) => 1 + depth * (
    0.6 * Math.sin(T * rate + phase(d))
    + 0.4 * Math.sin(T * rate * 2.37 + phase(d) * 1.7));
  const baseR = (d) => clamp(Math.sqrt(d.area_ha) * 28, 1500, 22000);

  out.push(new ScatterplotLayer({
    id: 'fire-halo',
    data: state.field.sources,
    radiusUnits: 'meters',
    getPosition: (d) => [d.lon, d.lat],
    getRadius: (d) => baseR(d) * 2.6 * flicker(d, 1.1, 0.10),
    getFillColor: (d) => [255, 96, 34, Math.round(34 * flicker(d, 1.1, 0.35))],
    updateTriggers: { getRadius: T, getFillColor: T },
  }));

  out.push(new ColumnLayer({
    id: 'fire-flames',
    data: state.field.sources,
    diskResolution: 6,
    radius: 4200,
    extruded: true,
    getPosition: (d) => [d.lon, d.lat],
    getElevation: (d) => Math.sqrt(d.area_ha) * 320 * flicker(d, 2.6, 0.22) * exag,
    getFillColor: (d) => {
      const f = clamp(flicker(d, 2.6, 0.5), 0.4, 1.6);
      return [255, Math.round(120 + 70 * f), Math.round(40 * f), 205];
    },
    updateTriggers: { getElevation: [T, exag], getFillColor: T },
  }));

  out.push(new ScatterplotLayer({
    id: 'fires',
    data: state.field.sources,
    pickable: true,
    radiusUnits: 'meters',
    getPosition: (d) => [d.lon, d.lat],
    getRadius: (d) => baseR(d) * flicker(d, 3.1, 0.06),
    getFillColor: (d) => [255, Math.round(196 + 40 * Math.sin(T * 3.1 + phase(d))), 150, 235],
    stroked: true,
    getLineColor: [255, 236, 214, 210],
    lineWidthMinPixels: 1,
    updateTriggers: { getRadius: T, getFillColor: T },
  }));

  if ($('#ctl-trails').checked) {
    const trails = smokeTrailsLayer();
    if (trails) out.push(trails);
  }

  const rec = state.field.receptor_series || [];
  out.push(new ScatterplotLayer({
    id: 'receptors',
    data: rec,
    pickable: true,
    radiusUnits: 'pixels',
    getPosition: (d) => [d.lon, d.lat],
    getRadius: 5,
    getFillColor: (d) => {
      const v = d.pm25[state.frame] ?? 0;
      const rgb = BAND_RGB[bandIndex(v)];
      return [rgb[0], rgb[1], rgb[2], 255];
    },
    stroked: true,
    getLineColor: [12, 10, 9, 255],
    lineWidthMinPixels: 2,
    updateTriggers: { getFillColor: state.frame },
  }));

  out.push(new TextLayer({
    id: 'receptor-labels',
    data: rec,
    getPosition: (d) => [d.lon, d.lat],
    getText: (d) => `${d.name}  ${Math.round(d.pm25[state.frame] ?? 0)}`,
    getSize: 11,
    getColor: [244, 239, 232, 225],
    getPixelOffset: [0, -14],
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    outlineWidth: 3,
    outlineColor: [8, 7, 6, 255],
    fontSettings: { sdf: true },
    updateTriggers: { getText: state.frame },
  }));

  return out;
}

/* ------------------------------------------------------------ smoke trails */

/**
 * Trajectories of parcels released from the biggest clusters, integrated
 * through the model's own per-hour wind (including veer). These are not
 * decoration: each vertex is where a parcel would be after that many hours at
 * the modelled wind speed and direction. In release mode the trail head is the
 * plume front at the displayed hour, which is what carries smoke to Sarawak.
 */
function buildTrails(field) {
  const src = field.sources.slice()
    .sort((a, b) => b.area_ha - a.area_ha)
    .slice(0, 26);
  const step = field.conditions.step_hours;
  const u = field.conditions.wind_speed_ms;
  const trails = [];

  for (const s of src) {
    let lon = s.lon;
    let lat = s.lat;
    const path = [[lon, lat, 0]];
    const times = [0];
    for (let k = 0; k < field.frames.length; k += 1) {
      const f = field.frames[k];
      // meteorological "from" -> the bearing it travels toward
      const toRad = ((f.wind_dir_deg + 180) % 360) * Math.PI / 180;
      const dist = u * step * 3600;                   // metres this step
      const dLat = (dist * Math.cos(toRad)) / 110574;
      const dLon = (dist * Math.sin(toRad))
        / (111320 * Math.cos(lat * Math.PI / 180));
      lat += dLat;
      lon += dLon;
      path.push([lon, lat, 0]);
      times.push((k + 1) * step);
    }
    trails.push({ path, timestamps: times, area_ha: s.area_ha });
  }
  return trails;
}

function smokeTrailsLayer() {
  const field = state.field;
  if (!state.trails || !state.trails.length) return null;
  const total = field.conditions.hours;
  const release = field.conditions.release_from_start;
  // Release mode: the head is the front at the hour on screen. Steady state:
  // a continuous loop, which reads as flow rather than as an advancing front.
  const now = release
    ? field.frames[state.frame].hour
    : (state.t * 3.2) % (total + 12);

  const Trips = deck.TripsLayer;
  if (Trips) {
    return new Trips({
      id: 'smoke-trails',
      data: state.trails,
      getPath: (d) => d.path,
      getTimestamps: (d) => d.timestamps,
      getColor: [214, 206, 198],
      opacity: 0.30,
      widthMinPixels: 2,
      jointRounded: true,
      capRounded: true,
      fadeTrail: true,
      trailLength: release ? Math.max(8, total * 0.5) : 14,
      currentTime: now,
      updateTriggers: { currentTime: now },
    });
  }
  // TripsLayer is part of the standalone bundle, but if a future build drops it
  // the trails degrade to static paths rather than the page failing.
  return new deck.PathLayer({
    id: 'smoke-trails',
    data: state.trails,
    getPath: (d) => d.path,
    getColor: [214, 206, 198, 60],
    widthMinPixels: 2,
  });
}

function tooltip({ object, layer }) {
  if (!object) return null;
  if (layer.id === 'haze') {
    const v = currentValues()[object.i];
    return { text: `${Math.round(v)} µg/m³ — ${BANDS[bandIndex(v)].name}` };
  }
  if (layer.id === 'fires') {
    return { text: `${Math.round(object.area_ha).toLocaleString()} ha · ${object.pool.replace(/_/g, ' ')}` };
  }
  if (layer.id === 'receptors') {
    const v = object.pm25[state.frame] ?? 0;
    return {
      text: `${object.name}\n${Math.round(v)} µg/m³ now\npeak ${Math.round(object.peak_pm25)} at ${String(object.peak_hour_of_day).padStart(2, '0')}:00\n${Math.round(object.distance_km)} km from the fires`,
    };
  }
  return null;
}

/* A single rAF clock drives the fire flicker and the trail flow. It stops when
   the tab is hidden and never starts under prefers-reduced-motion, where the
   fires stay lit but still and the trails stay put. */
function startClock() {
  if (state.reduced || state.anim) return;
  let last = performance.now();
  const tick = (now) => {
    state.t += Math.min(0.05, (now - last) / 1000);
    last = now;
    if (state.field) redraw();
    state.anim = requestAnimationFrame(tick);
  };
  state.anim = requestAnimationFrame(tick);
}

function stopClock() {
  if (state.anim) cancelAnimationFrame(state.anim);
  state.anim = 0;
}

function initDeck() {
  state.deck = new deck.Deck({
    canvas: 'deck-canvas',
    initialViewState: INITIAL_VIEW,
    controller: { dragRotate: true, touchRotate: true },
    layers: layers(),
    getTooltip: tooltip,
    parameters: { clearColor: [0.043, 0.039, 0.035, 1] },
  });
}

function redraw() {
  if (state.deck) state.deck.setProps({ layers: layers() });
}

/* -------------------------------------------------------------- rendering */

function setStatus(kind, text) {
  const d = $('#sim-status .status__dot');
  const t = $('#sim-status .status__text');
  if (d) d.dataset.status = kind;
  if (t) t.textContent = text;
}

function showFrame(i) {
  const field = state.field;
  if (!field || !field.frames.length) return;
  state.frame = clamp(i, 0, field.frames.length - 1);
  const f = field.frames[state.frame];

  $('#fr-time').textContent = f.label;
  $('#fr-date').textContent = f.time ? ` · ${f.time.slice(0, 10)}` : '';
  $('#fr-mix').textContent = `${f.mixing_height_m.toLocaleString()} m`;
  $('#fr-max').textContent = `${Math.round(f.max_pm25).toLocaleString()} µg/m³`;
  $('#fr-wind').textContent = `${f.wind_speed_ms} m/s from ${Math.round(f.wind_dir_deg)}° ${compass(f.wind_dir_deg)}`;
  $('#frame-slider').value = String(state.frame);
  $('#frame-label').textContent = f.label;

  const night = f.mixing_height_m < field.conditions.mean_mixing_height_m;
  $('#fr-note').textContent = field.conditions.diurnal
    ? (night
      ? 'The layer has collapsed. Same fires, less air to dilute into.'
      : 'Convection has lifted the layer. Same fires, more air.')
    : 'Diurnal cycle off — the mixing height is held flat.';

  renderReceptors();
  redraw();
}

function renderReceptors() {
  const ul = $('#receptor-list');
  const rec = (state.field.receptor_series || [])
    .slice()
    .sort((a, b) => (b.pm25[state.frame] ?? 0) - (a.pm25[state.frame] ?? 0))
    .slice(0, 8);
  ul.textContent = '';
  for (const r of rec) {
    const v = r.pm25[state.frame] ?? 0;
    const li = document.createElement('li');
    li.className = 'receptor';
    const rgb = BAND_RGB[bandIndex(v)];
    li.innerHTML =
      `<span class="receptor__dot" style="background:rgb(${rgb.join(',')})"></span>` +
      `<span class="receptor__name"></span>` +
      `<b class="receptor__v"></b>` +
      `<span class="receptor__cat"></span>`;
    li.querySelector('.receptor__name').textContent = r.name;
    li.querySelector('.receptor__v').textContent = `${Math.round(v)}`;
    li.querySelector('.receptor__cat').textContent = BANDS[bandIndex(v)].name;
    ul.appendChild(li);
  }
  $('#receptor-block').hidden = rec.length === 0;
}

function renderLegend() {
  const ul = $('#legend-scale');
  ul.textContent = '';
  BANDS.forEach((b, i) => {
    const li = document.createElement('li');
    const lo = i === 0 ? 0 : Math.ceil(BANDS[i - 1].max);
    const hi = b.max === Infinity ? '250+' : Math.round(b.max);
    li.innerHTML = `<i style="background:rgb(${BAND_RGB[i].join(',')})"></i><span></span>`;
    li.querySelector('span').textContent =
      `${b.name} · ${b.max === Infinity ? hi : `${lo}–${hi}`}`;
    ul.appendChild(li);
  });
  $('#legend').hidden = false;
}

function renderValidation() {
  const v = state.field.validation;
  const block = $('#validation-block');
  const body = $('#validation-body');
  body.textContent = '';
  if (!v || !v.rows || !v.rows.length) {
    block.hidden = false;
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent =
      'This year has no reported station peak in the dataset, so this run has '
      + 'not been checked against anything. That is not the same as it being right.';
    body.appendChild(p);
    return;
  }
  for (const row of v.rows) {
    const d = document.createElement('p');
    d.className = 'validation__row';
    d.innerHTML = '<b></b><span></span>';
    d.querySelector('b').textContent =
      `${row.receptor}: modelled ${Math.round(row.modelled_peak_ugm3).toLocaleString()} vs reported ${Math.round(row.reported_peak_ugm3).toLocaleString()} µg/m³`;
    d.querySelector('span').textContent =
      row.ratio ? ` — this run is running ${row.ratio}× high.` : '';
    body.appendChild(d);
  }
  const note = document.createElement('p');
  note.className = 'muted';
  note.textContent = v.note || '';
  body.appendChild(note);
  block.hidden = false;
}

function renderArrivals() {
  const field = state.field;
  const ul = $('#arrival-list');
  const block = $('#arrival-block');
  ul.textContent = '';

  const downwind = (field.receptor_series || [])
    .filter((r) => r.downwind && r.arrival_hours != null)
    .sort((a, b) => a.arrival_hours - b.arrival_hours);

  if (!downwind.length) {
    block.hidden = false;
    $('#arrival-note').textContent =
      'No receptor city lies downwind of these fires on this wind. Turn the '
      + 'wind around and the same smoke goes somewhere else entirely.';
    return;
  }

  const runHours = field.conditions.hours;
  for (const r of downwind.slice(0, 9)) {
    const li = document.createElement('li');
    li.className = 'arrival';
    const within = r.arrival_hours <= runHours;
    const rgb = BAND_RGB[bandIndex(r.peak_pm25)];
    li.innerHTML = '<span class="arrival__name"></span>'
      + '<span class="arrival__t"></span>'
      + '<span class="arrival__peak"></span>';
    li.querySelector('.arrival__name').textContent =
      `${r.name}${r.country ? ` · ${r.country}` : ''}`;
    li.querySelector('.arrival__t').textContent =
      within ? `+${r.arrival_hours.toFixed(1)} h` : `+${r.arrival_hours.toFixed(0)} h — past this run`;
    const peak = li.querySelector('.arrival__peak');
    peak.textContent = `${Math.round(r.distance_km)} km · peak ${Math.round(r.peak_pm25)} µg/m³`;
    peak.style.color = `rgb(${rgb.join(',')})`;
    if (!within) li.classList.add('arrival--beyond');
    ul.appendChild(li);
  }

  $('#arrival-note').textContent = field.conditions.release_from_start
    ? 'Release mode: the front leaves the fires at hour 0 and travels at the mean wind speed.'
    : 'Times are how long smoke takes to cover the distance. The field itself is steady state — turn on release mode to watch the front move.';
  block.hidden = false;
}

function renderCaveats() {
  const ul = $('#sim-caveats');
  ul.textContent = '';
  for (const c of state.field.caveats || []) {
    const li = document.createElement('li');
    li.textContent = c;
    ul.appendChild(li);
  }
}

/* ------------------------------------------------------------------ fetch */

function params() {
  return {
    year: Number($('#ctl-year').value),
    start_date: $('#ctl-date').value || null,
    hours: Number($('#ctl-hours').value),
    step_hours: Number($('#ctl-hours').value) > 48 ? 2 : 1,
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

async function run() {
  if (state.running) return;
  state.running = true;
  pause();
  $('#stage-error').hidden = true;
  $('#stage-loading').hidden = false;
  $('#stage-loading-text').textContent = 'Running the dispersion model…';
  setStatus('pending', 'running…');
  $('#run-btn').disabled = true;

  try {
    const body = params();
    const res = await fetch(API, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const j = await res.json();
        if (typeof j.detail === 'string') detail = j.detail;
        else if (Array.isArray(j.detail) && j.detail[0]?.msg) detail = j.detail[0].msg;
      } catch { /* keep the status line */ }
      throw new Error(detail);
    }
    const field = await res.json();
    if (!field.grid || !field.frames.length) {
      throw new Error(field.note || 'The model returned no field for those inputs.');
    }

    state.field = field;
    state.cells = buildCells(field.grid);
    state.trails = buildTrails(field);
    state.frame = 0;

    $('#frame-slider').max = String(field.frames.length - 1);
    $('#frame-readout').hidden = false;
    $('#timeline').hidden = false;
    renderLegend();
    renderArrivals();
    renderValidation();
    renderCaveats();

    const c = field.conditions;
    $('#hint-active').textContent =
      `${field.sources_used} clusters burning on this date`
      + (field.sources_inactive_on_date
        ? `, ${field.sources_inactive_on_date} elsewhere in the season` : '')
      + (field.sources_dropped ? ` · ${field.note}` : '')
      + ` · layer ${c.night_mixing_height_m}–${c.day_mixing_height_m} m`;

    showFrame(0);
    setStatus('ok', `${field.sources_used} fires · ${field.conditions.hours} h`);
  } catch (err) {
    $('#stage-error-msg').textContent = String(err.message || err);
    $('#stage-error').hidden = false;
    setStatus('error', 'model failed');
  } finally {
    $('#stage-loading').hidden = true;
    $('#run-btn').disabled = false;
    state.running = false;
  }
}

/* ------------------------------------------------------------- transport */

function play() {
  if (!state.field || state.playing) return;
  state.playing = true;
  $('#play-btn').innerHTML = '<span aria-hidden="true">❚❚</span> Pause';
  $('#play-btn').setAttribute('aria-label', 'Pause the simulated day');
  state.timer = setInterval(() => {
    showFrame((state.frame + 1) % state.field.frames.length);
  }, 620);
}

function pause() {
  state.playing = false;
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  const b = $('#play-btn');
  if (b) {
    b.innerHTML = '<span aria-hidden="true">▶</span> Play';
    b.setAttribute('aria-label', 'Play the simulated day');
  }
}

/* ------------------------------------------------------------------- init */

function wireOutputs() {
  const bind = (id, out, fmt) => {
    const el = $(id);
    const o = $(out);
    const upd = () => { o.textContent = fmt(Number(el.value)); };
    el.addEventListener('input', upd);
    upd();
  };
  bind('#ctl-wind-speed', '#out-wind-speed', (v) => `${v.toFixed(1)} m/s`);
  bind('#ctl-wind-dir', '#out-wind-dir', (v) => `${v}° ${compass(v)}`);
  bind('#ctl-veer', '#out-veer', (v) => `${v > 0 ? '+' : ''}${v}°/h`);
  bind('#ctl-mix', '#out-mix', (v) => `${v.toLocaleString()} m`);
  bind('#ctl-rain', '#out-rain', (v) => `${v} mm`);
  bind('#ctl-exag', '#out-exag', (v) => `${v.toFixed(1)}×`);
  bind('#ctl-floor', '#out-floor', (v) => `${v} µg/m³`);
  bind('#ctl-grid', '#out-grid', (v) => `${v} × ${v}`);
  bind('#ctl-hours', '#out-hours', (v) => `${v} h`);

  // View-only controls repaint without re-running the model.
  $('#ctl-exag').addEventListener('input', redraw);
  $('#ctl-floor').addEventListener('input', redraw);
  $('#ctl-trails').addEventListener('change', redraw);
}

async function loadSeasons() {
  const res = await fetch('/api/history');
  if (!res.ok) throw new Error(`/api/history returned ${res.status}`);
  const { seasons } = await res.json();
  state.seasons = (seasons || []).filter((s) => (s.burned_area_mha_kalimantan || 0) > 0);
  const sel = $('#ctl-year');
  sel.textContent = '';
  for (const s of state.seasons.slice().reverse()) {
    const o = document.createElement('option');
    o.value = String(s.year);
    o.textContent = `${s.year} · ${s.enso_phase.replace('_', ' ')} · ${(s.burned_area_mha_kalimantan).toFixed(2)} Mha`;
    sel.appendChild(o);
  }
  sel.value = '2015';
  syncDateToYear();
  sel.addEventListener('change', syncDateToYear);
}

/** Default the date to that season's peak, which is what people want to see. */
function syncDateToYear() {
  const y = Number($('#ctl-year').value);
  const s = state.seasons.find((x) => x.year === y);
  const d = $('#ctl-date');
  if (!s) return;
  d.min = s.start_date || `${y}-01-01`;
  d.max = s.end_date || `${y}-12-31`;
  d.value = s.peak_date || s.start_date || `${y}-09-15`;
}

async function boot() {
  wireOutputs();
  initDeck();

  $('#run-btn').addEventListener('click', run);
  $('#reset-view').addEventListener('click', () => {
    state.deck.setProps({ initialViewState: { ...INITIAL_VIEW, transitionDuration: 600 } });
  });
  $('#play-btn').addEventListener('click', () => (state.playing ? pause() : play()));
  $('#frame-slider').addEventListener('input', (e) => {
    pause();
    showFrame(Number(e.target.value));
  });
  // Nothing loops while the tab is hidden.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { pause(); stopClock(); } else { startClock(); }
  });
  startClock();

  try {
    await loadSeasons();
    await run();
  } catch (err) {
    $('#stage-loading').hidden = true;
    $('#stage-error-msg').textContent = String(err.message || err);
    $('#stage-error').hidden = false;
    setStatus('error', 'could not start');
  }
}

$('#stage-retry').addEventListener('click', run);

if (typeof deck === 'undefined') {
  $('#stage-loading').hidden = true;
  $('#stage-error-title').textContent = 'The 3D library did not load.';
  $('#stage-error-msg').textContent =
    'deck.gl is loaded from a CDN and did not arrive. The rest of the model still '
    + 'works on the main page.';
  $('#stage-error').hidden = false;
  setStatus('error', 'deck.gl missing');
} else {
  boot();
}
