/* map.js — Leaflet: province choropleth, year-slider fire replay,
 * dispersion plume overlay, receptor markers.
 *
 * Basemap: CARTO dark_all. All geometry comes from the API.
 */

import {
  api, QS, REDUCED_MOTION, describeError,
  $, el, panel, setPanelState, fmtDate, fmtInt, fmtNum, fmtSig,
  ensoLabel, titleCase, clamp, COLOR, POOL_ORDER, debounce,
} from './util.js';

/* --------------------------------------------------------------- basemaps
 * The contract asks for CARTO dark_all. CARTO now stamps "API KEY REQUIRED"
 * across unkeyed tiles, so CARTO is used only when a key is configured (or
 * forced with ?basemap=carto); otherwise we fall back to Esri's free
 * no-key Dark Gray canvas, which is the same visual register.
 * Drop a key into CARTO_API_KEY and CARTO becomes the default again.
 */
const CARTO_API_KEY = '';

const BASEMAPS = {
  carto: [{
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
      + (CARTO_API_KEY ? `?api_key=${CARTO_API_KEY}` : ''),
    options: {
      subdomains: 'abcd',
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    },
  }],
  esri_dark: [
    {
      url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      options: {
        maxZoom: 16,
        attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
      },
    },
    {
      url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
      options: { maxZoom: 16, pane: 'labels', opacity: 0.8 },
    },
  ],
};

function basemapName() {
  const forced = QS.get('basemap');
  if (forced && BASEMAPS[forced]) return forced;
  return CARTO_API_KEY ? 'carto' : 'esri_dark';
}

let map = null;
let regionLayer = null;
let fireLayer = null;
let plumeLayer = null;
let receptorLayer = null;

let years = [];
let currentYear = null;
let playTimer = null;
const clusterCache = new Map();

let ctx = { regions: null, fuel: null, receptors: null, history: null, meteo: null };

const PLUME_BAND_COLOR = { 35: '--plume-35', 55: '--plume-55', 150: '--plume-150', 250: '--plume-250' };

/** Concentration band of a plume polygon. The API names it pm25_ugm3. */
function bandValue(f) {
  const p = (f && f.properties) || {};
  const v = p.pm25_ugm3 != null ? p.pm25_ugm3 : p.band_ugm3;
  return Number(v);
}

/* ------------------------------------------------------------------ colour */

function hexToRgb(h) {
  const s = h.replace('#', '').trim();
  const v = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}

function mix(a, b, t) {
  const A = hexToRgb(a), B = hexToRgb(b);
  const c = A.map((v, i) => Math.round(v + (B[i] - v) * clamp(t, 0, 1)));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

function peatColor(v, min, max) {
  const t = max > min ? (v - min) / (max - min) : 0.5;
  return mix('#2a2320', '#ff7a45', Math.pow(t, 0.75));
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/* -------------------------------------------------------------------- init */

export async function initMap(data) {
  ctx = { ...ctx, ...data };

  // Leaflet needs a laid-out container: reveal the panel before building.
  setPanelState(panel('map'), 'ok');

  if (!window.L) throw new Error('Leaflet failed to load from the CDN');

  const holder = $('#map');
  if (map) { map.remove(); map = null; }

  map = L.map(holder, {
    zoomControl: true,
    scrollWheelZoom: false,
    attributionControl: true,
    minZoom: 4,
    maxZoom: 11,
  });

  // place-name labels sit above the choropleth but below markers and popups
  map.createPane('labels');
  map.getPane('labels').style.zIndex = 450;
  map.getPane('labels').style.pointerEvents = 'none';

  for (const layer of BASEMAPS[basemapName()]) {
    L.tileLayer(layer.url, layer.options).addTo(map);
  }

  buildRegions();

  plumeLayer = L.layerGroup().addTo(map);
  fireLayer = L.layerGroup().addTo(map);
  receptorLayer = L.layerGroup().addTo(map);

  // Smouldering loops cost nothing when nobody is looking at them.
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(
      ([e]) => holder.classList.toggle('map-frame--idle', !e.isIntersecting),
      { threshold: 0.01 },
    ).observe(holder);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) holder.classList.add('map-frame--idle');
      else holder.classList.remove('map-frame--idle');
    });
  }

  drawReceptors(null);
  buildLegend();
  wireYearSlider();
  wirePlume();

  requestAnimationFrame(() => map.invalidateSize());

  const seasons = (ctx.history && ctx.history.seasons) || [];
  years = seasons.map((s) => s.year).sort((a, b) => a - b);
  if (years.length) {
    const slider = $('#year-slider');
    slider.min = 0;
    slider.max = String(years.length - 1);
    slider.value = String(years.length - 1);
    await setMapYear(years[years.length - 1]);
  } else {
    $('#year-out').textContent = 'no seasons returned';
    $('#year-slider').disabled = true;
  }
}

