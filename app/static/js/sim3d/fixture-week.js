/**
 * fixture-week.js — SYNTHETIC stand-ins for the M5 /api/now/* responses.
 *
 * Loaded only when a page asks for it explicitly (?fixture=week), so the
 * "This week" UI can be built before the LIVE-DATA endpoints exist. Every
 * number here is generated, not observed; the page banners fixture data as
 * not real. Shapes follow CONTRACT_V2 M5 exactly.
 */

const H = 3.6e6;

function rng(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

export function makeFixture(endIso = new Date().toISOString().slice(0, 10), { noField = false } = {}) {
  const end = Date.parse(`${endIso}T00:00:00Z`);
  const start = end - 7 * 24 * H;
  const r = rng(20261016);
  const grid = {
    lon_min: 95, lon_max: 120, lat_min: -6, lat_max: 8, nx: 51, ny: 29,
    dlon: 0.5, dlat: 0.5, order: 'row-major, rows south to north, columns west to east',
  };
  const plumes = [
    // [lon, lat, strength, drift lon/h, drift lat/h, spread deg]
    { lon: 113.6, lat: -2.4, s: 260, dx: -0.045, dy: 0.040, w: 1.6 },
    { lon: 102.6, lat: -0.6, s: 220, dx: -0.010, dy: 0.050, w: 1.2 },
    { lon: 110.2, lat: -1.4, s: 140, dx: -0.050, dy: 0.035, w: 1.3 },
  ];
  const frames = [];
  for (let h = 0; h <= 7 * 24 - 3; h += 3) {
    const tt = start + h * H;
    const hod = (h + 8) % 24;
    const night = 0.75 + 0.25 * Math.cos((2 * Math.PI * (hod - 4)) / 24);
    const build = 0.55 + 0.45 * Math.min(1, h / 96);
    const values = [];
    for (let j = 0; j < grid.ny; j += 1) {
      const lat = grid.lat_min + j * grid.dlat;
      for (let i = 0; i < grid.nx; i += 1) {
        const lon = grid.lon_min + i * grid.dlon;
        let v = 14;
        for (const p of plumes) {
          // a plume is a chain of puffs released over the past 72 h
          for (let a = 0; a < 72; a += 6) {
            const cx = p.lon + p.dx * a * (1 + 0.15 * Math.sin(h / 30));
            const cy = p.lat + p.dy * a;
            const w = p.w * (1 + a / 40);
            const d2 = ((lon - cx) ** 2 + (lat - cy) ** 2) / (w * w);
            v += (p.s / (1 + a / 18)) * Math.exp(-d2) * night * build * 0.35;
          }
        }
        values.push(Math.round(v));
      }
    }
    frames.push({ t: new Date(tt).toISOString(), values });
  }

  const feats = [];
  const addBins = (n, lon, lat, sx, sy, region, frpScale) => {
    for (let k = 0; k < n; k += 1) {
      const d0 = Math.floor(r() * 5);
      const d1 = Math.min(6, d0 + 1 + Math.floor(r() * 4));
      feats.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lon + (r() - 0.5) * sx, lat + (r() - 0.5) * sy] },
        properties: {
          count: 5 + Math.floor(r() * 120),
          frp_mw: Math.round((50 + r() * 900) * frpScale),
          daily: Array.from({ length: 8 }, (_, d) => (d >= d0 && d <= d1 ? 1 + Math.floor(r() * 20) : 0)),
          first_date: new Date(start + d0 * 24 * H).toISOString().slice(0, 10),
          last_date: new Date(start + d1 * 24 * H).toISOString().slice(0, 10),
          region,
        },
      });
    }
  };
  addBins(70, 113.4, -2.2, 4.5, 2.4, 'kalimantan', 1);
  addBins(18, 110.4, -1.2, 2.2, 1.6, 'kalimantan', 0.8);
  addBins(40, 102.6, -0.8, 3.2, 3.0, 'sumatra', 0.9);
  addBins(3, 102.0, 3.6, 1.2, 1.0, 'peninsular_malaysia', 0.2);

  const series = (fn, step) => {
    const out = [];
    for (let h = 0; h <= 7 * 24 - step; h += step) out.push({ t: new Date(start + h * H).toISOString(), v: Math.round(fn(h)) });
    return out;
  };
  const sgPsi = (bias) => (h) => 60 + bias + 110 * Math.min(1, h / 120) * (0.85 + 0.15 * Math.sin(h / 7));
  const myAqi = (bias) => (h) => 90 + bias + 150 * Math.min(1, h / 110) * (0.85 + 0.15 * Math.sin(h / 9));
  const sg = (id, name, lat, lon, bias) => ({
    id, name, country: 'SG', lat, lon,
    official: {
      index: 'PSI', averaging: '24h', series: series(sgPsi(bias), 1), max: null,
      band: null, source: 'FIXTURE (synthetic)', licence: null, url: null,
    },
    pm25: null, modelled: null,
  });
  const portal = { name: 'APIMS - Air Pollutant Index Management System (DOE Malaysia)', url: 'https://eqms.doe.gov.my/APIMS/main', index: 'API', note: 'FIXTURE' };
  const my = (id, name, lat, lon, bias) => (noField ? {
    id, name, country: 'MY', lat, lon, official: null, pm25: null, modelled: null, official_portal: portal,
  } : {
    id, name, country: 'MY', lat, lon, official: null, pm25: null,
    modelled: {
      index: 'US AQI (modelled, CAMS)', series: series(myAqi(bias), 3), pm25_series: [],
      max: null, band: null, source: 'FIXTURE (synthetic)', grid_km: 40,
    },
  });
  const places = [
    my('my-kuala-lumpur', 'Kuala Lumpur', 3.139, 101.687, 0),
    my('my-petaling-jaya', 'Petaling Jaya', 3.107, 101.607, 0),
    my('my-shah-alam', 'Shah Alam', 3.073, 101.518, 0),
    my('my-klang', 'Klang', 3.044, 101.445, 0),
    sg('sg-west', 'Singapore West', 1.352, 103.700, 25),
    sg('sg-north', 'Singapore North', 1.418, 103.820, 5),
    sg('sg-central', 'Singapore Central', 1.300, 103.820, 0),
    sg('sg-east', 'Singapore East', 1.352, 103.940, -5),
    sg('sg-south', 'Singapore South', 1.275, 103.820, -10),
  ];

  return {
    field: noField ? null : {
      grid, frames, units: 'µg/m³ PM2.5',
      source: 'FIXTURE (synthetic)', attribution: 'FIXTURE — synthetic data, not CAMS',
      caveats: ['FIXTURE: synthetic field for interface development. Not an observation or a model run.'],
    },
    hotspots: {
      as_of: new Date(end).toISOString(), source: 'FIXTURE (synthetic)',
      attribution: 'FIXTURE — synthetic hotspots, not NASA FIRMS',
      window: { start: new Date(start).toISOString(), end: new Date(end).toISOString(), days: 7 },
      count_by_region: { kalimantan: 0, sumatra: 0 }, frp_gw_by_region: {},
      by_day: Array.from({ length: 8 }, (_, d) => ({ date: new Date(start + d * 24 * H).toISOString().slice(0, 10) })),
      clusters: { type: 'FeatureCollection', features: feats },
      caveats: ['FIXTURE: synthetic hotspots.'],
    },
    observed: {
      as_of: new Date(end).toISOString(), window: { start: new Date(start).toISOString(), end: new Date(end).toISOString() },
      places,
      headline: { text: 'FIXTURE headline — synthetic, not a real reading.', place_id: 'sg-west', index: 'PSI', measured: true, value: 0, t: null, band: null },
      caveats: ['FIXTURE: synthetic readings.'],
    },
  };
}
