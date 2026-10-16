/* map.js — MapLibre: province peat choropleth, year-by-year fire replay with
 * date-ordered ignition and peat smoulder, dispersion plume, receptor cities.
 *
 * Basemap: OpenFreeMap positron (vector, no key), re-coloured to sit on paper.
 * If that style cannot be fetched, Esri World Light Gray Canvas (raster, no
 * key) is used instead and the map says so. All data geometry comes from the API.
 */

import {
  api, REDUCED_MOTION, describeError,
  $, el, panel, setPanelState, fmtDate, fmtInt, fmtNum, fmtSig,
  ensoLabel, titleCase, clamp, COLOR, POOL_ORDER, debounce, paintRange, rangeOf, mixHex,
} from './util.js';

const POSITRON = 'https://tiles.openfreemap.org/styles/positron';
const OFM_GLYPHS = 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf';
const ESRI = 'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas';

let map = null;
let mapReady = null;
let years = [];
let currentYear = null;
let playTimer = null;
let firstLabelLayer = undefined;
let usingFallback = false;
let mapVisible = true;
let smoulderRaf = 0;
let igniteRaf = 0;
const clusterCache = new Map();
const popup = () => new maplibregl.Popup({ closeButton: true, maxWidth: '290px', className: 'map-pop-wrap' });

let ctx = { regions: null, fuel: null, receptors: null, history: null, meteo: null };

