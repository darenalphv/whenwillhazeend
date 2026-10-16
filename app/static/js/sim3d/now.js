/**
 * now.js — "This week": real fires, the CAMS haze field and official readings.
 *
 * Reads the three M5 endpoints and normalises them into what the scene needs:
 *   GET /api/now/field     CAMS PM2.5 (modelled), 3-hourly frames
 *   GET /api/now/hotspots  NASA FIRMS detections, aggregated bins (measured)
 *   GET /api/now/observed  places with official PSI / API readings (measured)
 *                          and CAMS US-AQI equivalents (modelled)
 *
 * Labelling rules (CONTRACT_V2 M5) are enforced here, so no caller can get a
 * number without its label: every value handed out carries its index name,
 * averaging period, and whether it is measured or modelled. PSI is PSI, API
 * is API, CAMS is "US AQI, modelled" — never one called by another's name.
 *
 * No wind comes with these endpoints, so this mode has NO advected smoke
 * particles: the CAMS field carries the transport story, and the fires get
 * their local columns only. Inventing a wind to push particles around would
 * break the visual truth rules.
 *
 * Fixture data exists only behind an explicit flag (?fixture=week) for
 * building the UI before the endpoints are live, and is flagged `fixture`
 * so the page can banner it as not real.
 */

// token, and its tokens.css value as the fallback when a host page has none
const C = {
  good: ['--aqi-good', '#3E9E6E'], moderate: ['--aqi-moderate', '#D4A017'],
  usg: ['--aqi-usg', '#E07B22'], unhealthy: ['--aqi-unhealthy', '#C9332A'],
  very: ['--aqi-very', '#7E3B8C'], hazardous: ['--aqi-hazardous', '#6A1B2A'],
};
const BANDS_PSI = [   // PSI (SG) and API (MY) share breakpoints, not meaning
  [50, 'Good', C.good], [100, 'Moderate', C.moderate],
  [200, 'Unhealthy', C.unhealthy], [300, 'Very Unhealthy', C.very],
  [Infinity, 'Hazardous', C.hazardous],
];
const BANDS_USAQI = [
  [50, 'Good', C.good], [100, 'Moderate', C.moderate],
  [150, 'Unhealthy for Sensitive Groups', C.usg], [200, 'Unhealthy', C.unhealthy],
  [300, 'Very Unhealthy', C.very], [Infinity, 'Hazardous', C.hazardous],
];

export function indexBand(kind, v) {
  const table = kind === 'USAQI' ? BANDS_USAQI : BANDS_PSI;
  for (const [max, name, [token, hex]] of table) if (v <= max) return { name, token, hex };
  return { name: 'Hazardous', token: C.hazardous[0], hex: C.hazardous[1] };
}

/** Value of a {t, v} series at time ms: the latest sample at or before it. */
function at(series, ms) {
  if (!Array.isArray(series) || !series.length) return null;
  let best = null;
  for (const p of series) {
    const tt = Date.parse(p.t);
    if (Number.isNaN(tt) || tt > ms) continue;
    if (!best || tt > best.tt) best = { tt, v: p.v, t: p.t };
  }
  return best;
}

/**
 * A place's reading at time ms, with every label attached.
 * Official measured index leads; CAMS modelled fills the gap and says so.
 */
export function readingAt(place, ms) {
  const o = place.official;
  if (o && Array.isArray(o.series) && o.series.length) {
    const r = at(o.series, ms);
    if (r && r.v != null) {
      const kind = o.index === 'API' ? 'API' : 'PSI';
      return {
        value: r.v, t: r.t, measured: true,
        index: kind, averaging: o.averaging || '',
        label: `${kind === 'API' ? 'API (IPU)' : 'PSI'} ${o.averaging || ''}`.trim(),
        tag: 'measured',
        band: indexBand(kind, r.v), source: o.source,
      };
    }
  }
  const m = place.modelled;
  if (m && Array.isArray(m.series) && m.series.length) {
    const r = at(m.series, ms);
    if (r && r.v != null) {
      return {
        value: r.v, t: r.t, measured: false,
        index: 'USAQI', averaging: '',
        label: 'US AQI', tag: 'modelled',
        band: indexBand('USAQI', r.v), source: m.source,
      };
    }
  }
  return null;
}

async function getJson(url, signal) {
  const res = await fetch(url, { signal });
  if (!res.ok) throw Object.assign(new Error(`${url} returned ${res.status}`), { status: res.status });
  return res.json();
}

const dayStart = (iso) => Date.parse(`${String(iso).slice(0, 10)}T00:00:00Z`);