/* ---------------------------------------------------------------- regions */

function buildRegions() {
  const features = (ctx.regions && ctx.regions.features) || [];
  const peats = features.map((f) => Number(f.properties.peat_mha) || 0);
  const min = Math.min(...peats, 0);
  const max = Math.max(...peats, 1);

  regionLayer = L.geoJSON(ctx.regions, {
    style: (f) => ({
      color: cssVar('--line-2', '#3b3431'),
      weight: 1,
      fillColor: peatColor(Number(f.properties.peat_mha) || 0, min, max),
      fillOpacity: 0.62,
    }),
    onEachFeature: (f, layer) => {
      const p = f.properties || {};
      layer.bindPopup(
        `<h4>${escapeHtml(p.name || 'Province')}</h4>` +
        `<dl>` +
        row('Peat', `${fmtNum(p.peat_mha, 2)} Mha`) +
        row('Land area', `${fmtNum(p.area_mha, 1)} Mha`) +
        row('Population', fmtInt(p.population)) +
        row('Capital', escapeHtml(p.capital || '—')) +
        `</dl>`
      );
      layer.on('mouseover', () => layer.setStyle({
        weight: 2, color: cssVar('--ember-2', '#ffa66f'), fillOpacity: 0.78,
      }));
      layer.on('mouseout', () => regionLayer.resetStyle(layer));
    },
  }).addTo(map);

  const b = regionLayer.getBounds();
  if (b.isValid()) map.fitBounds(b, { padding: [18, 18] });
  else map.setView([-1.5, 114], 6);
}

function row(k, v) { return `<dt>${k}</dt><dd>${v}</dd>`; }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ------------------------------------------------------------------- fires */

function radiusFor(areaHa) {
  const a = Math.max(1, Number(areaHa) || 1);
  return clamp(2 + Math.sqrt(a) / 13, 2.5, 26);
}

/* Ignition timing ------------------------------------------------------- *
 * Clusters used to appear all at once, which reads as "here is a year's
 * total". They didn't happen all at once: a season builds from the first
 * dry-season ignitions to the peak. Lighting them in date order shows that,
 * and shows the thing the whole model rests on — peat keeps burning after the
 * flaming front has moved on, so peat clusters smoulder and the rest settle.  */
const IGNITE_WINDOW_MS = 1700;   // total stagger, capped so replay stays brisk
const SMOULDER_CAP = 60;         // largest peat clusters only; 400 looping
                                 // SVG paths is not a budget, it's a bonfire
const PEAT_POOLS = new Set(['peat_drained', 'peat_intact']);

/** Fractional position of a cluster's date inside its season, 0..1. */
function seasonFraction(dateStr, t0, span) {
  const t = Date.parse(dateStr);
  if (!Number.isFinite(t) || !span) return Math.random();
  return clamp((t - t0) / span, 0, 1);
}