const PLUME_BAND_VAR = { 35: '--plume-35', 55: '--plume-55', 150: '--plume-150', 250: '--plume-250' };

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
function bandValue(f) {
  const p = (f && f.properties) || {};
  return Number(p.pm25_ugm3 != null ? p.pm25_ugm3 : p.band_ugm3);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const row = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;

/* --------------------------------------------------------------- the style */

async function fetchJSON(url, ms) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

/** Positron, re-inked: warm paper land, a cool pale sea, ink labels. */
function restyle(style) {
  const land = cssVar('--map-land', '#F2EDE3');
  const water = cssVar('--map-water', '#D5DEDE');
  const label = cssVar('--map-label', '#4A433C');
  const line = cssVar('--line-2', '#C9BFB0');
  for (const l of style.layers) {
    const p = l.paint || (l.paint = {});
    if (l.type === 'background') p['background-color'] = land;
    else if (l.id === 'water') p['fill-color'] = water;
    else if (l.id === 'waterway') p['line-color'] = water;
    else if (l.type === 'fill' && /park|landcover|landuse/.test(l.id)) p['fill-color'] = '#ECE6DA';
    else if (l.type === 'line' && /boundary/.test(l.id)) p['line-color'] = line;
    else if (l.type === 'line' && /highway|road|railway|tunnel|bridge/.test(l.id)) {
      p['line-opacity'] = 0.55;
    } else if (l.type === 'symbol') {
      if ('text-color' in p || (l.layout && l.layout['text-field'])) {
        p['text-color'] = label;
        p['text-halo-color'] = land;
        p['text-halo-width'] = 1.2;
      }
    }
  }
  return style;
}

function fallbackStyle() {
  return {
    version: 8,
    glyphs: OFM_GLYPHS,
    sources: {
      esri: {
        type: 'raster', tileSize: 256, maxzoom: 16,
        tiles: [`${ESRI}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`],
        attribution: 'Tiles &copy; Esri — Esri, DeLorme, NAVTEQ',
      },
      esriRef: {
        type: 'raster', tileSize: 256, maxzoom: 16,
        tiles: [`${ESRI}/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`],
      },
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': cssVar('--map-land', '#F2EDE3') } },
      { id: 'esri', type: 'raster', source: 'esri', paint: { 'raster-saturation': -0.25, 'raster-contrast': -0.05 } },
      { id: 'esri-ref', type: 'raster', source: 'esriRef', paint: { 'raster-opacity': 0.85 } },
    ],
  };
}

async function chooseStyle() {
  try {
    const s = await fetchJSON(POSITRON, 7000);
    usingFallback = false;
    return restyle(s);
  } catch (e) {
    console.info('OpenFreeMap style unavailable, using Esri Light Gray', e.message || e);
    usingFallback = true;
    return fallbackStyle();
  }
}

function bboxOf(fc) {
  let w = 180, s = 90, e = -180, n = -90;
  const walk = (c) => {
    if (typeof c[0] === 'number') {
      w = Math.min(w, c[0]); e = Math.max(e, c[0]); s = Math.min(s, c[1]); n = Math.max(n, c[1]);
    } else c.forEach(walk);
  };
  for (const f of (fc && fc.features) || []) if (f.geometry) walk(f.geometry.coordinates);
  return w < e ? [[w, s], [e, n]] : [[108.5, -4.4], [119.2, 4.5]];
}

/* -------------------------------------------------------------------- init */

export async function initMap(data) {
  ctx = { ...ctx, ...data };
  setPanelState(panel('map'), 'ok');           // MapLibre needs a laid-out container
  if (!window.maplibregl) throw new Error('MapLibre failed to load from the CDN');

  if (map) { map.remove(); map = null; }
  const style = await chooseStyle();

  map = new maplibregl.Map({
    container: 'map',
    style,
    bounds: bboxOf(ctx.regions),
    fitBoundsOptions: { padding: 24 },
    minZoom: 3.5,
    maxZoom: 10,
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    cooperativeGestures: true,
    attributionControl: { compact: true },
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

  const note = $('#map-basemap-note');
  if (note) {
    note.hidden = !usingFallback;
    note.textContent = usingFallback ? 'Basemap: Esri Light Gray (the vector basemap did not load)' : '';
  }

  // Tile and glyph errors are not fatal; only a map that never loads is.
  map.on('error', (e) => console.info('map resource error', (e && e.error && e.error.message) || e));
  // 'load' waits for a rendered frame, which a background tab never draws, so
  // a loaded style is enough to add layers; only a style that never arrives fails.
  mapReady = new Promise((resolve, reject) => {
    let done = false;
    const finish = () => { if (!done) { done = true; clearInterval(poll); clearTimeout(t); resolve(); } };
    map.once('load', finish);
    map.once('style.load', finish);
    const poll = setInterval(() => { if (map.isStyleLoaded()) finish(); }, 250);
    const t = setTimeout(() => {
      if (map.isStyleLoaded()) finish();
      else { done = true; clearInterval(poll); reject(new Error('the basemap style did not load within 20 s')); }
    }, 20000);
  });
  await mapReady;

  const layers = map.getStyle().layers || [];
  const sym = layers.find((l) => l.type === 'symbol');
  firstLabelLayer = sym ? sym.id : undefined;

  addDataLayers();
  wireInteractions();
  buildLegend();
  wireYearSlider();
  wirePlume();
  watchVisibility();

  const seasons = (ctx.history && ctx.history.seasons) || [];
  years = seasons.map((s) => s.year).sort((a, b) => a - b);
  buildYearBars(seasons);
  const slider = $('#year-slider');
  if (years.length) {
    slider.min = '0';
    slider.max = String(years.length - 1);
    slider.value = String(years.length - 1);
    slider.disabled = false;
    $('#year-first').textContent = String(years[0]);
    $('#year-last').textContent = String(years[years.length - 1]);
    paintRange(slider);
    await setMapYear(years[years.length - 1]);
  } else {
    $('#year-out').textContent = '—';
    slider.disabled = true;
    $('#year-summary').append(el('dt', { text: 'Seasons' }),
      el('dd', { text: 'The history endpoint did not answer, so there are no years to replay.' }));
    for (const id of ['#year-prev', '#year-next', '#year-play']) $(id).disabled = true;
  }
}

/** Optional live wind, arriving after the map: pre-fills the plume sliders. */
export function applyMeteo(m) {
  ctx.meteo = m;
  const d = windDefaults();
  const toggle = $('#plume-toggle');
  if (!d || !toggle || toggle.checked) return;
  $('#wind-dir').value = String(d.dir);
  $('#wind-speed').value = String(d.speed);
  syncWindLabels();
}

/* ------------------------------------------------------------ data layers */

function addDataLayers() {
  const features = (ctx.regions && ctx.regions.features) || [];
  const peats = features.map((f) => Number(f.properties.peat_mha) || 0);
  const min = Math.min(...peats, 0), max = Math.max(...peats, 0.01);
  const lo = cssVar('--peat-lo', '#EFE7D8'), hi = cssVar('--peat-hi', '#8A5A2E');

  map.addSource('regions', { type: 'geojson', data: ctx.regions, promoteId: 'name' });
  map.addLayer({
    id: 'regions-fill', type: 'fill', source: 'regions',
    paint: {
      'fill-color': ['interpolate', ['linear'], ['coalesce', ['get', 'peat_mha'], 0],
        min, lo, min + (max - min) * 0.5, mixHex(lo, hi, 0.55), max, hi],
      'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.86, 0.7],
    },
  }, firstLabelLayer);
  map.addLayer({
    id: 'regions-line', type: 'line', source: 'regions',
    paint: {
      'line-color': ['case', ['boolean', ['feature-state', 'hover'], false], cssVar('--ink', '#1C1815'), cssVar('--ink-3', '#62584F')],
      'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 1.8, 0.8],
    },
  }, firstLabelLayer);

  map.addSource('plume', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  const bandColor = ['match', ['to-number', ['coalesce', ['get', 'pm25_ugm3'], ['get', 'band_ugm3'], 0]]];
  for (const [b, v] of Object.entries(PLUME_BAND_VAR)) bandColor.push(Number(b), cssVar(v, '#C3833A'));
  bandColor.push(cssVar('--plume-55', '#C3833A'));
  map.addLayer({
    id: 'plume-fill', type: 'fill', source: 'plume',
    paint: { 'fill-color': bandColor, 'fill-opacity': 0.26 },
  });
  map.addLayer({
    id: 'plume-line', type: 'line', source: 'plume',
    paint: { 'line-color': bandColor, 'line-width': 1, 'line-opacity': 0.7 },
  });

  map.addSource('fires', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'fires-smoulder', type: 'circle', source: 'fires',
    filter: ['==', ['get', 'smoulder'], true],
    paint: {
      'circle-radius': radiusExpr(['*', ['get', 'r'], 1.55]),
      'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-color': cssVar('--ember', '#B43C0C'),
      'circle-stroke-width': 1.4,
      'circle-stroke-opacity': 0.4,
    },
  });
  map.addLayer({
    id: 'fires', type: 'circle', source: 'fires',
    paint: {
      'circle-radius': radiusExpr(['get', 'r']),
      'circle-color': ['get', 'color'],
      'circle-opacity': 0.72,
      'circle-stroke-color': cssVar('--paper-hi', '#FFFFFF'),
      'circle-stroke-width': 0.8,
      'circle-stroke-opacity': 0.9,
    },
  });

  map.addSource('receptors', { type: 'geojson', data: receptorFC(null) });
  map.addLayer({
    id: 'receptors', type: 'circle', source: 'receptors',
    paint: {
      'circle-radius': ['get', 'size'],
      'circle-color': ['get', 'color'],
      'circle-opacity': ['get', 'fillOpacity'],
      'circle-stroke-color': cssVar('--ink', '#1C1815'),
      'circle-stroke-width': 1.5,
    },
  });
  map.addLayer({
    id: 'receptor-labels', type: 'symbol', source: 'receptors',
    layout: {
      'text-field': ['get', 'label'],
      'text-font': ['Noto Sans Regular'],
      'text-size': 12,
      'text-offset': [0, 1.1],
      'text-anchor': 'top',
      'text-optional': true,
    },
    paint: {
      'text-color': cssVar('--ink', '#1C1815'),
      'text-halo-color': cssVar('--paper-hi', '#FFFFFF'),
      'text-halo-width': 1.6,
    },
  });
}

/** Radius that scales with zoom; `base` is the size at zoom 6. */
function radiusExpr(base) {
  return ['interpolate', ['linear'], ['zoom'],
    4, ['*', base, 0.55],
    6, base,
    9, ['*', base, 2.6]];
}

function radiusFor(areaHa) {
  const a = Math.max(1, Number(areaHa) || 1);
  return clamp(1.8 + Math.sqrt(a) / 9, 2.2, 22);
}

/* ------------------------------------------------------------ interaction */

let hoverId = null;

function wireInteractions() {
  for (const id of ['regions-fill', 'fires', 'receptors']) {
    map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
  }
  map.on('mousemove', 'regions-fill', (e) => {
    const f = e.features && e.features[0];
    if (!f) return;
    if (hoverId !== null && hoverId !== f.id) map.setFeatureState({ source: 'regions', id: hoverId }, { hover: false });
    hoverId = f.id;
    map.setFeatureState({ source: 'regions', id: hoverId }, { hover: true });
  });
  map.on('mouseleave', 'regions-fill', () => {
    if (hoverId !== null) map.setFeatureState({ source: 'regions', id: hoverId }, { hover: false });
    hoverId = null;
  });

  map.on('click', (e) => {
    const hits = map.queryRenderedFeatures(e.point, { layers: ['receptors', 'fires', 'regions-fill'] });
    if (!hits.length) return;
    const f = hits[0];
    const p = f.properties || {};
    let html = '';
    if (f.layer.id === 'receptors') {
      html = `<h4>${escapeHtml(p.name)}</h4><dl>`
        + (p.country ? row('Country', escapeHtml(p.country)) : '')
        + (p.population ? row('Population', fmtInt(Number(p.population))) : '')
        + (p.pm25 !== undefined && p.pm25 !== null && p.pm25 !== ''
          ? row('Modelled PM2.5', `${fmtNum(Number(p.pm25), 1)} µg/m³`) + row('Category', escapeHtml(p.category || '—'))
            + (p.arrival ? row('Arrival', `${fmtNum(Number(p.arrival), 1)} h`) : '')
          : row('Modelled PM2.5', 'plume off'))
        + '</dl>';
    } else if (f.layer.id === 'fires') {
      html = `<h4>${escapeHtml(titleCase(p.pool || 'fire'))}</h4><dl>`
        + row('Date', fmtDate(p.date))
        + row('Area', `${fmtInt(Number(p.area_ha))} ha`)
        + row('Burned for', p.duration_days ? `${fmtInt(Number(p.duration_days))} days` : '—')
        + row('FRP', p.frp_mw ? `${fmtInt(Number(p.frp_mw))} MW` : '—')
        + row('Province', escapeHtml(p.province || '—'))
        + '</dl>'
        + (String(p.estimated) === 'true' ? '<p class="map-pop__note">Synthesised cluster, not an observed fire. See Methods.</p>' : '');
    } else {
      html = `<h4>${escapeHtml(p.name || 'Province')}</h4><dl>`
        + row('Peat', `${fmtNum(Number(p.peat_mha), 2)} Mha`)
        + row('Land area', `${fmtNum(Number(p.area_mha), 1)} Mha`)
        + row('Population', fmtInt(Number(p.population)))
        + row('Capital', escapeHtml(p.capital || '—'))
        + '</dl>';
    }
    popup().setLngLat(e.lngLat).setHTML(`<div class="map-pop">${html}</div>`).addTo(map);
  });
}

function watchVisibility() {
  const holder = $('#map');
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(([e]) => {
      mapVisible = e.isIntersecting;
      if (mapVisible) startSmoulder(); else stopSmoulder();
    }, { threshold: 0 }).observe(holder);
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopSmoulder(); else if (mapVisible) startSmoulder();
  });
}

/* ------------------------------------------------------------------- fires */

/* Ignition timing ---------------------------------------------------------
 * A season builds from the first dry-season ignitions to the peak, so each
 * cluster lights in date order. Afterwards the biggest peat clusters keep a
 * slow breathing ring: peat goes on smouldering after the flaming front has
 * moved on, which is the thing the whole model rests on.                   */
const IGNITE_WINDOW_MS = 1700;
const FADE_MS = 320;
const SMOULDER_CAP = 60;
const PEAT_POOLS = new Set(['peat_drained', 'peat_intact']);

function prepareFires(geojson) {
  const feats = ((geojson && geojson.features) || []).filter((f) => f && f.geometry);
  const pts = [];
  for (const f of feats) {
    if (f.geometry.type === 'Point') pts.push(f);
    else if (f.geometry.type === 'Polygon' || f.geometry.type === 'MultiPolygon') {
      // the API may one day send real burn scars; place them by centroid of the bbox
      const [[w, s], [e, n]] = bboxOf({ features: [f] });
      pts.push({ ...f, geometry: { type: 'Point', coordinates: [(w + e) / 2, (s + n) / 2] } });
    }
  }
  const times = pts.map((f) => Date.parse((f.properties || {}).date)).filter(Number.isFinite);
  const t0 = times.length ? Math.min(...times) : 0;
  const span = times.length ? Math.max(...times) - t0 : 0;
  const smoulder = new Set(pts
    .filter((f) => PEAT_POOLS.has((f.properties || {}).pool))
    .sort((a, b) => (b.properties.area_ha || 0) - (a.properties.area_ha || 0))
    .slice(0, SMOULDER_CAP));
  let k = 0;
  return {
    type: 'FeatureCollection',
    features: pts.map((f) => {
      const p = f.properties || {};
      const t = Date.parse(p.date);
      const frac = Number.isFinite(t) && span ? (t - t0) / span : 0.5;
      k += 1;
      return {
        type: 'Feature',
        geometry: f.geometry,
        properties: {
          ...p,
          r: radiusFor(p.area_ha),
          color: COLOR.pool(p.pool),
          ign: Math.round(frac * IGNITE_WINDOW_MS),
          smoulder: smoulder.has(f),
          phase: (k * 2.399) % 6.283,
        },
      };
    }),
  };
}

function drawFires(geojson, season) {
  const fc = prepareFires(geojson);
  map.getSource('fires').setData(fc);
  renderYearSummary(season, (geojson && geojson.features) || []);
  ignite();
}

/** Lights clusters in season order. Reduced motion: one short fade, all at once. */
function ignite() {
  cancelAnimationFrame(igniteRaf);
  stopSmoulder();
  if (REDUCED_MOTION) {
    const t0 = performance.now();
    const step = (now) => {
      const k = clamp((now - t0) / 120, 0, 1);
      map.setPaintProperty('fires', 'circle-opacity', 0.72 * k);
      map.setPaintProperty('fires-smoulder', 'circle-stroke-opacity', 0.4 * k);
      if (k < 1) igniteRaf = requestAnimationFrame(step);
    };
    igniteRaf = requestAnimationFrame(step);
    return;
  }
  const t0 = performance.now();
  const total = IGNITE_WINDOW_MS + FADE_MS + 200;
  map.setPaintProperty('fires-smoulder', 'circle-stroke-opacity', 0);
  const step = (now) => {
    const clock = now - t0;
    const age = ['-', clock, ['get', 'ign']];
    map.setPaintProperty('fires', 'circle-opacity',
      ['interpolate', ['linear'], age, 0, 0, FADE_MS, 0.72]);
    map.setPaintProperty('fires', 'circle-radius', radiusExpr(['*', ['get', 'r'],
      ['interpolate', ['linear'], age, 0, 0.2, FADE_MS * 0.6, 1.35, FADE_MS * 1.4, 1]]));
    if (clock < total) {
      igniteRaf = requestAnimationFrame(step);
    } else {
      map.setPaintProperty('fires', 'circle-opacity', 0.72);
      map.setPaintProperty('fires', 'circle-radius', radiusExpr(['get', 'r']));
      startSmoulder();
    }
  };
  igniteRaf = requestAnimationFrame(step);
}

/* The smoulder loop: ~15 updates a second is plenty for a slow breath. */
function startSmoulder() {
  if (!map || REDUCED_MOTION || smoulderRaf || !mapVisible || document.hidden) return;
  if (igniteRaf && !map.getPaintProperty) return;
  let last = 0;
  const t0 = performance.now();
  const step = (now) => {
    smoulderRaf = requestAnimationFrame(step);
    if (now - last < 66) return;
    last = now;
    const t = (now - t0) / 1000 * 1.1;
    const s = ['sin', ['+', t, ['get', 'phase']]];
    map.setPaintProperty('fires-smoulder', 'circle-radius',
      radiusExpr(['*', ['get', 'r'], ['+', 1.55, ['*', 0.3, s]]]));
    map.setPaintProperty('fires-smoulder', 'circle-stroke-opacity', ['+', 0.32, ['*', 0.2, s]]);
  };
  smoulderRaf = requestAnimationFrame(step);
}

function stopSmoulder() {
  if (smoulderRaf) cancelAnimationFrame(smoulderRaf);
  smoulderRaf = 0;
}

function renderYearSummary(season, feats) {
  const dl = $('#year-summary');
  if (!dl) return;
  dl.textContent = '';
  if (!season) {
    dl.append(el('dt', { text: 'Season record' }), el('dd', { text: 'none for this year' }));
    return;
  }
  const totalHa = feats.reduce((a, f) => a + (Number(f.properties && f.properties.area_ha) || 0), 0);
  const kalR = rangeOf(season, 'burned_area_mha_kalimantan');
  const dR = rangeOf(season, 'excess_deaths');
  const rows = [
    ['ENSO phase', `${ensoLabel(season.enso_phase)}${season.oni_peak != null ? ` · ONI ${fmtSig(season.oni_peak)}` : ''}`],
    ['Season ran', season.start_date && season.end_date
      ? `${fmtDate(season.start_date, { withYear: false })} → ${fmtDate(season.end_date, { withYear: false })}` : '—'],
    ['Duration', season.duration_days != null ? `${fmtInt(season.duration_days)} days` : '—'],
    ['Burned, Kalimantan', kalR
      ? [`${fmtNum(kalR.low, 2)}–${fmtNum(kalR.high, 2)} Mha`, 'range of published estimates']
      : (season.burned_area_mha_kalimantan != null
        ? [`${fmtNum(season.burned_area_mha_kalimantan, 2)} Mha`, season.estimated ? 'estimated' : null] : '—')],
    ['Peat fraction', season.peat_fraction != null ? `${fmtInt(season.peat_fraction * 100)}%` : 'not reported'],
    ['Hotspots', season.hotspot_count != null ? fmtInt(season.hotspot_count) : 'not reported'],
    ['Clusters shown', [`${fmtInt(feats.length)} · ${fmtInt(totalHa)} ha`, 'synthesised, see Methods']],
  ];
  if (dR) rows.push(['Excess deaths', [`${fmtInt(dR.low)}–${fmtInt(dR.high)}`, 'studies disagree']]);
  else if (season.excess_deaths_est != null) rows.push(['Excess deaths', [fmtInt(season.excess_deaths_est), 'an estimate']]);
  for (const [k, v] of rows) {
    const dd = Array.isArray(v) ? el('dd', {}, [v[0], v[1] ? el('small', { text: v[1] }) : null]) : el('dd', { text: v });
    dl.append(el('dt', { text: k }), dd);
  }
}

/* --------------------------------------------------------------- receptors */

function receptorFC(dispersion) {
  const modelled = new Map(((dispersion && dispersion.receptors) || []).map((r) => [r.name, r]));
  return {
    type: 'FeatureCollection',
    features: (ctx.receptors || []).filter((r) => Number.isFinite(Number(r.lat))).map((r) => {
      const m = modelled.get(r.name);
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [Number(r.lon), Number(r.lat)] },
        properties: {
          name: r.name,
          country: r.country || '',
          population: r.population || null,
          pm25: m ? m.pm25_ugm3 : null,
          category: m ? m.category : '',
          arrival: m ? m.arrival_hours : null,
          color: m ? COLOR.aqi(m.category) : cssVar('--paper-hi', '#FFFFFF'),
          fillOpacity: m ? 0.9 : 1,
          size: m ? clamp(5 + Math.sqrt(m.pm25_ugm3 || 0) / 2.2, 5, 15) : 4,
          // basemap already names the cities; label only when there is a reading to add
          label: m ? `${r.name} · ${fmtNum(m.pm25_ugm3, 0)}` : '',
        },
      };
    }),
  };
}

