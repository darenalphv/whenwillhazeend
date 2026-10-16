/**
 * basemap.js — the light terrain the haze sits over.
 *
 * OpenFreeMap positron (no key) restyled toward the site's paper palette,
 * with AWS terrarium terrain (sea clamped to zero, see elevation.js),
 * hillshade, emphasised national borders, and our own labels for the places
 * this story is about. If the vector style cannot be fetched, the Esri World
 * Light Gray Canvas raster is the fallback the contract names.
 */

import { demUrl } from './elevation.js';

export const POSITRON = 'https://tiles.openfreemap.org/styles/positron';
const ESRI_LIGHT = 'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}';

const PAL = {
  land: '#EEE9DF',
  water: '#CAD7DC',
  border: '#6E6457',
  shadow: '#5C4A36',
  highlight: '#FFFBF2',
  accent: '#8C7656',
};

/* Places the story turns on. Coordinates are geographic facts, not data. */
const LABELS = {
  type: 'FeatureCollection',
  features: [
    ['country', 'MALAYSIA', 102.1, 4.5],
    ['country', 'MALAYSIA', 113.6, 3.05],
    ['country', 'SINGAPORE', 103.83, 1.15],
    ['country', 'BRUNEI', 114.75, 4.72],
    ['country', 'INDONESIA', 112.6, -3.9],
    ['region', 'Sarawak', 112.6, 2.25],
    ['region', 'Sabah', 117.0, 5.3],
    ['region', 'Kalimantan', 114.8, -0.4],
    ['region', 'Sumatra', 101.6, -0.9],
    ['relief', 'Schwaner Mts', 112.6, -0.75],
    ['relief', 'Müller Mts', 114.0, 0.95],
    ['relief', 'Meratus Mts', 115.55, -2.75],
    ['relief', 'Kinabalu 4,095 m', 116.56, 6.12],
    ['lowland', 'Peat plains — Kalimantan Tengah', 113.3, -2.95],
  ].map(([kind, name, lon, lat]) => ({
    type: 'Feature',
    properties: { kind, name },
    geometry: { type: 'Point', coordinates: [lon, lat] },
  })),
};

function terrainSources(style) {
  style.sources.dem = {
    type: 'raster-dem', tiles: [demUrl()], encoding: 'terrarium',
    tileSize: 256, maxzoom: 11,
    attribution: 'Terrain: <a href="https://registry.opendata.aws/terrain-tiles/">AWS Terrain Tiles</a> (Mapzen)',
  };
  style.sources['dem-hs'] = {
    type: 'raster-dem', tiles: [demUrl()], encoding: 'terrarium', tileSize: 256, maxzoom: 11,
  };
}

function hillshadeLayer() {
  return {
    id: 'haze-hillshade',
    type: 'hillshade',
    source: 'dem-hs',
    paint: {
      'hillshade-exaggeration': 0.62,
      'hillshade-shadow-color': PAL.shadow,
      'hillshade-highlight-color': PAL.highlight,
      'hillshade-accent-color': PAL.accent,
      'hillshade-illumination-direction': 315,
    },
  };
}

function labelLayers() {
  const halo = 'rgba(247,244,238,0.92)';
  return [
    {
      id: 'haze-label-country', type: 'symbol', source: 'haze-labels',
      filter: ['==', ['get', 'kind'], 'country'],
      layout: {
        'text-field': ['get', 'name'], 'text-font': ['Noto Sans Bold'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 4, 11, 8, 15],
        'text-letter-spacing': 0.22, 'text-allow-overlap': false,
      },
      paint: { 'text-color': '#3D3630', 'text-halo-color': halo, 'text-halo-width': 1.6 },
    },
    {
      id: 'haze-label-region', type: 'symbol', source: 'haze-labels',
      filter: ['==', ['get', 'kind'], 'region'],
      layout: {
        'text-field': ['get', 'name'], 'text-font': ['Noto Sans Italic'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 4, 11, 8, 14],
        'text-letter-spacing': 0.06,
      },
      paint: { 'text-color': '#62584F', 'text-halo-color': halo, 'text-halo-width': 1.4 },
    },
    {
      id: 'haze-label-relief', type: 'symbol', source: 'haze-labels',
      filter: ['in', ['get', 'kind'], ['literal', ['relief', 'lowland']]],
      minzoom: 4.6,
      layout: {
        'text-field': ['get', 'name'], 'text-font': ['Noto Sans Italic'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 5, 11, 8, 13],
      },
      paint: {
        'text-color': ['match', ['get', 'kind'], 'lowland', '#1F6B85', '#6B563C'],
        'text-halo-color': halo, 'text-halo-width': 1.3,
      },
    },
  ];
}

