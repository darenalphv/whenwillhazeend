/**
 * particles.js — smoke as particles advected by the model's wind.
 *
 * What the particles are allowed to do (CONTRACT_V2, visual truth rules):
 *  - move with the API's `wind` field (bilinear in space, linear between
 *    height levels and between frames) — nothing else steers them;
 *  - be released from each source at a rate proportional to `emission_g_s`;
 *  - start inside that source's `injection_m` range;
 *  - stay under the frame's mixing height (they ride as a fraction of the
 *    layer depth, so when the layer collapses at night the smoke is squeezed
 *    down with it), unless the response carries an aloft layer;
 *  - take their colour and opacity from the model's local concentration.
 *    They never carry a concentration of their own. Where the model says the
 *    air is clean — including beyond the grid edge, or ahead of the front in
 *    release mode — a particle is invisible.
 *
 * Release is deterministic per slot: slot k is tied to a source by a
 * low-discrepancy draw over the cumulative emission, and is reborn every
 * `lifetime` hours at a fixed phase. Any prefix of the slots is therefore a
 * fair sample of the emission, so the adaptive particle budget can shrink or
 * grow without biasing which fires the smoke comes from.
 */

const A1 = 0.7548776662466927;   // R2 low-discrepancy sequence constants
const A2 = 0.5698402909980532;
const K_H = 6000;                // horizontal eddy diffusivity, m^2/s (visual spread)
const M_PER_DEG_LAT = 110574;

const frac = (x) => x - Math.floor(x);
const gauss = () => (Math.random() + Math.random() + Math.random() - 1.5) * 2;

export class Smoke {
  constructor(maxN) {
    this.maxN = maxN;
    this.n = 0;
    this.lon = new Float32Array(maxN);
    this.lat = new Float32Array(maxN);
    this.eta = new Float32Array(maxN);     // 0..1 position within its layer
    this.layer = new Uint8Array(maxN);     // 0 mixed layer, 1 aloft
    this.alive = new Uint8Array(maxN);
    this.birth = new Float64Array(maxN);   // compared against computed births; f32 would never match
    this.phase = new Float32Array(maxN);
    this.src = new Uint16Array(maxN);
    this.pos = new Float32Array(maxN * 3);
    this.col = new Uint8Array(maxN * 4);
    this.rad = new Float32Array(maxN);
    this.w = [0, 0];
    // per-slot texture: size and shade vary so the veil has grain and layers
    this.vsize = new Float32Array(maxN);
    this.vshade = new Float32Array(maxN);
    for (let k = 0; k < maxN; k += 1) {
      this.phase[k] = frac(0.5 + k * A2);
      const h = frac(Math.sin(k * 12.9898) * 43758.5453);
      const g = frac(Math.sin(k * 78.233) * 12345.6789);
      this.vsize[k] = 0.55 + 0.9 * h;
      this.vshade[k] = (g - 0.5) * 26;
    }
  }

  /**
   * field: Field; sources: adapted v2 sources; release: bool; lifetime: h.
   */
  configure({ field, sources, release, lifetime, elev }) {
    this.field = field;
    this.sources = sources;
    this.release = release;
    this.L = lifetime;
    this.elev = elev;
    // cumulative emission -> source per slot
    const e = sources.map((s) => Math.max(0, s.emission_g_s || 0));
    const tot = e.reduce((a, b) => a + b, 0) || 1;
    const cdf = [];
    let acc = 0;
    for (const v of e) { acc += v / tot; cdf.push(acc); }
    for (let k = 0; k < this.maxN; k += 1) {
      const u = frac(0.5 + k * A1);
      let i = 0;
      while (i < cdf.length - 1 && cdf[i] < u) i += 1;
      this.src[k] = i;
    }
    // per-source spawn geometry
    this.sx = sources.map((s) => {
      const sideKm = Math.sqrt(Math.max(1, s.area_ha || 100) / 100);
      return {
        lon: s.lon,
        lat: s.lat,
        sig: Math.max(1.2, sideKm * 0.7) * 1000,           // metres
        z0: s.injection_m[0],
        z1: Math.max(s.injection_m[0] + 1, s.injection_m[1]),
      };
    });
    this.alive.fill(0);
  }