function drawReceptors(dispersion) {
  const src = map && map.getSource('receptors');
  if (src) src.setData(receptorFC(dispersion));
}

/* ------------------------------------------------------------------ legend */

function buildLegend() {
  const box = $('#map-legend');
  if (!box) return;
  box.textContent = '';
  const features = (ctx.regions && ctx.regions.features) || [];
  const peats = features.map((f) => Number(f.properties.peat_mha) || 0);
  const min = Math.min(...peats, 0), max = Math.max(...peats, 0.01);
  const lo = cssVar('--peat-lo', '#EFE7D8'), hi = cssVar('--peat-hi', '#8A5A2E');

  box.append(el('div', { class: 'legend-group' }, [
    el('h4', { text: 'Province shading: peat area' }),
    el('div', { class: 'legend-row' }, [
      el('span', { class: 'mono', text: fmtNum(min, 1) }),
      el('span', { class: 'legend-ramp', style: `background:linear-gradient(90deg, ${lo}, ${mixHex(lo, hi, 0.55)}, ${hi})` }),
      el('span', { class: 'mono', text: `${fmtNum(max, 1)} Mha` }),
    ]),
    el('div', { class: 'legend-row', text: 'Circle size: area burned' }),
    el('div', { class: 'legend-row' }, [
      el('span', { class: 'legend-swatch legend-swatch--round', style: `background:transparent;border:1.5px solid ${COLOR.ember}` }),
      el('span', { text: REDUCED_MOTION ? 'Ring: largest peat fires, still smouldering' : 'Breathing ring: largest peat fires, still smouldering' }),
    ]),
  ]));

  const pools = (ctx.fuel && ctx.fuel.pools) || [];
  if (pools.length) {
    const group = el('div', { class: 'legend-group' }, [el('h4', { text: 'Fire cluster, by what was burning' })]);
    const ordered = [...pools].sort((a, b) => POOL_ORDER.indexOf(a.id) - POOL_ORDER.indexOf(b.id));
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
  box.append(el('h4', { text: 'Modelled PM2.5, µg/m³' }));
  const bands = [...new Set(feats.map(bandValue).filter(Number.isFinite))].sort((a, b) => a - b);
  for (const b of bands) {
    box.append(el('div', { class: 'legend-row' }, [
      el('span', { class: 'legend-swatch', style: `background:${cssVar(PLUME_BAND_VAR[b] || '--plume-55', '#C3833A')}` }),
      el('span', { text: `${b} and above` }),
    ]));
  }
}

/* ------------------------------------------------------------- year slider */

function buildYearBars(seasons) {
  const box = $('#year-bars');
  if (!box) return;
  box.textContent = '';
  const sorted = seasons.slice().sort((a, b) => a.year - b.year);
  const vals = sorted.map((s) => {
    const r = rangeOf(s, 'burned_area_mha_kalimantan');
    return r ? (r.low + r.high) / 2 : Number(s.burned_area_mha_kalimantan) || 0;
  });
  const max = Math.max(...vals, 0.001);
  const n1 = Math.max(1, sorted.length - 1);
  sorted.forEach((s, i) => {
    box.append(el('i', {
      'data-year': s.year,
      style: `left:${(i / n1) * 100}%;height:${Math.max(5, (vals[i] / max) * 100)}%;background:${COLOR.enso(s.enso_phase)}`,
    }));
  });
}

function markYearBar(year) {
  for (const b of document.querySelectorAll('#year-bars i')) {
    b.classList.toggle('is-current', Number(b.dataset.year) === year);
  }
}

function wireYearSlider() {
  const slider = $('#year-slider');
  const play = $('#year-play');
  if (!slider || slider.dataset.wired) return;
  slider.dataset.wired = '1';

  const go = (idx) => {
    const i = clamp(idx, 0, years.length - 1);
    slider.value = String(i);
    paintRange(slider);
    const y = years[i];
    if (y != null) setMapYear(y);
  };
  slider.addEventListener('input', () => go(Number(slider.value)));
  $('#year-prev').addEventListener('click', () => { stopPlay(); go(Number(slider.value) - 1); });
  $('#year-next').addEventListener('click', () => { stopPlay(); go(Number(slider.value) + 1); });

  play.addEventListener('click', () => {
    if (playTimer) { stopPlay(); return; }
    play.setAttribute('aria-pressed', 'true');
    $('.btn__label', play).textContent = 'Pause the replay';
    if (Number(slider.value) >= years.length - 1) go(0);
    playTimer = setInterval(() => {
      const next = Number(slider.value) + 1;
      if (next > years.length - 1) { stopPlay(); return; }
      go(next);
    }, REDUCED_MOTION ? 2200 : IGNITE_WINDOW_MS + 900);
  });
}

function stopPlay() {
  clearInterval(playTimer);
  playTimer = null;
  const play = $('#year-play');
  if (!play) return;
  play.setAttribute('aria-pressed', 'false');
  $('.btn__label', play).textContent = 'Replay every season';
}

/** Load and show a year. Exported so the history charts can drive it. */
export async function setMapYear(year, { scroll = false } = {}) {
  if (!map || year == null) return;
  currentYear = year;
  const idx = years.indexOf(year);
  const slider = $('#year-slider');
  if (idx >= 0 && slider) { slider.value = String(idx); paintRange(slider); }
  $('#year-out').textContent = String(year);
  $('#year-prev').disabled = idx <= 0;
  $('#year-next').disabled = idx >= years.length - 1;
  markYearBar(year);

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
    if (currentYear !== year) return;
    drawFires(payload.clusters, payload.season);
    if ($('#plume-toggle').checked) refreshPlume();
  } catch (e) {
    console.error('year load failed', e);
    map.getSource('fires').setData({ type: 'FeatureCollection', features: [] });
    const dl = $('#year-summary');
    dl.textContent = '';
    dl.append(el('dt', { text: `${year}` }), el('dd', { text: describeError(e, `${year} fire clusters`) }));
  } finally {
    if (busy && currentYear === year) busy.hidden = true;
  }
}