function drawFires(geojson, season) {
  fireLayer.clearLayers();
  const feats = (geojson && geojson.features) || [];

  // Chronological, so ignition order is the season's own order.
  const dated = feats.slice().sort((a, b) => {
    const da = Date.parse((a.properties || {}).date) || 0;
    const db = Date.parse((b.properties || {}).date) || 0;
    return da - db;
  });
  const times = dated
    .map((f) => Date.parse((f.properties || {}).date))
    .filter(Number.isFinite);
  const t0 = times.length ? times[0] : 0;
  const span = times.length ? (times[times.length - 1] - t0) : 0;

  // The biggest peat clusters are the ones that smoulder for weeks.
  const smoulderIds = new Set(
    dated
      .filter((f) => PEAT_POOLS.has((f.properties || {}).pool))
      .sort((a, b) => (b.properties.area_ha || 0) - (a.properties.area_ha || 0))
      .slice(0, SMOULDER_CAP),
  );

  const pending = [];
  for (const f of dated) {
    if (!f.geometry) continue;
    const p = f.properties || {};
    const color = COLOR.pool(p.pool);
    let layer;
    if (f.geometry.type === 'Point') {
      const [lon, lat] = f.geometry.coordinates;
      layer = L.circleMarker([lat, lon], {
        radius: radiusFor(p.area_ha),
        color,
        weight: 0.8,
        opacity: 0.85,
        fillColor: color,
        fillOpacity: 0.32,
      });
    } else {
      layer = L.geoJSON(f, {
        style: { color, weight: 1, fillColor: color, fillOpacity: 0.35 },
      });
    }
    layer.bindPopup(
      `<h4>${escapeHtml(titleCase(p.pool || 'fire'))}</h4><dl>` +
      row('Date', fmtDate(p.date)) +
      row('Area', `${fmtInt(p.area_ha)} ha`) +
      row('Burned for', `${fmtInt(p.duration_days)} d`) +
      row('FRP', `${fmtInt(p.frp_mw)} MW`) +
      row('Province', escapeHtml(p.province || '—')) +
      `</dl>`
    );
    fireLayer.addLayer(layer);
    pending.push([layer, f]);
  }

  applyIgnition(pending, t0, span, smoulderIds);
  renderYearSummary(season, feats);
}

/**
 * Stagger each marker's entrance by where its date falls in the season.
 * Pure CSS once the delays are set — no per-frame JS for the fires.
 */
function applyIgnition(pending, t0, span, smoulderIds) {
  for (const [layer, f] of pending) {
    const node = typeof layer.getElement === 'function' ? layer.getElement() : null;
    if (!node) continue;
    const p = f.properties || {};
    const delay = seasonFraction(p.date, t0, span) * IGNITE_WINDOW_MS;
    node.style.setProperty('--ign-delay', `${Math.round(delay)}ms`);
    node.classList.add('fire-ignite');
    if (smoulderIds.has(f)) {
      // De-synchronise the loops so the peat domes breathe independently.
      node.style.setProperty('--smoulder-dur', `${(3.8 + Math.random() * 3.4).toFixed(2)}s`);
      node.classList.add('fire-smoulder');
    }
  }
}

function renderYearSummary(season, feats) {
  const dl = $('#year-summary');
  if (!dl) return;
  dl.textContent = '';
  if (!season) {
    dl.append(el('dt', { text: 'No season record' }), el('dd', { text: '—' }));
    return;
  }
  const totalHa = feats.reduce((a, f) => a + (Number(f.properties && f.properties.area_ha) || 0), 0);
  const rows = [
    ['ENSO phase', `${ensoLabel(season.enso_phase)}${season.oni_peak != null ? ` (ONI ${fmtSig(season.oni_peak)})` : ''}`],
    ['Season ran', `${fmtDate(season.start_date, { withYear: false })} → ${fmtDate(season.end_date, { withYear: false })}`],
    ['Duration', season.duration_days != null ? `${fmtInt(season.duration_days)} days` : '—'],
    ['Burned, Kalimantan', season.burned_area_mha_kalimantan != null ? `${fmtNum(season.burned_area_mha_kalimantan, 2)} Mha` : '—'],
    ['Peat fraction', season.peat_fraction != null ? `${fmtInt(season.peat_fraction * 100)}%` : '—'],
    ['Hotspots', fmtInt(season.hotspot_count)],
    ['Clusters shown', `${fmtInt(feats.length)} · ${fmtInt(totalHa)} ha`],
  ];
  if (season.excess_deaths_est != null) {
    rows.push(['Excess deaths (est.)', fmtInt(season.excess_deaths_est)]);
  }
  for (const [k, v] of rows) {
    dl.append(el('dt', { text: k }), el('dd', { text: v }));
  }
}