/** Turn the three responses into scene-ready data. Any part may be null. */
export function normaliseWeek({ field, hotspots, observed, fixture = false }) {
  if (!field || !field.grid || !Array.isArray(field.frames) || !field.frames.length) {
    return { available: false, reason: 'The CAMS haze field is not available.', hotspots, observed };
  }
  const t0 = Date.parse(field.frames[0].t);
  const toH = (iso) => (Date.parse(iso) - t0) / 3.6e6;
  const frames = field.frames.map((f, i) => ({
    index: i,
    hour: toH(f.t),
    t: f.t,
    time: f.t,
    hour_of_day: new Date(Date.parse(f.t) + 8 * 3.6e6).getUTCHours(),   // MYT/SGT, UTC+8
    values: f.values,
    max_pm25: f.values.reduce((a, b) => (b > a ? b : a), 0),
    mixing_height_m: null,
  }));
  const tEnd = frames[frames.length - 1].hour;

  // fires: FIRMS bins, lit between their first and last detection day
  const feats = hotspots?.clusters?.features || [];
  const sources = feats
    .filter((f) => f.geometry && f.geometry.type === 'Point')
    .map((f) => {
      const p = f.properties || {};
      const [lon, lat] = f.geometry.coordinates;
      const first = p.first_date ? (dayStart(p.first_date) - t0) / 3.6e6 : 0;
      const last = p.last_date ? (dayStart(p.last_date) - t0) / 3.6e6 + 24 : tEnd;
      return {
        lon, lat,
        count: p.count || 1,
        frp_mw: p.frp_mw || 0,
        // FRP is a measured burning rate; here it only sizes the fire.
        emission_g_s: Math.max(1, p.frp_mw || p.count || 1),
        injection_m: [0, 800],              // illustrative column height
        flaming_fraction: 0.3,              // FIRMS does not say; visual only
        region: p.region || 'other',
        on: [first, last],
      };
    });

  const places = (observed?.places || []).filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon));

  const attribution = [
    field.attribution || 'CAMS / Copernicus via Open-Meteo (CC BY 4.0)',
    hotspots?.attribution || (hotspots ? 'NASA FIRMS (LANCE NRT)' : null),
    places.some((p) => p.official && p.country === 'SG') ? 'NEA / data.gov.sg (Singapore Open Data Licence v1.0)' : null,
    places.some((p) => p.official && p.country === 'MY') ? 'DOE Malaysia, APIMS (https://eqms.doe.gov.my/APIMS/main)' : null,
  ].filter(Boolean);

  return {
    available: true,
    fixture,
    t0,
    tEnd,
    field: {
      version: 'now',
      grid: field.grid,
      frames,
      scale: { background_pm25: 0 },
      conditions: { release_from_start: false, hours: tEnd },
      sources,
      units: field.units || 'µg/m³',
    },
    sources,
    places,
    headline: observed?.headline || null,
    hotspots,
    observed,
    attribution,
    caveats: [
      ...(field.caveats || []),
      ...(hotspots?.caveats || []),
      ...(observed?.caveats || []),
    ],
    parts: { field: true, hotspots: !!hotspots, observed: !!observed },
  };
}

let routesP = null;
/** Paths the API publishes, from /api/openapi.json (cached per page). */
export function nowRoutes(signal) {
  if (!routesP) {
    routesP = fetch('/api/openapi.json', { signal })
      .then((r) => (r.ok ? r.json() : { paths: {} }))
      .then((j) => new Set(Object.keys(j.paths || {})))
      .catch(() => new Set());
  }
  return routesP;
}

/**
 * Fetch and normalise. Never throws: resolves { available: false, reason }
 * when the field cannot be had, so callers can show an unavailable state.
 */
export async function loadWeek({ fixture = false, signal } = {}) {
  if (fixture) {
    const { makeFixture } = await import('./fixture-week.js');
    return normaliseWeek({ ...makeFixture(), fixture: true });
  }
  // Feature-detect before calling: a missing optional endpoint must not
  // put 404s in the console. The route list comes from the API's schema.
  const routes = await nowRoutes(signal);
  if (!routes.has('/api/now/field')) {
    return { available: false, reason: "This week's haze data is not published yet.", errors: [] };
  }
  const want = (path) => routes.has(path);
  const q = 'days=7';
  const skip = Promise.resolve(new Error('not published'));
  const [field, hotspots, observed] = await Promise.all([
    getJson(`/api/now/field?${q}&step_hours=3`, signal).catch((e) => e),
    want('/api/now/hotspots') ? getJson(`/api/now/hotspots?${q}`, signal).catch((e) => e) : skip,
    want('/api/now/observed') ? getJson(`/api/now/observed?${q}`, signal).catch((e) => e) : skip,
  ]);
  const ok = (x) => (x instanceof Error ? null : x);
  const week = normaliseWeek({ field: ok(field), hotspots: ok(hotspots), observed: ok(observed) });
  if (!week.available) {
    const why = field instanceof Error ? field.message : 'no frames';
    week.reason = `This week's haze data is not available right now (${why}).`;
  }
  week.errors = [field, hotspots, observed].filter((x) => x instanceof Error).map((e) => e.message);
  return week;
}