  /** Particle clock -> field time: clamp in release runs, wrap in steady runs. */
  mapT(t) {
    const f = this.field;
    if (t <= 0) return 0;
    if (this.release || !(f.tEnd > 0)) return Math.min(f.tEnd, t);
    return t % f.tEnd;
  }

  mixingAt(t) {
    const f = this.field;
    const tc = this.mapT(t);
    return Math.max(50, f.scalarAt(tc, 'mixing_height_m') || 800);
  }

  aloftAt(t) {
    const f = this.field;
    const tc = this.mapT(t);
    const b = f.scalarAt(tc, 'aloft_base_m');
    const tp = f.scalarAt(tc, 'aloft_top_m');
    return [typeof b === 'number' ? b : 1200, typeof tp === 'number' ? tp : 3000];
  }

  birthOf(k, t) {
    const p = this.phase[k];
    return (Math.floor(t / this.L - p) + p) * this.L;
  }

  spawn(k, tb) {
    const s = this.sx[this.src[k]];
    const cosl = Math.cos((s.lat * Math.PI) / 180);
    this.lon[k] = s.lon + (gauss() * s.sig) / (111320 * cosl);
    this.lat[k] = s.lat + (gauss() * s.sig) / M_PER_DEG_LAT;
    const z = s.z0 + Math.random() * (s.z1 - s.z0);
    const H = this.mixingAt(tb);
    if (this.field.curAloft && z > H) {
      const [ab, at] = this.aloftAt(tb);
      this.layer[k] = 1;
      this.eta[k] = Math.min(1, Math.max(0, (z - ab) / Math.max(1, at - ab)));
    } else {
      this.layer[k] = 0;
      this.eta[k] = Math.min(1, z / H);
    }
    this.birth[k] = tb;
    this.alive[k] = 1;
  }

  /** Advance particle k by dt hours at the wind currently set on field.wind. */
  advect(k, dt, H, ab, at) {
    const wind = this.field.wind;
    const lat = this.lat[k];
    const z = this.layer[k] === 0 ? this.eta[k] * H : ab + this.eta[k] * (at - ab);
    wind.sample(this.lon[k], lat, z, this.w);
    const ds = dt * 3600;
    const sig = Math.sqrt(2 * K_H * ds);
    const cosl = Math.cos((lat * Math.PI) / 180);
    this.lon[k] += (this.w[0] * ds + gauss() * sig) / (111320 * cosl);
    this.lat[k] += (this.w[1] * ds + gauss() * sig) / M_PER_DEG_LAT;
    let e = this.eta[k] + gauss() * 0.12 * Math.sqrt(dt);
    if (e < 0) e = -e;
    if (e > 1) e = 2 - e;
    this.eta[k] = e < 0 ? 0 : (e > 1 ? 1 : e);
    if (!this.field.inside(this.lon[k], this.lat[k])) this.alive[k] = 0;
  }

  /**
   * Rebuild the population for model time t by integrating from t - L,
   * time-major so the wind is set once per step. Used on load, on scrub,
   * on loop, and when the particle budget grows (for slots from..to).
   */
  reset(t, from = 0, to = this.n) {
    const f = this.field;
    if (!f || to <= from) return;
    const L = this.L;
    const dt = Math.max(0.25, L / 140);
    for (let k = from; k < to; k += 1) this.alive[k] = 0;
    const tb = new Float32Array(to - from);
    for (let k = from; k < to; k += 1) {
      tb[k - from] = this.birthOf(k, t);
      this.birth[k] = tb[k - from];
    }
    let tau = t - L;
    // align the integration clock so that the final step lands exactly on t
    const steps = Math.ceil(L / dt);
    tau = t - steps * dt;
    for (let s = 0; s < steps; s += 1) {
      const t1 = tau + dt;
      f.wind.setTime(this.mapT(tau));
      const H = this.mixingAt(tau);
      const [ab, at] = this.aloftAt(tau);
      for (let k = from; k < to; k += 1) {
        const b = tb[k - from];
        if (!this.alive[k]) {
          if (b >= tau && b < t1 && !(this.release && b < 0)) {
            this.spawn(k, b);
            this.advect(k, t1 - b, H, ab, at);
          }
        } else {
          this.advect(k, dt, H, ab, at);
        }
      }
      tau = t1;
    }
    f.wind.t = -1;
    f.wind.setTime(this.mapT(t));
  }

