/* __mocks__/fixtures.js — DEVELOPMENT ONLY.
 *
 * Synthetic responses in the exact shapes of CONTRACT.md Layers 1-3, so the
 * front end can be built and reviewed without a running backend.
 * Activated only by ?mock=1. Nothing here is real data: every season row is
 * marked "estimated": true and the caveats say so out loud.
 *
 * If you are looking for the numbers the site actually shows, they come from
 * the API, not from this file.
 */

/* --------------------------------------------------------------- utilities */

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const iso = (y, m, d) => `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;

function addDays(isoStr, n) {
  const d = new Date(isoStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function daysBetween(a, b) {
  return Math.round((new Date(b + 'T00:00:00Z') - new Date(a + 'T00:00:00Z')) / 86400000);
}

const MOCK_NOTE = 'MOCK FIXTURE — synthetic numbers for front-end development, not measurements.';

/* ----------------------------------------------------------------- seasons */

// [year, enso_phase, oni_peak, startMonthDay, peakMonthDay, endMonthDay,
//  burned_idn, burned_kal, peat_fraction, hotspots]
const SEASON_SEED = [
  [1997, 'el_nino', 2.4, [6, 10], [9, 28], [11, 22], 4.90, 1.45, 0.35, 141000],
  [2002, 'el_nino', 1.3, [7, 2], [9, 20], [10, 28], 1.60, 0.52, 0.30, 62000],
  [2004, 'el_nino', 0.7, [7, 14], [9, 12], [10, 18], 1.10, 0.34, 0.28, 41000],
  [2006, 'el_nino', 0.9, [6, 28], [10, 6], [11, 4], 1.90, 0.61, 0.31, 74000],
  [2009, 'el_nino', 1.6, [7, 8], [9, 24], [10, 26], 1.20, 0.38, 0.27, 45000],
  [2014, 'neutral', 0.7, [7, 20], [9, 16], [10, 12], 0.95, 0.29, 0.24, 33000],
  [2015, 'el_nino', 2.6, [6, 24], [10, 14], [11, 9], 2.61, 0.77, 0.33, 129813],
  [2016, 'la_nina', -0.7, [8, 12], [9, 4], [9, 26], 0.44, 0.09, 0.16, 9800],
  [2017, 'neutral', -0.4, [8, 2], [9, 8], [10, 2], 0.17, 0.05, 0.14, 7200],
  [2018, 'neutral', 0.2, [7, 26], [9, 10], [10, 8], 0.53, 0.16, 0.19, 15400],
  [2019, 'el_nino', 0.6, [6, 30], [9, 21], [10, 30], 1.65, 0.48, 0.30, 89000],
  [2020, 'la_nina', -1.2, [8, 20], [9, 12], [9, 28], 0.30, 0.07, 0.15, 6100],
  [2021, 'la_nina', -1.0, [8, 24], [9, 14], [9, 30], 0.36, 0.08, 0.14, 5400],
  [2022, 'la_nina', -1.0, [8, 18], [9, 8], [9, 27], 0.20, 0.05, 0.13, 4800],
  [2023, 'el_nino', 2.0, [6, 28], [10, 2], [11, 1], 1.16, 0.34, 0.29, 70000],
  [2024, 'neutral', 0.1, [7, 22], [9, 14], [10, 10], 0.52, 0.15, 0.20, 18600],
  [2025, 'el_nino', 1.1, [6, 26], [9, 26], [10, 29], 1.05, 0.31, 0.27, 52000],
];

const SEASONS = SEASON_SEED.map(([year, phase, oni, s, p, e, idn, kal, peat, hs]) => {
  const start = iso(year, s[0], s[1]);
  const end = iso(year, e[0], e[1]);
  const scale = kal / 0.77;
  return {
    year,
    enso_phase: phase,
    oni_peak: oni,
    start_date: start,
    peak_date: iso(year, p[0], p[1]),
    end_date: end,
    duration_days: daysBetween(start, end),
    burned_area_mha_indonesia: idn,
    burned_area_mha_kalimantan: kal,
    peat_fraction: peat,
    hotspot_count: hs,
    pm25_peak_ugm3: {
      Palangkaraya: Math.round(400 + 3341 * Math.pow(scale, 1.1)),
      Pontianak: Math.round(160 + 690 * Math.pow(scale, 0.9)),
      Singapore: Math.round(30 + 311 * Math.pow(scale, 0.9)),
    },
    co2e_gt: Number((1.75 * scale).toFixed(2)),
    economic_loss_usd_bn: Number((16.1 * scale).toFixed(1)),
    excess_deaths_est: year === 2015 ? 100300 : Math.round(100300 * Math.pow(scale, 1.2)),
    source: 'MOCK — shaped after KLHK, Koplitz et al. 2016 ERL and World Bank 2016',
    estimated: true,
    notes: MOCK_NOTE,
  };
});

/* CONTRACT_V2 range objects. ILLUSTRATIVE ONLY: the values are patterned
 * (12,345 / 45,678 / 98,765 …) and the "studies" are named Mock Study A–D with
 * 10.0000/ DOIs, so nobody can mistake this harness for the literature. */
const MOCK_RANGES = {
  2015: {
    excess_deaths_range: {
      low: 12345, high: 98765, central: null,
      estimates: [
        { value: 98765, region: 'ID+MY+SG', source: 'Mock Study A (illustrative)', doi: '10.0000/MOCK-A' },
        { value: 45678, region: 'ID+MY+SG', source: 'Mock Study B (illustrative)', doi: '10.0000/MOCK-B' },
        { value: 12345, region: 'ID only', source: 'Mock Study C (illustrative)', doi: '10.0000/MOCK-C' },
      ],
      note: 'MOCK NOTE: estimates differ mainly in the concentration-response function and the modelled exposure.',
    },
    burned_area_mha_kalimantan_range: {
      low: 0.555, high: 0.999, central: null,
      estimates: [
        { value: 0.555, source: 'Mock Survey D (illustrative)', doi: '10.0000/MOCK-D' },
        { value: 0.777, source: 'Mock Survey E (illustrative)', doi: '10.0000/MOCK-E' },
        { value: 0.999, source: 'Mock Survey F (illustrative)', doi: '10.0000/MOCK-F' },
      ],
      note: 'MOCK NOTE: products disagree on small and peat-understorey burns.',
    },
    burned_area_mha_indonesia_range: {
      low: 2.222, high: 3.333, central: null,
      estimates: [
        { value: 2.222, source: 'Mock Survey D (illustrative)', doi: '10.0000/MOCK-D' },
        { value: 3.333, source: 'Mock Survey F (illustrative)', doi: '10.0000/MOCK-F' },
      ],
      note: 'MOCK NOTE: shape only.',
    },
  },
  1997: {
    excess_deaths_range: {
      low: 33333, high: 222222, central: null,
      estimates: [
        { value: 222222, region: 'ID+MY+SG', source: 'Mock Study A (illustrative)', doi: '10.0000/MOCK-A' },
        { value: 111111, region: 'ID+MY+SG', source: 'Mock Study B (illustrative)', doi: '10.0000/MOCK-B' },
        { value: 33333, region: 'ID only', source: 'Mock Study C (illustrative)', doi: '10.0000/MOCK-C' },
      ],
      note: 'MOCK NOTE: shape only. Real ranges come from the MODEL agent.',
    },
    burned_area_mha_kalimantan_range: {
      low: 1.111, high: 1.888, central: null,
      estimates: [
        { value: 1.111, source: 'Mock Survey G (illustrative)', doi: '10.0000/MOCK-G' },
        { value: 1.888, source: 'Mock Survey H (illustrative)', doi: '10.0000/MOCK-H' },
      ],
      note: 'MOCK NOTE: shape only.',
    },
  },
};
for (const s of SEASONS) Object.assign(s, MOCK_RANGES[s.year] || {});

const SEASON_SUMMARY = {
  n_seasons: SEASONS.length,
  first_year: SEASONS[0].year,
  last_year: SEASONS[SEASONS.length - 1].year,
  mean_duration_days: Math.round(
    SEASONS.reduce((a, s) => a + s.duration_days, 0) / SEASONS.length),
  mean_duration_el_nino: Math.round(
    SEASONS.filter((s) => s.enso_phase === 'el_nino')
      .reduce((a, s, _, arr) => a + s.duration_days / arr.length, 0)),
  mean_duration_la_nina: Math.round(
    SEASONS.filter((s) => s.enso_phase === 'la_nina')
      .reduce((a, s, _, arr) => a + s.duration_days / arr.length, 0)),
  total_burned_mha_kalimantan: Number(
    SEASONS.reduce((a, s) => a + s.burned_area_mha_kalimantan, 0).toFixed(2)),
  worst_year: 1997,
  median_start_day: '24 June – 24 August',
  caveats: [
    'MOCK DATA: every row in this table is synthetic and exists only so the page can be developed offline.',
    'Season start and end dates are defined by a hotspot-density threshold, not by when the air became breathable again.',
  ],
};

/* -------------------------------------------------------------- fuel stock */

const FUEL = {
  as_of: '2025-12-31',
  pools: [
    {
      id: 'peat_drained', label: 'Drained / degraded peatland', area_mha: 2.7,
      fuel_load_t_ha: 300, pm25_ef_g_kg: 17.3, burn_susceptibility: 1.0,
      source: 'MOCK — shaped after Wetlands International / BRGM peat extent', estimated: true,
    },
    {
      id: 'peat_intact', label: 'Intact peatland', area_mha: 2.1,
      fuel_load_t_ha: 320, pm25_ef_g_kg: 17.3, burn_susceptibility: 0.35,
      source: 'MOCK — shaped after Wetlands International peat extent', estimated: true,
    },
    {
      id: 'primary_forest', label: 'Primary forest', area_mha: 9.4,
      fuel_load_t_ha: 190, pm25_ef_g_kg: 9.1, burn_susceptibility: 0.22,
      source: 'MOCK — shaped after Margono et al. 2014', estimated: true,
    },
    {
      id: 'secondary_forest', label: 'Secondary / logged-over forest', area_mha: 12.8,
      fuel_load_t_ha: 120, pm25_ef_g_kg: 9.1, burn_susceptibility: 0.48,
      source: 'MOCK — shaped after KLHK land-cover classes', estimated: true,
    },
    {
      id: 'scrub_grass', label: 'Scrub, grass & abandoned land', area_mha: 6.9,
      fuel_load_t_ha: 42, pm25_ef_g_kg: 8.2, burn_susceptibility: 0.75,
      source: 'MOCK — shaped after KLHK land-cover classes', estimated: true,
    },
  ],
  deforestation_mha_per_yr: 0.13,
  restoration_mha_per_yr: 0.05,
  total_land_mha: 54.4,
  sources: [
    'MOCK FIXTURE — no real dataset was read to build this object.',
    'Stand-in for: KLHK land cover, Global Forest Watch, BRGM peat restoration reporting.',
  ],
  caveats: [
    'MOCK RESPONSE: the fuel pools here are invented for front-end development.',
    'burn_susceptibility is a model parameter, not a measurement.',
  ],
};

/* ------------------------------------------------------------------ regions */

const PROVINCES = [
  {
    name: 'Kalimantan Barat', name_en: 'West Kalimantan', area_mha: 14.7, peat_mha: 1.73,
    population: 5414400, capital: 'Pontianak', capital_lat: -0.026, capital_lon: 109.342,
    ring: [[108.9, -1.0], [109.0, 0.9], [109.6, 1.8], [110.6, 1.6], [111.6, 1.1],
      [112.6, 1.5], [113.6, 1.4], [114.0, 0.6], [113.4, -0.4], [112.4, -1.2],
      [111.4, -2.4], [110.4, -3.0], [109.4, -2.0], [108.9, -1.0]],
  },
  {
    name: 'Kalimantan Tengah', name_en: 'Central Kalimantan', area_mha: 15.4, peat_mha: 2.63,
    population: 2741000, capital: 'Palangkaraya', capital_lat: -2.208, capital_lon: 113.916,
    ring: [[111.0, -3.3], [111.6, -2.4], [112.6, -1.4], [113.5, -0.5], [114.3, -0.2],
      [115.4, -0.9], [115.3, -1.9], [114.6, -2.6], [114.0, -3.4], [113.0, -3.6],
      [112.0, -3.5], [111.0, -3.3]],
  },
  {
    name: 'Kalimantan Selatan', name_en: 'South Kalimantan', area_mha: 3.8, peat_mha: 0.24,
    population: 4182700, capital: 'Banjarmasin', capital_lat: -3.319, capital_lon: 114.592,
    ring: [[114.0, -3.4], [114.6, -2.6], [115.3, -1.9], [116.3, -2.3], [116.1, -3.2],
      [115.6, -4.1], [114.8, -4.2], [114.3, -3.7], [114.0, -3.4]],
  },
  {
    name: 'Kalimantan Timur', name_en: 'East Kalimantan', area_mha: 12.7, peat_mha: 0.66,
    population: 3908000, capital: 'Samarinda', capital_lat: -0.502, capital_lon: 117.154,
    ring: [[115.4, -0.9], [114.3, -0.2], [114.6, 0.9], [115.6, 1.6], [116.6, 1.5],
      [117.5, 1.1], [118.0, 0.3], [117.5, -0.6], [117.0, -1.5], [116.3, -2.3],
      [115.3, -1.9], [115.4, -0.9]],
  },
  {
    name: 'Kalimantan Utara', name_en: 'North Kalimantan', area_mha: 7.5, peat_mha: 0.21,
    population: 716400, capital: 'Tanjung Selor', capital_lat: 2.838, capital_lon: 117.366,
    ring: [[115.6, 1.6], [115.4, 2.5], [116.2, 3.4], [117.2, 4.2], [117.9, 4.1],
      [117.6, 3.2], [117.6, 2.2], [117.5, 1.1], [116.6, 1.5], [115.6, 1.6]],
  },
];

const REGIONS = {
  type: 'FeatureCollection',
  features: PROVINCES.map((p) => ({
    type: 'Feature',
    properties: {
      name: p.name, name_en: p.name_en, area_mha: p.area_mha, peat_mha: p.peat_mha,
      population: p.population, capital: p.capital,
      capital_lat: p.capital_lat, capital_lon: p.capital_lon,
    },
    geometry: { type: 'Polygon', coordinates: [p.ring] },
  })),
};

/* ---------------------------------------------------------------- clusters */

function pointInProvinceBox(rng, p) {
  const lons = p.ring.map((c) => c[0]);
  const lats = p.ring.map((c) => c[1]);
  const lon0 = Math.min(...lons), lon1 = Math.max(...lons);
  const lat0 = Math.min(...lats), lat1 = Math.max(...lats);
  // shrink toward centre so points stay roughly inside the simplified ring
  const cx = (lon0 + lon1) / 2, cy = (lat0 + lat1) / 2;
  const lon = cx + (rng() - 0.5) * (lon1 - lon0) * 0.62;
  const lat = cy + (rng() - 0.5) * (lat1 - lat0) * 0.62;
  return [Number(lon.toFixed(3)), Number(lat.toFixed(3))];
}

const POOL_WEIGHTS = [
  ['peat_drained', 0.34], ['peat_intact', 0.10], ['secondary_forest', 0.28],
  ['scrub_grass', 0.20], ['primary_forest', 0.08],
];

function pickPool(rng) {
  let r = rng();
  for (const [id, w] of POOL_WEIGHTS) { if ((r -= w) <= 0) return id; }
  return 'scrub_grass';
}

function clustersFor(year) {
  const season = SEASONS.find((s) => s.year === year) || SEASONS[SEASONS.length - 1];
  const rng = mulberry32(year * 7919);
  const n = Math.round(60 + 320 * Math.min(1, season.burned_area_mha_kalimantan / 1.45));
  // fire weight per province, peat-heavy provinces burn more
  const weights = PROVINCES.map((p) => p.peat_mha + 0.15 * p.area_mha / 10);
  const wsum = weights.reduce((a, b) => a + b, 0);
  const startD = new Date(season.start_date + 'T00:00:00Z');
  const span = season.duration_days;
  const features = [];
  for (let i = 0; i < n; i++) {
    let r = rng() * wsum, idx = 0;
    while (idx < weights.length - 1 && (r -= weights[idx]) > 0) idx++;
    const prov = PROVINCES[idx];
    const [lon, lat] = pointInProvinceBox(rng, prov);
    // fires cluster around the peak date
    const u = (rng() + rng() + rng()) / 3;
    const dayOffset = Math.round(u * span);
    const area = Math.round(40 + Math.pow(rng(), 3.2) * 4200
      * (season.burned_area_mha_kalimantan / 0.77));
    features.push({
      type: 'Feature',
      properties: {
        date: addDays(startD.toISOString().slice(0, 10), dayOffset),
        area_ha: area,
        duration_days: Math.round(3 + rng() * 30),
        pool: pickPool(rng),
        frp_mw: Math.round(20 + rng() * 1400),
        province: prov.name,
        estimated: true,
      },
      geometry: { type: 'Point', coordinates: [lon, lat] },
    });
  }
  return { type: 'FeatureCollection', features };
}

/* --------------------------------------------------------------- receptors */

const RECEPTORS = [
  { name: 'Palangkaraya', country: 'Indonesia', lat: -2.208, lon: 113.916, population: 293500 },
  { name: 'Pontianak', country: 'Indonesia', lat: -0.026, lon: 109.342, population: 673400 },
  { name: 'Banjarmasin', country: 'Indonesia', lat: -3.319, lon: 114.592, population: 700900 },
  { name: 'Samarinda', country: 'Indonesia', lat: -0.502, lon: 117.154, population: 831000 },
  { name: 'Kuching', country: 'Malaysia', lat: 1.553, lon: 110.359, population: 617900 },
  { name: 'Kuala Lumpur', country: 'Malaysia', lat: 3.139, lon: 101.687, population: 1808000 },
  { name: 'Singapore', country: 'Singapore', lat: 1.352, lon: 103.820, population: 5920000 },
];

/* ------------------------------------------------------------ season model */

const DRIVER_TEMPLATES = [
  {
    id: 'kbdi', name: 'KBDI', unit: 'index', direction: 'prolongs',
    explain: 'Peat is dry well below the surface, so fires keep smouldering underground between rain events.',
  },
  {
    id: 'oni', name: 'ONI', unit: 'degC', direction: 'prolongs',
    explain: 'A warm Pacific delays the monsoon rains that normally shut the season down.',
  },
  {
    id: 'monsoon', name: 'Monsoon progress', unit: '0-1', direction: 'shortens',
    explain: 'The northeast monsoon rain band is still to the north; once it arrives the fires go out quickly.',
  },
  {
    id: 'rain_7d', name: '7-day rainfall', unit: 'mm', direction: 'shortens',
    explain: 'Recent rain has wetted the surface fuels, though not deep enough to reach the peat.',
  },
  {
    id: 'iod', name: 'Indian Ocean Dipole', unit: 'degC', direction: 'prolongs',
    explain: 'MOCK: a positive dipole cools the eastern Indian Ocean and dries out western Indonesia.',
  },
  {
    id: 'enforcement', name: 'Enforcement / moratorium', unit: 'index', direction: 'shortens',
    explain: 'Burn bans and canal blocking reduce new ignitions but do not stop fires already alight.',
  },
];

function seasonSim(body) {
  const b = body || {};
  const asOf = b.as_of || new Date().toISOString().slice(0, 10);
  const oni = b.oni === null || b.oni === undefined ? 1.1 : Number(b.oni);
  const scenario = b.scenario || 'baseline';
  const scenarioShift = {
    baseline: 0, no_enforcement: 8, strict_moratorium: -9,
    monsoon_early: -12, monsoon_late: 14,
  }[scenario] || 0;

  const p50 = Math.max(1, Math.round(26 + oni * 11 + scenarioShift));
  const p10 = Math.max(1, Math.round(p50 * 0.42));
  const p90 = Math.round(p50 * 2.15 + 6);
  const mean = Number((p50 * 1.12).toFixed(1));

  const daily = [];
  let survive = 1;
  for (let d = 1; d <= Math.min(p90 + 30, 150); d++) {
    const hz = 1 / (1 + Math.exp(-(d - p50) / (p50 * 0.34))) * 0.085 + 0.002;
    const pEnd = survive * hz;
    survive = Math.max(0, survive - pEnd);
    daily.push({
      date: addDays(asOf, d),
      p_end: Number(pEnd.toFixed(4)),
      p_still_burning: Number(survive.toFixed(4)),
    });
  }

  const raw = [
    0.30 + 0.10 * Math.max(0, oni),
    0.12 + 0.16 * Math.abs(oni),
    0.26 - 0.05 * Math.max(0, oni),
    0.16,
    0.07,
    scenario === 'strict_moratorium' ? 0.22 : 0.09,
  ];
  const total = raw.reduce((a, c) => a + c, 0);
  const values = [
    Math.round(560 + oni * 55),
    Number(oni.toFixed(2)),
    Number(Math.max(0.02, 0.28 - oni * 0.07).toFixed(2)),
    Math.round(Math.max(0, 22 - oni * 7)),
    0.42,
    scenario === 'strict_moratorium' ? 0.8 : scenario === 'no_enforcement' ? 0.1 : 0.4,
  ];
  const drivers = DRIVER_TEMPLATES.map((t, i) => ({
    ...t,
    value: values[i],
    contribution: Number((raw[i] / total).toFixed(3)),
    direction: oni < 0 && t.id === 'oni' ? 'shortens' : t.direction,
  })).sort((a, b2) => b2.contribution - a.contribution);

  return {
    as_of: asOf,
    region: b.region || 'kalimantan',
    oni,                 // the live route echoes the resolved ONI
    drought_readiness: 0.83,   // CONTRACT M4 (MOCK value)
    active: true,
    days_remaining: { p10, p50, p90, mean },
    end_date: { p10: addDays(asOf, p10), p50: addDays(asOf, p50), p90: addDays(asOf, p90) },
    daily_end_probability: daily,
    drivers,
    confidence: Math.abs(oni) > 1.8 ? 'low' : 'moderate',
    live_weather: false,
    scenario,
    caveats: [
      'MOCK RESPONSE: these numbers come from a front-end fixture, not from the model.',
      '"End of season" means hotspot counts fall below the season-onset threshold — not that the air is clean.',
      'Beyond the 16-day weather forecast horizon the model draws from climatology, so the interval widens faster than it looks.',
      'Peat fires can smoulder underground and re-emerge after the declared end date.',
    ],
  };
}

/* --------------------------------------------------------- exhaustion model */

function exhaustionSim(body) {
  const b = body || {};
  const burnRed = Number(b.burn_reduction_pct ?? 0) / 100;
  const restoration = Number(b.restoration_mha_per_yr ?? FUEL.restoration_mha_per_yr);
  const defor = Number(b.deforestation_mha_per_yr ?? FUEL.deforestation_mha_per_yr);
  const horizon = Math.max(1, Math.min(Number(b.horizon_years ?? 300), 300));
  const BASE_YEAR = 2025;
  const startYear = BASE_YEAR + 1;
  const lastYear = BASE_YEAR + horizon;

  const S0 = FUEL.pools.reduce((a, p) => a + p.area_mha, 0);
  const floor = S0 * 0.05;

  // Net annual loss, tuned so the baseline lands near the real model's 2264
  // rather than the illustrative 2094 that was once in the contract.
  const net = Math.max(0, 0.2072 * (1 - burnRed) + 0.55 * defor - 0.60 * restoration);

  // With dS/dt = -net * (S/S0)^0.6, the time to fall to 5% is 1.746*S0/net.
  const yearsToFloor = net > 0 ? (1.746 * S0) / net : Infinity;
  const yearFor = (speed) => {
    const t = yearsToFloor / speed;
    if (!Number.isFinite(t)) return null;
    const y = Math.round(BASE_YEAR + t);
    return y <= lastYear ? y : null;         // horizon-relative, not "never"
  };
  const p10 = yearFor(1.04);                 // fastest depletion, earliest year
  const p50 = yearFor(1.00);
  const p90 = yearFor(0.96);                 // slowest depletion, latest year

  const years = [];
  const t10 = [], t50 = [], t90 = [];
  let s10 = S0, s50 = S0, s90 = S0;
  const step = (S, speed) =>
    Math.max(0, S - net * speed * Math.pow(Math.max(S, 1e-6) / S0, 0.6));
  for (let i = 0; i < horizon; i++) {
    years.push(startYear + i);
    s10 = step(s10, 1.04); s50 = step(s50, 1.00); s90 = step(s90, 0.96);
    t10.push(Number(s10.toFixed(3)));
    t50.push(Number(s50.toFixed(3)));
    t90.push(Number(s90.toFixed(3)));
  }

  const reached = [p10, p50, p90].filter((y) => y !== null).length;
  const neverPct = reached === 3 ? 0.0 : reached === 0 ? 100.0 : Number(((3 - reached) / 3 * 100).toFixed(1));

  const idx2050 = years.indexOf(2050);
  const remainingFrac = (idx2050 >= 0 ? t50[idx2050] : t50[t50.length - 1]) / S0;

  return {
    exhaustion_year: { p10, p50, p90, never_pct: neverPct },
    trajectories: {
      years, p10: t10, p50: t50, p90: t90,
      unit: 'Mha', baseline_mha: Number(S0.toFixed(3)),
    },
    per_pool: FUEL.pools.map((p, i) => {
      // the slower-burning pools outlast the aggregate, some past the horizon
      const poolYear = p50 === null ? null
        : (() => {
          const y = p50 + Math.round((0.8 - p.burn_susceptibility) * 60) + i * 4;
          return y <= lastYear ? y : null;
        })();
      return {
        id: p.id,
        label: p.label,
        baseline_mha: p.area_mha,
        exhaustion_year_p50: poolYear,
        never_pct: poolYear === null ? 100.0 : 0.0,
        remaining_mha_2050: Number((p.area_mha * Math.min(1, remainingFrac * 1.15)).toFixed(4)),
        burn_cycles: Number((p.burn_susceptibility * 1.9).toFixed(2)),
        estimated: true,
      };
    }),
    headlines: {   // CONTRACT M4, MOCK values
      peat: { label: 'Peat', exhaustion_year: { p10: p10 && p10 - 111, p50: p50 && p50 - 111, p90: p90 && p90 - 111, never_pct: neverPct }, remaining_mha_2050: 3.21 },
      forest: { label: 'Forest', exhaustion_year: { p10, p50, p90, never_pct: neverPct }, remaining_mha_2050: 12.3 },
    },
    cumulative_burned_mha_by_2050: Number((0.363 * 24 * (1 - burnRed)).toFixed(3)),
    cumulative_pm25_kt: Number((198 * horizon * (1 - burnRed)).toFixed(1)),
    assumptions: {
      n_sims: b.n_sims ?? 2000,
      horizon_years: horizon,
      base_year: BASE_YEAR,
      restoration_mha_per_yr: restoration,
      burn_reduction_pct: Number(b.burn_reduction_pct ?? 0),
      deforestation_mha_per_yr: defor,
      seed: b.seed ?? null,
      theta_depletion: 0.6,
      exhaustion_threshold_fraction: 0.05,
      peat_is_irreversible: true,
      estimated: true,
    },
    caveats: [
      'MOCK RESPONSE: these numbers come from a front-end fixture, not from the model.',
      'Peat that burns does not grow back on a human timescale. Restoration here means rewetting and blocking canals, which lowers the chance of burning — it does not rebuild the carbon.',
      '"Exhausted" is defined as burnable stock falling below 5% of its base-year value; smaller fires would continue after that date.',
      'never_pct is relative to the horizon. 100% means no run exhausted the fuel before the run stopped, not that the fuel lasts forever.',
      'A projection running past 2250 is an arithmetic exercise, not a forecast.',
    ],
  };
}

/* --------------------------------------------------------------- dispersion */

function aqiCategory(c) {
  if (c <= 12) return ['Good', Math.round(c * 50 / 12)];
  if (c <= 35.4) return ['Moderate', Math.round(50 + (c - 12) * 50 / 23.4)];
  if (c <= 55.4) return ['Unhealthy for Sensitive Groups', Math.round(100 + (c - 35.4) * 50 / 20)];
  if (c <= 150.4) return ['Unhealthy', Math.round(150 + (c - 55.4) * 50 / 95)];
  if (c <= 250.4) return ['Very Unhealthy', Math.round(200 + (c - 150.4) * 100 / 100)];
  return ['Hazardous', Math.min(500, Math.round(300 + (c - 250.4) * 200 / 250))];
}

function dispersionSim(body) {
  const b = body || {};
  const sources = b.sources || [];
  const ws = Number(b.wind_speed_ms ?? 3.5);
  const wd = Number(b.wind_dir_deg ?? 140);      // direction the wind comes FROM
  const rain = Number(b.rain_mm ?? 0);
  const wash = Math.exp(-rain / 8);

  const totalPm = sources.reduce((a, s) => {
    const pool = FUEL.pools.find((p) => p.id === s.pool) || FUEL.pools[0];
    // tonnes of fuel x g PM2.5 per kg fuel == kg of PM2.5
    return a + (s.area_ha || 0) * pool.fuel_load_t_ha * pool.pm25_ef_g_kg;
  }, 0);

  const cLat = sources.length
    ? sources.reduce((a, s) => a + s.lat, 0) / sources.length : -1.6;
  const cLon = sources.length
    ? sources.reduce((a, s) => a + s.lon, 0) / sources.length : 113.2;

  const toRad = Math.PI / 180;
  const downwind = (wd + 180) % 360;               // direction the smoke travels
  const ux = Math.sin(downwind * toRad), uy = Math.cos(downwind * toRad);

  const receptors = RECEPTORS.map((r) => {
    const dy = (r.lat - cLat) * 111;
    const dx = (r.lon - cLon) * 111 * Math.cos(cLat * toRad);
    const dist = Math.hypot(dx, dy);
    const along = dx * ux + dy * uy;
    const cross = Math.abs(-dx * uy + dy * ux);
    // Deliberately crude: a downwind decay with lateral spread, scaled so the
    // fixture lands in a plausible range. The real endpoint does Pasquill-Gifford.
    let conc = 0;
    if (along > 0) {
      const spread = 0.32 * along + 45;               // km, widens downwind
      conc = 1400 * (totalPm / 2.4e8)
        * Math.exp(-along / 520)
        * Math.exp(-0.5 * Math.pow(cross / spread, 2))
        * (3.4 / Math.max(ws, 0.8))
        * wash;
    }
    conc = Math.min(conc, 3200);
    const [category, aqi] = aqiCategory(conc);
    return {
      name: r.name, lat: r.lat, lon: r.lon,
      pm25_ugm3: Number(conc.toFixed(1)),
      aqi, category,
      arrival_hours: along > 0 ? Number((along * 1000 / Math.max(ws, 0.8) / 3600).toFixed(1)) : null,
      distance_km: Number(dist.toFixed(0)),
    };
  });

  // concentration bands as tapering plume polygons downwind of the centroid
  const bands = [250, 150, 55, 35];
  const scale = Math.min(2.2, Math.max(0.35, totalPm / 1.5e8));
  const features = bands.map((band, i) => {
    const len = (1.0 + i * 1.5) * 190 * scale * Math.min(1.9, Math.max(0.6, ws / 3.5));
    const wid = (0.55 + i * 0.45) * 95 * scale;
    const pts = [];
    const N = 26;
    // leaf-shaped: widens fast near the source, tapers to a point downwind
    const halfWidth = (t) => wid * Math.sqrt(Math.max(t, 0.02)) * Math.pow(1 - t, 0.45);
    for (let k = 0; k <= N; k++) {
      const t = k / N;
      pts.push(along2ll(cLat, cLon, ux, uy, len * t, halfWidth(t)));
    }
    for (let k = N; k >= 0; k--) {
      const t = k / N;
      pts.push(along2ll(cLat, cLon, ux, uy, len * t, -halfWidth(t)));
    }
    pts.push(pts[0]);
    return {
      type: 'Feature',
      properties: {
        pm25_ugm3: band,
        aqi: aqiCategory(band)[1],
        category: aqiCategory(band)[0],
        label: `>= ${band} ug/m3`,
        estimated: true,
      },
      geometry: { type: 'Polygon', coordinates: [pts] },
    };
  });

  return {
    receptors,
    plume: { type: 'FeatureCollection', features },
    total_pm25_kg: Math.round(totalPm),
    source_summary: {
      n_sources: sources.length,
      total_area_ha: Number(sources.reduce((a, x) => a + (x.area_ha || 0), 0).toFixed(1)),
      total_pm25_kg: Math.round(totalPm),
      total_pm25_t: Math.round(totalPm / 1000),
    },
    conditions: {
      wind_speed_ms: ws,
      wind_dir_deg: wd,
      wind_blows_toward_deg: (wd + 180) % 360,
      mixing_height_m: Number(b.mixing_height_m ?? 800),
      rain_mm: rain,
      stability_class: 'D',
    },
    live: false,
    caveats: [
      'MOCK RESPONSE: these numbers come from a front-end fixture, not from the model.',
      'A single steady-state Gaussian plume with one wind vector cannot represent a month of shifting winds. Treat the shape as illustrative.',
      'Real receptor PM2.5 also comes from traffic, industry and fires outside Kalimantan.',
    ],
  };
}

function along2ll(lat0, lon0, ux, uy, alongKm, crossKm) {
  const dx = ux * alongKm + -uy * crossKm;
  const dy = uy * alongKm + ux * crossKm;
  const lat = lat0 + dy / 111;
  const lon = lon0 + dx / (111 * Math.cos(lat0 * Math.PI / 180));
  return [Number(lon.toFixed(3)), Number(lat.toFixed(3))];
}

/* ------------------------------------------------------------------- meteo */

function meteoSim(params) {
  const start = params.get('start') || new Date().toISOString().slice(0, 10);
  const end = params.get('end') || addDays(start, 13);
  const n = Math.max(1, Math.min(60, daysBetween(start, end) + 1));
  const rng = mulberry32(42);
  const dates = [], tmax = [], tmin = [], rain = [], rh = [], wsp = [], wdir = [];
  for (let i = 0; i < n; i++) {
    dates.push(addDays(start, i));
    tmax.push(Number((31.5 + rng() * 3).toFixed(1)));
    tmin.push(Number((22.8 + rng() * 1.6).toFixed(1)));
    rain.push(Number((Math.pow(rng(), 3) * 18).toFixed(1)));
    rh.push(Math.round(64 + rng() * 22));
    wsp.push(Number((2.2 + rng() * 3.4).toFixed(1)));
    // SE-ish flow: during the burning season the smoke runs northwest, over
    // Sarawak and the Malacca Strait. That is why Singapore gets it.
    wdir.push(Math.round(120 + rng() * 45));
  }
  return {
    live: false,
    lat: Number(params.get('lat') || -2.2),
    lon: Number(params.get('lon') || 113.9),
    dates, tmax_c: tmax, tmin_c: tmin, rain_mm: rain, rh_pct: rh,
    wind_speed_ms: wsp, wind_dir_deg: wdir,
    source: 'mock-fixture',
    fallback_reason: 'Mock mode is on (?mock=1): no network call was made.',
  };
}

/* ------------------------------------------------------------- provenance */

const PROVENANCE = {
  generated: '2026-01-01',
  note: "MOCK PROVENANCE. status is one of measured / estimated / synthesised / modelled. "
    + "'measured' means the number is taken from a published measurement. 'estimated' means it "
    + "is derived from published figures under a stated assumption. 'synthesised' means it was "
    + "generated to be consistent with published aggregates and corresponds to no observation. "
    + "'modelled' means it is a parameter of the model, not a property of the world.",
  datasets: [
    {
      id: 'seasons_burned_area', label: 'Burned area, Indonesia', coverage: '1997-2025',
      source: 'MOCK — stands in for KLHK SiPongi annual recapitulation',
      url: null, status: 'measured',
      notes: 'In the real build this is the official Indonesian series.',
    },
    {
      id: 'seasons_kalimantan_share', label: 'Kalimantan share of burned area',
      coverage: '1997-2025', source: 'MOCK — derived from the national series',
      url: null, status: 'estimated',
      notes: 'Derived by applying a fixed provincial share to the national total.',
    },
    {
      id: 'enso_oni', label: 'Oceanic Nino Index', coverage: '1997-2025',
      source: 'MOCK — stands in for NOAA CPC ONI', url: null, status: 'measured', notes: null,
    },
    {
      id: 'excess_deaths', label: 'Excess deaths attributable to smoke', coverage: '1997-2025',
      source: 'MOCK — stands in for Koplitz et al. 2016 ERL', url: null, status: 'estimated',
      notes: 'Modelled attributions from smoke exposure, not counted deaths.',
    },
    {
      id: 'peat_extent', label: 'Peatland extent, Kalimantan', coverage: '2025',
      source: 'MOCK — stands in for KLHK / Wetlands International', url: null,
      status: 'measured', notes: null,
    },
    {
      id: 'fuel_loads', label: 'Fuel loads and emission factors', coverage: 'n/a',
      source: 'MOCK — stands in for Akagi et al. 2011 emission factors', url: null,
      status: 'estimated', notes: null,
    },
    {
      id: 'burn_susceptibility', label: 'Burn susceptibility per fuel pool', coverage: 'n/a',
      source: 'Model parameter, not a measurement', url: null, status: 'modelled',
      notes: 'Relative annual probability a hectare of each pool burns in a drought year, '
        + 'normalised so drained peat is 1.0. It is fitted, not observed.',
    },
    {
      id: 'fire_clusters', label: 'Per-year fire clusters (map layer)', coverage: '1997-2025',
      source: 'Synthesised from published annual aggregates', url: null, status: 'synthesised',
      notes: 'THESE ARE NOT FIRE DETECTIONS. Every point, size, date, duration and FRP value in '
        + 'the map layer was generated to be consistent with the published annual totals. They '
        + 'show roughly where and when fires of that scale occurred; no individual marker '
        + 'corresponds to an observed fire.',
    },
  ],
};

/* ------------------------------------------------------------------ router */

/* ---------------------------------------------------- forecast odds (M2) */

function oddsMock() {
  const season = seasonSim({ scenario: 'baseline', oni: 1.1 });
  const daily = season.daily_end_probability;
  const still = (iso) => {
    let v = 1;
    for (const d of daily) { if (d.date <= iso) v = d.p_still_burning; else break; }
    return v;
  };
  const asOf = season.as_of;
  const d0 = new Date(asOf + 'T00:00:00Z');
  const markets = [];
  for (let k = 0; k < 3; k++) {
    const end = new Date(Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth() + 1 + k, 0)).toISOString().slice(0, 10);
    const yes = Number((1 - still(end)).toFixed(3));
    markets.push({ id: `end-by-${end}`, question: `MOCK: Will the haze season end by ${end}?`, threshold_date: end, yes, no: Number((1 - yes).toFixed(3)) });
  }
  const outcomes = [];
  let cur = addDays(asOf, 1);
  for (let w = 0; w < 10; w++) {
    const e = addDays(cur, 6);
    const p = daily.filter((d) => d.date >= cur && d.date <= e).reduce((a, d) => a + d.p_end, 0);
    outcomes.push({ label: `MOCK wk ${w + 1}`, start: cur, end: e, p: Number(p.toFixed(3)) });
    cur = addDays(e, 1);
  }
  return {
    as_of: asOf,
    season_active: true,
    n_sims: 4321,
    markets,
    distribution: { bucket: 'week', outcomes },
    resolution: {
      definition: 'MOCK DEFINITION: the season has ended when the illustrative hotspot count stays below an illustrative threshold for an illustrative number of days.',
      source: 'MOCK SOURCE — not a real resolution source',
    },
    model_error_days: 12.3,
    caveats: ['MOCK RESPONSE: odds from a front-end fixture, not from the model.'],
  };
}

function oddsHistoryMock() {
  const odds = oddsMock();
  const series = [];
  const rng = mulberry32(4242);
  const n = 30;
  for (let i = n - 1; i >= 0; i--) {
    const asOf = addDays(odds.as_of, -i);
    const drift = i / n;
    const m = {};
    odds.markets.forEach((mk, j) => {
      const v = mk.yes - drift * (0.35 - j * 0.08) + (rng() - 0.5) * 0.04;
      m[mk.id] = Number(Math.min(0.99, Math.max(0.01, v)).toFixed(3));
    });
    series.push({ as_of: asOf, p50_end_date: addDays(asOf, 30 + Math.round(drift * 9)), markets: m });
  }
  return {
    series,
    method: 'MOCK METHOD: an invented random walk, not a recomputation.',
    computed_at: odds.as_of,
    caveats: ['MOCK RESPONSE: this history is invented for layout only.'],
  };
}

/* ------------------------------------------------ this week (M5), MOCK */

function weekSeries(endIso, peakIso, peak, base, rng, stepH = 1) {
  const end = Date.parse(endIso);
  const pk = Date.parse(peakIso);
  const out = [];
  for (let t = end - 7 * 86400000; t <= end; t += stepH * 3600000) {
    const dd = (t - pk) / 86400000;
    const v = base + (peak - base) * Math.exp(-(dd * dd) / 3.2) + (rng() - 0.5) * 8;
    out.push({ t: new Date(t).toISOString(), v: Math.max(5, Math.round(v)) });
  }
  return out;
}
const maxPt = (series) => series.reduce((b, p) => (p.v > b.v ? p : b), series[0]);

function observedMock() {
  const end = new Date();
  end.setUTCMinutes(0, 0, 0);
  const endIso = end.toISOString();
  const day = (n, h) => new Date(end.getTime() - n * 86400000 + (h - end.getUTCHours()) * 3600000).toISOString();
  const rng = mulberry32(77);
  const sg = [['sg-west', 'MOCK West', 205, 1.27, 103.70], ['sg-north', 'MOCK North', 168, 1.41, 103.82],
    ['sg-central', 'MOCK Central', 174, 1.35, 103.82], ['sg-east', 'MOCK East', 151, 1.35, 103.94], ['sg-south', 'MOCK South', 162, 1.29, 103.82]];
  const places = sg.map(([id, name, peak, lat, lon]) => {
    const series = weekSeries(endIso, day(2, 8), peak, 70, rng);
    const pm = weekSeries(endIso, day(2, 6), Math.round(peak * 1.4), 40, rng);
    return {
      id, name, country: 'SG', lat, lon,
      official: { index: 'PSI', averaging: '24h', series, max: maxPt(series), band: peak > 200 ? 'Very Unhealthy' : 'Unhealthy',
        source: 'MOCK — stands in for NEA via data.gov.sg', licence: 'Singapore Open Data Licence v1.0', url: 'https://data.gov.sg/' },
      pm25: { averaging: '1h', series: pm, max: maxPt(pm), source: 'MOCK — stands in for NEA' },
      modelled: null,
    };
  });
  const cell = weekSeries(endIso, day(3, 6), 272, 110, mulberry32(9));
  const klang = weekSeries(endIso, day(3, 9), 248, 105, mulberry32(10));
  for (const [id, name, series] of [['my-kuala-lumpur', 'MOCK Kuala Lumpur', cell], ['my-shah-alam', 'MOCK Shah Alam', cell],
    ['my-petaling-jaya', 'MOCK Petaling Jaya', cell], ['my-klang', 'MOCK Klang', klang]]) {
    places.push({
      id, name, country: 'MY', lat: 3.1, lon: 101.6,
      official: null, pm25: null,
      modelled: { index: 'US AQI (modelled, CAMS)', series, pm25_series: [], max: maxPt(series), band: 'Very Unhealthy',
        source: 'MOCK — stands in for CAMS via Open-Meteo', grid_km: 40 },
    });
  }
  const w = places[0].official.max;
  return {
    as_of: endIso,
    window: { start: new Date(end.getTime() - 7 * 86400000).toISOString(), end: endIso },
    places,
    headline: {
      text: `MOCK: Singapore’s West region reached a 24-hour PSI of ${w.v}, “Very Unhealthy”, on ${new Date(w.t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'Asia/Singapore' })}. Measured by NEA.`,
      place_id: 'sg-west', index: 'PSI', measured: true, value: w.v, t: w.t, band: 'Very Unhealthy',
    },
    caveats: ['MOCK RESPONSE: invented readings for layout only. Not NEA, DOE or CAMS data.'],
  };
}

function hotspotsMock() {
  const rng = mulberry32(5);
  const by_day = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
    by_day.push({ date: d, kalimantan: Math.round(1200 + rng() * 900), sumatra: Math.round(500 + rng() * 500), peninsular_malaysia: Math.round(rng() * 12) });
  }
  return {
    as_of: new Date().toISOString(),
    source: 'MOCK — stands in for NASA FIRMS VIIRS NRT',
    attribution: 'MOCK attribution: NASA FIRMS (LANCE NRT).',
    count_by_region: { kalimantan: 12345, sumatra: 6789, peninsular_malaysia: 42 },
    frp_gw_by_region: { kalimantan: 98.7, sumatra: 54.3, peninsular_malaysia: 0.1 },
    by_day,
    clusters: { type: 'FeatureCollection', features: [] },
    caveats: ['MOCK RESPONSE: invented detection counts.'],
  };
}

function notFound(path) {
  const e = new Error(`mock: ${path} is not available in v1 mode`);
  e.name = 'ApiError';
  e.status = 404;
  e.detail = 'Not Found';
  return e;
}

/** Strip every optional CONTRACT_V2 field, to prove the page renders without them. */
function stripV2(path, res) {
  if (res && Array.isArray(res.seasons)) {
    for (const s of res.seasons) for (const k of Object.keys(s)) if (k.endsWith('_range')) delete s[k];
  }
  if (res && res.season) for (const k of Object.keys(res.season)) if (k.endsWith('_range')) delete res.season[k];
  if (res && 'drought_readiness' in res) delete res.drought_readiness;
  if (res && 'headlines' in res) delete res.headlines;
  if (res && Array.isArray(res.drivers)) {
    res.drivers = res.drivers.filter((d) => d.id !== 'iod');
    const tot = res.drivers.reduce((a, d) => a + d.contribution, 0) || 1;
    for (const d of res.drivers) d.contribution = Number((d.contribution / tot).toFixed(3));
  }
  return res;
}

export function mockResponse(path, method = 'GET', body = null, opts = {}) {
  const res = route(path, method, body, opts);
  return opts.v1 ? stripV2(path, res) : res;
}

function route(path, method = 'GET', body = null, opts = {}) {
  const [clean, query] = path.split('?');
  const params = new URLSearchParams(query || '');
  if (clean === '/api/now/observed') { if (opts.v1) throw notFound(clean); return observedMock(); }
  if (clean === '/api/now/hotspots') { if (opts.v1) throw notFound(clean); return hotspotsMock(); }
  if (clean === '/api/forecast/odds') { if (opts.v1) throw notFound(clean); return oddsMock(); }
  if (clean === '/api/forecast/history') { if (opts.v1) throw notFound(clean); return oddsHistoryMock(); }

  if (clean === '/api/health') {
    return { status: 'ok', version: '0.0.0-mock', live_weather_available: false, mock: true };
  }
  if (clean === '/api/history') {
    const { caveats, ...summary } = SEASON_SUMMARY;
    return {
      seasons: SEASONS.map((s) => ({ ...s })),
      summary,
      caveats,          // sibling of summary, as the live route returns it
    };
  }
  if (/^\/api\/history\/\d{4}$/.test(clean)) {
    const year = Number(clean.split('/').pop());
    const season = SEASONS.find((s) => s.year === year);
    if (!season) {
      const e = new Error(`no season for ${year}`);
      e.status = 404;
      throw e;
    }
    return { season: { ...season }, clusters: clustersFor(year) };
  }
  if (clean === '/api/provenance') return JSON.parse(JSON.stringify(PROVENANCE));
  if (clean === '/api/regions') return JSON.parse(JSON.stringify(REGIONS));
  if (clean === '/api/fuel') return JSON.parse(JSON.stringify(FUEL));
  if (clean === '/api/receptors') return RECEPTORS.map((r) => ({ ...r }));
  if (clean === '/api/simulate/season') return seasonSim(body);
  if (clean === '/api/simulate/exhaustion') return exhaustionSim(body);
  if (clean === '/api/dispersion') return dispersionSim(body);
  if (clean === '/api/meteo') return meteoSim(params);

  throw new Error(`mock fixture has no route for ${method} ${path}`);
}