/* --------------------------------------------------------------- receptors */

function drawReceptors(dispersion) {
  receptorLayer.clearLayers();
  const modelled = new Map(
    ((dispersion && dispersion.receptors) || []).map((r) => [r.name, r]));
  const list = ctx.receptors || [];
  for (const r of list) {
    const m = modelled.get(r.name);
    const color = m ? COLOR.aqi(m.category) : cssVar('--ink-3', '#9d948a');
    const marker = L.circleMarker([r.lat, r.lon], {
      radius: m ? clamp(5 + Math.sqrt(m.pm25_ugm3 || 0) / 2.2, 5, 16) : 4.5,
      color,
      weight: 2,
      fillColor: color,
      fillOpacity: m ? 0.45 : 0.15,
    });
    let html = `<h4>${escapeHtml(r.name)}</h4><dl>`;
    if (r.country) html += row('Country', escapeHtml(r.country));
    if (r.population != null) html += row('Population', fmtInt(r.population));
    if (m) {
      html += row('Modelled PM2.5', `${fmtNum(m.pm25_ugm3, 1)} µg/m³`);
      if (m.aqi != null) html += row('AQI', fmtInt(m.aqi));
      if (m.category) html += row('Category', escapeHtml(m.category));
      if (m.distance_km != null) html += row('Distance', `${fmtInt(m.distance_km)} km`);
      if (m.arrival_hours != null) html += row('Arrival', `${fmtNum(m.arrival_hours, 1)} h`);
    } else {
      html += row('Modelled PM2.5', 'plume off');
    }
    html += '</dl>';
    marker.bindPopup(html);
    marker.bindTooltip(m ? `${r.name} · ${fmtNum(m.pm25_ugm3, 0)} µg/m³` : r.name, {
      direction: 'top', className: 'receptor-tip',
    });
    receptorLayer.addLayer(marker);
  }
}

/* ------------------------------------------------------------------ legend */

function buildLegend() {
  const box = $('#map-legend');
  if (!box) return;
  box.textContent = '';

  const features = (ctx.regions && ctx.regions.features) || [];
  const peats = features.map((f) => Number(f.properties.peat_mha) || 0);
  const min = Math.min(...peats, 0), max = Math.max(...peats, 1);

  const ramp = el('div', { class: 'legend-row' });
  const bar = el('span', {
    style: `flex:1;height:8px;border-radius:2px;background:linear-gradient(90deg, ${peatColor(min, min, max)}, ${peatColor(max, min, max)})`,
  });
  ramp.append(el('span', { text: fmtNum(min, 1) }), bar, el('span', { text: fmtNum(max, 1) }));
  box.append(el('div', { class: 'legend-group' }, [
    el('h4', { text: 'Province shading — peat area (Mha)' }),
    ramp,
    el('div', { class: 'legend-row' }, [
      el('span', { text: 'Circle size = area burned' }),
    ]),
  ]));

  const pools = (ctx.fuel && ctx.fuel.pools) || [];
  if (pools.length) {
    const group = el('div', { class: 'legend-group' }, [
      el('h4', { text: 'Fire cluster, by fuel' }),
    ]);
    const ordered = [...pools].sort(
      (a, b) => POOL_ORDER.indexOf(a.id) - POOL_ORDER.indexOf(b.id));
    for (const p of ordered) {
      group.append(el('div', { class: 'legend-row' }, [
        el('span', { class: 'legend-swatch legend-swatch--round', style: `background:${COLOR.pool(p.id)}` }),
        el('span', { text: p.label || titleCase(p.id) }),
      ]));
    }
    box.append(group);
  }

  const plumeBox = el('div', { id: 'plume-legend', class: 'legend-group' });
  plumeBox.hidden = true;
  box.append(plumeBox);
}

function renderPlumeLegend(dispersion) {
  const box = $('#plume-legend');
  if (!box) return;
  box.textContent = '';
  const feats = (dispersion && dispersion.plume && dispersion.plume.features) || [];
  if (!feats.length) { box.hidden = true; return; }
  box.hidden = false;
  box.append(el('h4', { text: 'Modelled PM2.5 (µg/m³)' }));
  const bands = feats
    .map((f) => bandValue(f))
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);
  for (const b of bands) {
    box.append(el('div', { class: 'legend-row' }, [
      el('span', { class: 'legend-swatch', style: `background:${cssVar(PLUME_BAND_COLOR[b] || '--plume-55', '#f5a623')}` }),
      el('span', { text: `≥ ${b}` }),
    ]));
  }
}