  /** Incremental step to time t (dt hours since the previous call). */
  step(t, dt) {
    if (!this.field || dt <= 0) return;
    const H = this.mixingAt(t);
    const [ab, at] = this.aloftAt(t);
    for (let k = 0; k < this.n; k += 1) {
      const b = this.birthOf(k, t);
      if (b > this.birth[k] + 1e-6) {
        if (this.release && b < 0) { this.alive[k] = 0; this.birth[k] = b; continue; }
        this.spawn(k, b);
        const age = t - b;
        if (age > 0) this.advect(k, Math.min(age, dt), H, ab, at);
      } else if (this.alive[k]) {
        this.advect(k, dt, H, ab, at);
      }
    }
  }

  /**
   * Write render attributes for the first n particles at time t.
   * vex: vertical exaggeration (applied to ground and height alike).
   */
  fill(t, vex) {
    const f = this.field;
    const H = this.mixingAt(t);
    const [ab, at] = this.aloftAt(t);
    const { pos, col, rad } = this;
    const L = this.L;
    const bg = f.bg;
    // Fewer particles must not read as cleaner air: scale per-puff opacity so
    // the veil's overall density is roughly budget-independent. The shading
    // still follows the model's local value; only the grain changes.
    let alive = 0;
    for (let k = 0; k < this.n; k += 1) alive += this.alive[k];
    const gain = Math.min(2.6, Math.max(1, Math.sqrt(9000 / Math.max(1, alive))));
    let visible = 0;
    for (let k = 0; k < this.n; k += 1) {
      const o3 = k * 3;
      const o4 = k * 4;
      if (!this.alive[k]) {
        col[o4 + 3] = 0;
        pos[o3] = 0; pos[o3 + 1] = 0; pos[o3 + 2] = -1e5;
        rad[k] = 0;
        continue;
      }
      const lon = this.lon[k];
      const lat = this.lat[k];
      const aloft = this.layer[k] === 1;
      const zagl = aloft ? ab + this.eta[k] * (at - ab) : this.eta[k] * H;
      pos[o3] = lon;
      pos[o3 + 1] = lat;
      pos[o3 + 2] = (this.elev(lon, lat) + zagl) * vex;
      const age = t - this.birth[k];
      const fadeIn = Math.min(1, age / 0.6);
      const fadeOut = Math.min(1, (L - age) / (L * 0.15));
      const c = aloft ? f.sampleAloft(lon, lat) + bg : f.sample(lon, lat);
      // inline smoke colour: warm grey-ochre, browner with concentration
      const ex = c - bg;
      if (!(ex > 0.5)) {
        col[o4 + 3] = 0;
      } else {
        const tt = Math.min(1, Math.log1p(ex / 6) / 5.0106);   // log1p(900/6)
        // daylight haze: pale warm grey at the edges, ochre-grey where thick
        // tops of the layer catch the light; the base is the dirtier air
        const lift = (aloft ? 10 : 18 * this.eta[k] - 6) + this.vshade[k];
        col[o4] = Math.min(255, Math.max(0, 232 - 72 * tt + lift));
        col[o4 + 1] = Math.min(255, Math.max(0, 224 - 84 * tt + lift));
        col[o4 + 2] = Math.min(255, Math.max(0, 206 - 104 * tt + lift * 0.8));
        const a = Math.min(200, 255 * Math.max(0, fadeIn * fadeOut) * (0.04 + 0.30 * tt) * gain);
        col[o4 + 3] = a;
        if (a > 3) visible += 1;
      }
      // puffs widen as they age, as a plume does
      rad[k] = (9000 + 21000 * Math.min(1, age / 30)) * this.vsize[k];
    }
    this.visible = visible;
  }
}
