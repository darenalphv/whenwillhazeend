/**
 * colors.js — AQI bands and the colour ramps the 3D view paints with.
 *
 * Band colours are read from tokens.css at runtime so the simulator cannot
 * drift from the main page. The hex values below are only the fallback if the
 * stylesheet has not applied yet.
 */

export const BANDS = [
  { max: 12.0, name: 'Good', token: '--aqi-good', fallback: '#3E9E6E' },
  { max: 35.4, name: 'Moderate', token: '--aqi-moderate', fallback: '#D4A017' },
  { max: 55.4, name: 'Unhealthy for sensitive groups', token: '--aqi-usg', fallback: '#E07B22' },
  { max: 150.4, name: 'Unhealthy', token: '--aqi-unhealthy', fallback: '#C9332A' },
  { max: 250.4, name: 'Very unhealthy', token: '--aqi-very', fallback: '#7E3B8C' },
  { max: Infinity, name: 'Hazardous', token: '--aqi-hazardous', fallback: '#6A1B2A' },
];

/** The four contour levels the model itself reports (dispersion.CONTOUR_BANDS). */
export const CONTOURS = [35, 55, 150, 250];

export function hexToRgb(hex) {
  const h = String(hex).trim().replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(full, 16);
  if (Number.isNaN(n)) return [128, 128, 128];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function cssVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

let bandRgb = null;
export function bandColors() {
  if (!bandRgb) bandRgb = BANDS.map((b) => hexToRgb(cssVar(b.token, b.fallback)));
  return bandRgb;
}

export function bandIndex(v) {
  for (let i = 0; i < BANDS.length; i += 1) if (v <= BANDS[i].max) return i;
  return BANDS.length - 1;
}

export function bandOf(v) {
  return BANDS[bandIndex(v)];
}

export function bandRgbOf(v) {
  return bandColors()[bandIndex(v)];
}

/**
 * Surface-field LUT: 1024 entries over log10(C) in [0, log10(5000)].
 * Colour is the AQI band with a short blend across each breakpoint so the
 * field reads as a field rather than as stepped terraces; alpha rises from
 * zero at the floor so pale terrain stays visible where the air is clean.
 * Returns Uint8ClampedArray(1024*4) and an index function.
 */
export const LUT_N = 1024;
const LOG_MAX = Math.log10(5000);

export function lutIndex(c) {
  if (!(c > 1)) return 0;
  const i = Math.round((Math.log10(c) / LOG_MAX) * (LUT_N - 1));
  return i < 0 ? 0 : (i >= LUT_N ? LUT_N - 1 : i);
}

export function buildFieldLut(floor) {
  const rgb = bandColors();
  const out = new Uint8ClampedArray(LUT_N * 4);
  const edges = BANDS.slice(0, -1).map((b) => b.max);
  for (let i = 0; i < LUT_N; i += 1) {
    const c = 10 ** ((i / (LUT_N - 1)) * LOG_MAX);
    // band colour with a +-9% (in log space) cross-fade at each breakpoint
    let col = rgb[bandIndex(c)];
    for (let e = 0; e < edges.length; e += 1) {
      const lo = edges[e] / 1.09;
      const hi = edges[e] * 1.09;
      if (c > lo && c < hi) {
        const t = (Math.log(c) - Math.log(lo)) / (Math.log(hi) - Math.log(lo));
        const a = rgb[e];
        const b = rgb[e + 1];
        col = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
        break;
      }
    }
    let alpha = 0;
    if (c >= floor) {
      // log ramp from the floor to ~400 ug/m3, then held
      const t = Math.min(1, Math.log(c / floor) / Math.log(Math.max(400 / floor, 1.5)));
      alpha = 0.10 + 0.42 * t ** 0.85;
      // soften the very edge at the floor so the cut is not a hard line
      const edge = Math.min(1, (c - floor) / (floor * 0.25));
      alpha *= edge;
    }
    out[i * 4] = col[0];
    out[i * 4 + 1] = col[1];
    out[i * 4 + 2] = col[2];
    out[i * 4 + 3] = Math.round(alpha * 255);
  }
  return out;
}