/* --------------------------------------------------------------- dispersion */

function windDefaults() {
  const m = ctx.meteo;
  if (!m || !Array.isArray(m.wind_speed_ms) || !m.wind_speed_ms.length) return null;
  const n = Math.min(5, m.wind_speed_ms.length);
  const spd = m.wind_speed_ms.slice(0, n).reduce((a, b) => a + b, 0) / n;
  let sx = 0, sy = 0;
  for (let i = 0; i < n; i += 1) {
    const r = ((m.wind_dir_deg && m.wind_dir_deg[i]) || 0) * Math.PI / 180;
    sx += Math.sin(r); sy += Math.cos(r);
  }
  let dir = Math.atan2(sx, sy) * 180 / Math.PI;
  if (dir < 0) dir += 360;
  return { speed: clamp(Math.round(spd * 2) / 2, 0.5, 12), dir: (Math.round(dir / 10) * 10) % 360 };
}

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const compass = (deg) => COMPASS[Math.round((deg % 360) / 22.5) % 16];

function syncWindLabels() {
  const dirEl = $('#wind-dir'), spdEl = $('#wind-speed');
  $('#wind-dir-out').textContent = `${dirEl.value}° ${compass(Number(dirEl.value))}`;
  $('#wind-speed-out').textContent = `${fmtNum(Number(spdEl.value), 1)} m/s`;
  paintRange(dirEl);
  paintRange(spdEl);
}

