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
  // No reading at all. Say so, and point to the official source; never
  // render an absence as a low (clean-looking) number.
  if (place.official_portal) {
    return {
      value: null, measured: null, noReading: true,
      label: 'No public reading', tag: `see ${place.official_portal.name?.split(' ')[0] || 'official portal'}`,
      portal: place.official_portal, band: null,
    };
  }
  return null;
}

async function getJson(url, signal) {
  const res = await fetch(url, { signal });
  if (!res.ok) throw Object.assign(new Error(`${url} returned ${res.status}`), { status: res.status });
  return res.json();
}

const H = 3.6e6;
const MYT = '+08:00';

/** Display name: Singapore's regions come back as "North", "Central"... */
function placeName(p) {
  if (p.country === 'SG' && !/singapore/i.test(p.name || '')) return `Singapore ${p.name}`;
  return p.name;
}

/**
 * Turn the three responses into scene-ready data. Any part may be missing:
 *  - field + fires: the CAMS haze field over real fires;
 *  - fires only (the launch configuration: no CAMS licence): real fires lit
 *    day by day and the official readings, with NO haze drawn — an absent
 *    field is not clean air, and the page says which it is;
 *  - neither: unavailable.
 */
export function normaliseWeek({ field, hotspots, observed, fixture = false }) {
  const hasField = !!(field && field.grid && Array.isArray(field.frames) && field.frames.length);
  const feats = (hotspots?.clusters?.features || []).filter((f) => f.geometry && f.geometry.type === 'Point');
  const hasFires = feats.length > 0;
  if (!hasField && !hasFires) {
    return { available: false, reason: "Neither this week's fire detections nor its haze field are available right now.", hotspots, observed };
  }

  // time base: the field's frames, or the fire window in 3 h steps
  let t0;
  let frames;
  if (hasField) {
    t0 = Date.parse(field.frames[0].t);
    frames = field.frames.map((f, i) => ({
      index: i, hour: (Date.parse(f.t) - t0) / H, t: f.t, time: f.t,
      hour_of_day: new Date(Date.parse(f.t) + 8 * H).getUTCHours(),   // MYT/SGT, UTC+8
      values: f.values,
      max_pm25: f.values.reduce((a, b) => (b > a ? b : a), 0),
      mixing_height_m: null,
    }));
  } else {
    const w = hotspots.window || observed?.window || {};
    const end = Date.parse(w.end) || Date.now();
    const start = Date.parse(w.start) || end - 7 * 24 * H;
    t0 = Math.floor(start / (3 * H)) * 3 * H;
    frames = [];
    for (let t = t0, i = 0; t <= end; t += 3 * H, i += 1) {
      frames.push({
        index: i, hour: (t - t0) / H, t: new Date(t).toISOString(), time: new Date(t).toISOString(),
        hour_of_day: new Date(t + 8 * H).getUTCHours(),
        values: [0, 0, 0, 0], max_pm25: null, mixing_height_m: null,
      });
    }
  }
  const tEnd = frames[frames.length - 1].hour;
  // stand-in grid when there is no field: never drawn, never sampled for display
  const grid = hasField ? field.grid : {
    lon_min: 95, lon_max: 120, lat_min: -6, lat_max: 8, nx: 2, ny: 2, dlon: 25, dlat: 14,
  };

  // fires: FIRMS bins, lit on the local days they were detected
  const days = (hotspots?.by_day || []).map((d) => d.date);
  const day0 = days.length ? (Date.parse(`${days[0]}T00:00:00${MYT}`) - t0) / H : null;
  const sources = feats.map((f) => {
    const p = f.properties || {};
    const [lon, lat] = f.geometry.coordinates;
    const src = {
      lon, lat,
      count: p.count || 1,
      frp_mw: p.frp_mw || 0,
      // FRP is a measured burning rate; here it only sizes the fire.
      // log-compressed so the many small bins still read beside the few huge ones
      emission_g_s: 1 + Math.log1p(p.frp_mw || p.count || 1) ** 2,
      injection_m: [0, 800],              // illustrative column height
      flaming_fraction: 0.3,              // FIRMS does not say; visual only
      region: p.region || 'other',
    };
    if (Array.isArray(p.daily) && day0 != null) {
      src.daily = p.daily;
      src.day0 = day0;
    } else {
      const first = p.first_date ? (Date.parse(`${p.first_date}T00:00:00${MYT}`) - t0) / H : 0;
      const last = p.last_date ? (Date.parse(`${p.last_date}T00:00:00${MYT}`) - t0) / H + 24 : tEnd;
      src.on = [first, last];
    }
    return src;
  });

  const places = (observed?.places || [])
    .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon))
    .map((p) => ({ ...p, name: placeName(p) }));

  const anyModelled = places.some((p) => p.modelled);
  const attribution = [
    hasField ? 'Haze: CAMS / Copernicus via Open-Meteo (CC BY 4.0), modelled' : null,
    hasFires ? 'Fires: NASA FIRMS, VIIRS (LANCE NRT), measured detections' : null,
    places.some((p) => p.official && p.country === 'SG') ? 'Singapore PSI: NEA via data.gov.sg (Singapore Open Data Licence v1.0), measured' : null,
    places.some((p) => p.official && p.country === 'MY') ? 'Malaysia API: DOE Malaysia, measured' : null,
    places.some((p) => p.official_portal) ? 'Malaysia: official API (IPU) readings are on APIMS (eqms.doe.gov.my); not copied here' : null,
  ].filter(Boolean);
  const attributionFull = [
    hasField ? field.attribution : null,
    hasFires ? hotspots.attribution : null,
    ...((observed?.attribution || []).filter((a) => hasField || anyModelled || !/copernicus|open-meteo/i.test(a))),
  ].filter(Boolean);

  return {
    available: true,
    hasField,
    hasFires,
    fixture,
    t0,
    tEnd,
    field: {
      version: 'now',
      grid,
      frames,
      scale: { background_pm25: 0 },
      conditions: { release_from_start: false, hours: tEnd },
      sources,
      units: hasField ? (field.units || 'µg/m³') : null,
    },
    sources,
    places,
    portals: observed?.official_portals || null,
    headline: observed?.headline || null,
    hotspots: hasFires ? hotspots : null,
    observed,
    attribution,
    attributionFull,
    caveats: [
      ...(hasField ? (field.caveats || []) : []),
      ...(hotspots?.caveats || []),
      ...(observed?.caveats || []),
    ],
    parts: { field: hasField, hotspots: hasFires, observed: !!observed },
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
    return normaliseWeek({ ...makeFixture(undefined, { noField: fixture === 'nofield' }), fixture: true });
  }
  // Feature-detect before calling: an endpoint that is not published must
  // not put 404s in the console. The route list comes from the API schema.
  const routes = await nowRoutes(signal);
  const want = (path) => routes.has(path);
  if (!want('/api/now/field') && !want('/api/now/hotspots')) {
    return { available: false, reason: "This week's data is not published yet.", errors: [] };
  }
  const q = 'days=7';
  const skip = Promise.resolve(new Error('not published'));
  const [hotspots, observed] = await Promise.all([
    want('/api/now/hotspots') ? getJson(`/api/now/hotspots?${q}`, signal).catch((e) => e) : skip,
    want('/api/now/observed') ? getJson(`/api/now/observed?${q}`, signal).catch((e) => e) : skip,
  ]);
  const ok = (x) => (x instanceof Error ? null : x);
  // Without a CAMS licence the field route still exists but answers 503, and
  // the browser logs every failed request. /observed says whether CAMS is
  // on (sources.cams.available), so ask only when it is — or when we cannot
  // tell.
  const cams = ok(observed)?.sources?.cams;
  const camsOff = cams && cams.available === false;
  const field = want('/api/now/field') && !camsOff
    ? await getJson(`/api/now/field?${q}&step_hours=3`, signal).catch((e) => e)
    : Object.assign(new Error(camsOff ? 'CAMS is not enabled' : 'not published'), { status: camsOff ? 503 : 404 });
  const week = normaliseWeek({ field: ok(field), hotspots: ok(hotspots), observed: ok(observed) });
  week.errors = [field, hotspots, observed].filter((x) => x instanceof Error).map((e) => e.message);
  if (week.available && !week.hasField) {
    week.fieldReason = field instanceof Error && field.status === 503
      ? 'The haze field is not available in this configuration.'
      : 'The haze field is not available right now.';
  }
  return week;
}