function restylePositron(style) {
  const keepSymbols = new Set(['label_city_capital', 'label_city', 'water_name_point_label']);
  style.layers = style.layers.filter((l) => {
    if (l.type === 'symbol' && !keepSymbols.has(l.id)) return false;   // ours are clearer
    if (/^(building|highway_minor|highway_path|road_|aeroway|railway|tunnel_)/.test(l.id)) return false;
    return true;
  });
  for (const l of style.layers) {
    if (l.id === 'background') l.paint = { 'background-color': PAL.land };
    if (l.id === 'water') l.paint = { ...(l.paint || {}), 'fill-color': PAL.water, 'fill-opacity': 1 };
    if (l.id === 'waterway') l.paint = { ...(l.paint || {}), 'line-color': '#B9C9CF' };
    if (l.id === 'boundary_2') {
      l.paint = {
        'line-color': PAL.border,
        'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.9, 8, 2.2],
        'line-opacity': 0.85,
      };
    }
    if (l.id === 'boundary_3') l.paint = { ...(l.paint || {}), 'line-opacity': 0.35 };
    if (l.id === 'label_city' || l.id === 'label_city_capital') {
      l.minzoom = 6.5;
      l.paint = { ...(l.paint || {}), 'text-color': '#62584F', 'text-halo-color': 'rgba(247,244,238,0.9)' };
    }
  }
  // hillshade sits over land/landcover, under water lines, borders and labels
  const at = style.layers.findIndex((l) => l.id === 'waterway');
  style.layers.splice(at < 0 ? 1 : at, 0, hillshadeLayer());
  style.sources['haze-labels'] = { type: 'geojson', data: LABELS };
  style.layers.push(...labelLayers());
  return style;
}

function esriStyle() {
  return {
    version: 8,
    glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    sources: {
      esri: {
        type: 'raster', tiles: [ESRI_LIGHT], tileSize: 256, maxzoom: 16,
        attribution: 'Basemap: Esri, HERE, Garmin, © OpenStreetMap contributors',
      },
      'haze-labels': { type: 'geojson', data: LABELS },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': PAL.land } },
      { id: 'esri', type: 'raster', source: 'esri', paint: { 'raster-saturation': -0.4 } },
      hillshadeLayer(),
      ...labelLayers(),
    ],
  };
}

/**
 * Build the style object. Resolves { style, fallback: bool, reason }.
 * Never rejects: the raster fallback always exists.
 */
export async function buildStyle({ vex, timeoutMs = 9000, labels = true }) {
  let style;
  let fallback = false;
  let reason = null;
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    const res = await fetch(POSITRON, { signal: ctl.signal });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    style = restylePositron(await res.json());
  } catch (err) {
    style = esriStyle();
    fallback = true;
    reason = `OpenFreeMap style unavailable (${err.name === 'AbortError' ? 'timed out' : err.message}); showing the Esri light-grey raster instead.`;
  }
  terrainSources(style);
  if (!labels) style.layers = style.layers.filter((l) => !l.id.startsWith('haze-label'));
  style.terrain = { source: 'dem', exaggeration: vex };
  style.sky = skySpec(0);
  return { style, fallback, reason };
}

/**
 * Sky and fog for a 0..1 haze factor. Clean air: pale blue-grey horizon,
 * light fog far off. Heavy haze: the horizon goes ochre and the fog walks in
 * toward the camera — the scene itself gets hazier as the haze worsens.
 */
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const css = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
export function skySpec(h) {
  const t = Math.max(0, Math.min(1, h));
  return {
    'sky-color': css(mix([200, 216, 226], [214, 196, 160], t)),
    'horizon-color': css(mix([234, 238, 236], [222, 200, 158], t)),
    'fog-color': css(mix([236, 236, 230], [208, 186, 146], t)),
    'sky-horizon-blend': 0.6 + 0.3 * t,
    'horizon-fog-blend': 0.5 + 0.4 * t,
    'fog-ground-blend': 0.85 - 0.6 * t,
    'atmosphere-blend': 0.5,
  };
}
