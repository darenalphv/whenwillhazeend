/**
 * adapter.js — normalise a /api/simulate/haze3d response to the v2 shape.
 *
 * CONTRACT_V2 says: until the model's v2 response is live, adapt v1; and keep
 * the adapter afterwards as the fallback for any extended field the model
 * layer fails to supply. So this runs on every response and fills only what
 * is missing, recording what it filled in `adapted` so the page can say which
 * parts of the picture are the model's and which are the adapter's.
 *
 * The fills, exactly as the contract specifies:
 *   wind        absent -> uniform field from conditions.wind_speed_ms and
 *                         frames[].wind_dir_deg (one level, every frame)
 *   emission_g_s absent -> pm25_kg spread over a 14-day episode
 *   injection_m  absent -> [0, 500] for peat pools, [0, 1500] otherwise
 * Two more, which the contract does not specify and which only drive visuals:
 *   flaming_fraction absent -> 0.1 peat, 0.5 forest, 0.7 scrub/grass
 *   region           absent -> "kalimantan"
 */

const EPISODE_S = 14 * 86400;

const isPeat = (pool) => /peat/i.test(String(pool || ''));

function defaultFlaming(pool) {
  const p = String(pool || '');
  if (/peat/i.test(p)) return 0.1;
  if (/scrub|grass/i.test(p)) return 0.7;
  return 0.5;
}

const finite = (v) => typeof v === 'number' && Number.isFinite(v);

/** Wind "from" direction + speed -> (u east, v north), m/s. */
export function windFromDir(speed, dirFromDeg) {
  const r = (dirFromDeg * Math.PI) / 180;
  return [-speed * Math.sin(r), -speed * Math.cos(r)];
}

function uniformWind(resp) {
  const frames = resp.frames || [];
  const base = resp.conditions?.wind_speed_ms ?? 3;
  const baseDir = resp.conditions?.wind_dir_deg ?? 190;
  return {
    nx: 2,
    ny: 2,
    levels_m: [10],
    frames: frames.map((f) => {
      const spd = finite(f.wind_speed_ms) ? f.wind_speed_ms : base;
      const dir = finite(f.wind_dir_deg) ? f.wind_dir_deg : baseDir;
      const [u, v] = windFromDir(spd, dir);
      return { u: [u, u, u, u], v: [v, v, v, v] };
    }),
  };
}

function validWind(w, nFrames) {
  if (!w || !finite(w.nx) || !finite(w.ny) || w.nx < 2 || w.ny < 2) return false;
  if (!Array.isArray(w.levels_m) || !w.levels_m.length) return false;
  if (!Array.isArray(w.frames) || !w.frames.length) return false;
  const n = w.nx * w.ny * w.levels_m.length;
  const f0 = w.frames[0];
  if (!f0 || !Array.isArray(f0.u) || !Array.isArray(f0.v)) return false;
  if (f0.u.length !== n || f0.v.length !== n) return false;
  // frame count may legitimately differ; we clamp by index. But not zero.
  return nFrames > 0;
}

export function adapt(resp) {
  const adapted = {
    wind: false, emission: false, injection: false, flaming: false, region: false,
  };
  const out = { ...resp };
  out.version = resp.version || 1;

  out.frames = (resp.frames || []).map((f) => ({
    ...f,
    values_aloft: Array.isArray(f.values_aloft) ? f.values_aloft : null,
    aloft_base_m: finite(f.aloft_base_m) ? f.aloft_base_m : null,
    aloft_top_m: finite(f.aloft_top_m) ? f.aloft_top_m : null,
  }));

  if (validWind(resp.wind, out.frames.length)) {
    out.wind = resp.wind;
  } else {
    out.wind = uniformWind(out);
    adapted.wind = true;
  }

  out.sources = (resp.sources || []).map((s) => {
    const o = { ...s };
    if (!finite(o.emission_g_s)) {
      o.emission_g_s = finite(o.pm25_kg) ? (o.pm25_kg * 1000) / EPISODE_S : 0;
      adapted.emission = true;
    }
    if (!Array.isArray(o.injection_m) || o.injection_m.length !== 2
        || !finite(o.injection_m[0]) || !finite(o.injection_m[1])) {
      o.injection_m = isPeat(o.pool) ? [0, 500] : [0, 1500];
      adapted.injection = true;
    }
    if (!finite(o.flaming_fraction)) {
      o.flaming_fraction = defaultFlaming(o.pool);
      adapted.flaming = true;
    }
    o.flaming_fraction = Math.min(1, Math.max(0, o.flaming_fraction));
    if (!o.region) {
      o.region = 'kalimantan';
      adapted.region = true;
    }
    return o;
  });

  out.adapted = adapted;
  out.hasAloft = out.frames.some((f) => f.values_aloft);
  return out;
}