function wirePlume() {
  const toggle = $('#plume-toggle');
  if (toggle.dataset.wired) return;
  toggle.dataset.wired = '1';
  const controls = $('#plume-controls');
  const d = windDefaults();
  if (d) { $('#wind-dir').value = String(d.dir); $('#wind-speed').value = String(d.speed); }
  syncWindLabels();

  toggle.addEventListener('change', () => {
    controls.hidden = !toggle.checked;
    if (toggle.checked) refreshPlume();
    else {
      map.getSource('plume').setData({ type: 'FeatureCollection', features: [] });
      drawReceptors(null);
      renderPlumeLegend(null);
      setPanelState(panel('dispersion'), 'ok');
    }
  });
  const onWind = debounce(() => { if (toggle.checked) refreshPlume(); }, 350);
  for (const input of [$('#wind-dir'), $('#wind-speed')]) {
    input.addEventListener('input', () => { syncWindLabels(); onWind(); });
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
  pts.sort((a, b) => b.area_ha - a.area_ha);
  return pts.slice(0, 150);
}

let plumeToken = 0;

async function refreshPlume() {
  const p = panel('dispersion');
  const sources = sourcesFromCurrentYear();
  if (!sources.length) {
    setPanelState(p, 'error', 'No fire clusters with point locations for this year, so there is nothing to disperse.');
    return;
  }
  const token = ++plumeToken;
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
    if (token !== plumeToken) return;
    map.getSource('plume').setData(res.plume && res.plume.features ? res.plume : { type: 'FeatureCollection', features: [] });
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
    if (token !== plumeToken) return;
    console.error('dispersion failed', e);
    map.getSource('plume').setData({ type: 'FeatureCollection', features: [] });
    drawReceptors(null);
    setPanelState(p, 'error', describeError(e, 'dispersion model'));
  }
}

function renderReceptorTable(res) {
  const tbody = $('#receptor-table tbody');
  if (!tbody) return;
  tbody.textContent = '';
  const rows = (res.receptors || []).slice().sort((a, b) => (b.pm25_ugm3 || 0) - (a.pm25_ugm3 || 0));
  for (const r of rows) {
    tbody.append(el('tr', {}, [
      el('td', { text: r.name }),
      el('td', { class: 'num', text: fmtNum(r.pm25_ugm3, 1) }),
      el('td', {}, [el('span', { class: 'aqi-pill', style: `--aqi:${COLOR.aqi(r.category)}`, text: r.category || '—' })]),
    ]));
  }
}