/* ------------------------------------------------------------- year slider */

function wireYearSlider() {
  const slider = $('#year-slider');
  const play = $('#year-play');
  if (!slider) return;

  slider.addEventListener('input', () => {
    const y = years[Number(slider.value)];
    if (y != null) {
      $('#year-out').textContent = String(y);
      setMapYear(y);
    }
  });

  play.addEventListener('click', () => {
    if (playTimer) { stopPlay(); return; }
    play.setAttribute('aria-pressed', 'true');
    $('.btn__icon', play).textContent = '❚❚';
    $('.btn__label', play).textContent = 'Pause';
    playTimer = setInterval(() => {
      const next = (Number(slider.value) + 1) % years.length;
      slider.value = String(next);
      slider.dispatchEvent(new Event('input'));
      if (next === years.length - 1) stopPlay();
    }, REDUCED_MOTION ? 2200 : 1250);
  });
}

function stopPlay() {
  clearInterval(playTimer);
  playTimer = null;
  const play = $('#year-play');
  if (!play) return;
  play.setAttribute('aria-pressed', 'false');
  $('.btn__icon', play).textContent = '▶';
  $('.btn__label', play).textContent = 'Replay years';
}

/** Load and show a year. Exported so the history chart can drive it. */
export async function setMapYear(year, { scroll = false } = {}) {
  if (!map || year == null) return;
  currentYear = year;
  const idx = years.indexOf(year);
  const slider = $('#year-slider');
  if (idx >= 0 && slider) slider.value = String(idx);
  $('#year-out').textContent = String(year);

  if (scroll) {
    document.getElementById('map-section')
      .scrollIntoView({ behavior: REDUCED_MOTION ? 'auto' : 'smooth', block: 'start' });
  }

  const busy = $('#year-loading');
  if (busy) busy.hidden = false;
  try {
    let payload = clusterCache.get(year);
    if (!payload) {
      payload = await api(`/api/history/${year}`);
      clusterCache.set(year, payload);
    }
    if (currentYear !== year) return;   // a newer request won
    drawFires(payload.clusters, payload.season);
    if ($('#plume-toggle').checked) refreshPlume();
  } catch (e) {
    console.error('year load failed', e);
    fireLayer.clearLayers();
    const dl = $('#year-summary');
    dl.textContent = '';
    dl.append(el('dt', { text: `${year} unavailable` }),
      el('dd', { text: describeError(e, `${year} fire clusters`) }));
  } finally {
    if (busy) busy.hidden = true;
  }
}

/* --------------------------------------------------------------- dispersion */

function windDefaults() {
  const m = ctx.meteo;
  if (!m || !Array.isArray(m.wind_speed_ms) || !m.wind_speed_ms.length) return null;
  const n = Math.min(5, m.wind_speed_ms.length);
  const spd = m.wind_speed_ms.slice(0, n).reduce((a, b) => a + b, 0) / n;
  // circular mean of direction
  let sx = 0, sy = 0;
  for (let i = 0; i < n; i++) {
    const r = (m.wind_dir_deg[i] || 0) * Math.PI / 180;
    sx += Math.sin(r); sy += Math.cos(r);
  }
  let dir = Math.atan2(sx, sy) * 180 / Math.PI;
  if (dir < 0) dir += 360;
  return { speed: Number(spd.toFixed(1)), dir: Math.round(dir / 10) * 10 };
}

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

function compass(deg) { return COMPASS[Math.round((deg % 360) / 22.5) % 16]; }

function wirePlume() {
  const toggle = $('#plume-toggle');
  const controls = $('#plume-controls');
  const dirEl = $('#wind-dir');
  const spdEl = $('#wind-speed');

  const d = windDefaults();
  if (d) { dirEl.value = String(d.dir); spdEl.value = String(d.speed); }

  const syncLabels = () => {
    $('#wind-dir-out').textContent = `${dirEl.value}° ${compass(Number(dirEl.value))}`;
    $('#wind-speed-out').textContent = `${fmtNum(Number(spdEl.value), 1)} m/s`;
  };
  syncLabels();

  toggle.addEventListener('change', () => {
    controls.hidden = !toggle.checked;
    if (toggle.checked) {
      refreshPlume();
    } else {
      plumeLayer.clearLayers();
      drawReceptors(null);
      renderPlumeLegend(null);
      setPanelState(panel('dispersion'), 'ok');
    }
  });

  const onWind = debounce(() => { if (toggle.checked) refreshPlume(); }, 350);
  for (const input of [dirEl, spdEl]) {
    input.addEventListener('input', () => { syncLabels(); onWind(); });
  }
}

function sourcesFromCurrentYear() {
  const payload = clusterCache.get(currentYear);
  const feats = (payload && payload.clusters && payload.clusters.features) || [];
  const pts = [];
  for (const f of feats) {
    if (!f.geometry || f.geometry.type !== 'Point') continue;
    const [lon, lat] = f.geometry.coordinates;
    pts.push({
      lat, lon,
      area_ha: Number((f.properties && f.properties.area_ha) || 0),
      pool: (f.properties && f.properties.pool) || 'peat_drained',
    });
  }
  // biggest fires dominate the emission total; cap the payload size
  pts.sort((a, b) => b.area_ha - a.area_ha);
  return pts.slice(0, 150);
}

async function refreshPlume() {
  const p = panel('dispersion');
  const sources = sourcesFromCurrentYear();
  if (!sources.length) {
    setPanelState(p, 'error', 'No point-geometry fire clusters for this year, so there is nothing to disperse.');
    return;
  }
  setPanelState(p, 'loading');
  try {
    const res = await api('/api/dispersion', {
      method: 'POST',
      body: {
        sources,
        wind_speed_ms: Number($('#wind-speed').value),
        wind_dir_deg: Number($('#wind-dir').value),
        mixing_height_m: 800,
        rain_mm: 0,
      },
    });
    drawPlume(res);
    drawReceptors(res);
    renderReceptorTable(res);
    renderPlumeLegend(res);
    if (Array.isArray(res.caveats) && res.caveats.length) {
      document.dispatchEvent(new CustomEvent('wwhe:caveats', {
        detail: { label: 'Dispersion model — /api/dispersion', caveats: res.caveats },
      }));
    }
    setPanelState(p, 'ok');
  } catch (e) {
    console.error('dispersion failed', e);
    plumeLayer.clearLayers();
    drawReceptors(null);
    setPanelState(p, 'error', describeError(e, 'dispersion model'));
  }
}

function drawPlume(res) {
  plumeLayer.clearLayers();
  const fc = res && res.plume;
  if (!fc || !fc.features) return;
  const bands = fc.features.slice().sort((a, b) => bandValue(a) - bandValue(b));
  for (const f of bands) {
    const band = bandValue(f);
    const color = cssVar(PLUME_BAND_COLOR[band] || '--plume-55', '#f5a623');
    plumeLayer.addLayer(L.geoJSON(f, {
      style: { color, weight: 0.8, opacity: 0.28, fillColor: color, fillOpacity: 0.13 },
      interactive: false,
    }));
  }
  plumeLayer.eachLayer((l) => l.bringToBack && l.bringToBack());
}

function renderReceptorTable(res) {
  const tbody = $('#receptor-table tbody');
  if (!tbody) return;
  tbody.textContent = '';
  const rows = (res.receptors || []).slice()
    .sort((a, b) => (b.pm25_ugm3 || 0) - (a.pm25_ugm3 || 0));
  for (const r of rows) {
    tbody.append(el('tr', {}, [
      el('td', { text: r.name }),
      el('td', { text: fmtNum(r.pm25_ugm3, 1) }),
      el('td', {}, [
        el('span', {
          class: 'aqi-pill',
          style: `background:${COLOR.aqi(r.category)}`,
          text: r.category || '—',
        }),
      ]),
    ]));
  }
}
