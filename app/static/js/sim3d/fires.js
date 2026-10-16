/**
 * fires.js — each source drawn as a burning landscape, not a marker.
 *
 * Three systems per source, all scaled from the source's own fields:
 *   - flame core + ground glow: size from emission_g_s, height and brightness
 *     from flaming_fraction. Peat smoulder is low, wide, smoky, little flame;
 *     flaming forest is taller and brighter.
 *   - a smoke column that climbs to the top of injection_m and bends over as
 *     the wind takes it (the regional smoke particles take over from there);
 *   - embers, rising and fading, more of them the more flaming the fire.
 *
 * The flicker and the column run on wall-clock time, not model time: they
 * are how a fire looks from minute to minute, which the hourly model does not
 * resolve. Every source's phase comes from its coordinates so nothing pulses
 * in unison. Under reduced motion all of it is a still snapshot.
 */

const frac = (x) => x - Math.floor(x);

export class Fires {
  constructor({ column = 14, embers = 10 } = {}) {
    this.M = column;
    this.E = embers;
  }

  configure(sources, elev) {
    this.src = sources;
    this.elev = elev;
    const n = sources.length;
    const maxE = Math.max(1, ...sources.map((s) => s.emission_g_s || 0));
    this.info = sources.map((s) => {
      const ff = s.flaming_fraction ?? 0.2;
      const e = Math.max(0, s.emission_g_s || 0);
      const rel = Math.sqrt(e / maxE);                     // 0..1 by emission
      return {
        lon: s.lon,
        lat: s.lat,
        ff,
        rel,
        ph: frac(Math.sin(s.lon * 12.9898 + s.lat * 78.233) * 43758.5453) * 6.2831,
        top: Math.max(150, s.injection_m?.[1] ?? 600),
        sumatra: s.region === 'sumatra',
        on: Array.isArray(s.on) ? s.on : null,   // [start h, end h] in "this week" mode
        ground: 0,
      };
    });
    this.flame = { pos: new Float32Array(n * 3 * 3), col: new Uint8Array(n * 3 * 4), rad: new Float32Array(n * 3), n: n * 3 };
    this.glow = { pos: new Float32Array(n * 3), col: new Uint8Array(n * 4), rad: new Float32Array(n), n };
    this.col = { pos: new Float32Array(n * this.M * 3), col: new Uint8Array(n * this.M * 4), rad: new Float32Array(n * this.M), n: n * this.M };
    this.emb = { pos: new Float32Array(n * this.E * 3), col: new Uint8Array(n * this.E * 4), rad: new Float32Array(n * this.E), n: n * this.E };
    this.refreshGround();
  }

  refreshGround() {
    if (!this.info) return;
    for (const f of this.info) f.ground = this.elev ? this.elev(f.lon, f.lat) : 0;
  }

  /**
   * rt: wall-clock seconds (fixed under reduced motion); vex: vertical
   * exaggeration; wind: (lon, lat, z, out) sampler for where the column bends.
   * still: true -> no flicker.
   */
  update(rt, vex, windAt, still, night = 0.5, tModel = 0) {
    const { flame, glow, col, emb, M, E } = this;
    const w = [0, 0];
    for (let i = 0; i < this.info.length; i += 1) {
      const f = this.info[i];
      // lit only on the days FIRMS detected it, with a 3 h fade either side
      const act = f.on
        ? Math.max(0, Math.min(1, (tModel - f.on[0]) / 3, (f.on[1] - tModel) / 3))
        : 1;
      const g = (f.ground + 40) * vex;
      const flick = still ? 1 : 1 + 0.28 * (0.6 * Math.sin(rt * 5.3 + f.ph) + 0.4 * Math.sin(rt * 12.6 + f.ph * 1.7));
      const breathe = still ? 1 : 1 + 0.12 * Math.sin(rt * 1.1 + f.ph);
      const size = (2200 + 5200 * f.rel) * (0.15 + 0.85 * act);   // metres
      const peat = 1 - f.ff;

      // ground glow: wide and dull for smoulder, tighter and hotter for flame
      glow.pos[i * 3] = f.lon; glow.pos[i * 3 + 1] = f.lat; glow.pos[i * 3 + 2] = g;
      glow.rad[i] = size * (2.2 + 1.6 * peat) * breathe;
      glow.col[i * 4] = 214; glow.col[i * 4 + 1] = 92 + 40 * f.ff; glow.col[i * 4 + 2] = 30;
      glow.col[i * 4 + 3] = (50 + 80 * f.rel) * (0.75 + 0.25 * breathe) * (0.55 + 0.45 * night) * act;

      // flame core: three stacked tongues, taller with flaming fraction
      for (let k = 0; k < 3; k += 1) {
        const j = i * 3 + k;
        const hk = (k / 2) * (150 + 900 * f.ff) * (still ? 1 : (1 + 0.35 * Math.sin(rt * (6 + k * 2.3) + f.ph + k)));
        flame.pos[j * 3] = f.lon; flame.pos[j * 3 + 1] = f.lat; flame.pos[j * 3 + 2] = g + hk * vex;
        flame.rad[j] = size * (0.55 - k * 0.14) * (0.5 + 0.6 * f.ff + 0.2 * f.rel) * flick;
        flame.col[j * 4] = 255;
        flame.col[j * 4 + 1] = Math.min(255, 120 + 45 * k + 40 * f.ff);
        flame.col[j * 4 + 2] = 30 + 30 * k * f.ff;
        flame.col[j * 4 + 3] = Math.min(255, (150 + 80 * f.ff) * (k === 0 ? 1 : 0.8) * Math.min(1.2, flick)) * act;
      }

      // the column: rises to injection top, widens, bends downwind
      windAt(f.lon, f.lat, f.top, w);
      const sp = Math.hypot(w[0], w[1]) || 1;
      const bend = Math.min(16000, 2600 * sp);           // metres of lean at the top
      const cosl = Math.cos((f.lat * Math.PI) / 180);
      const period = 9 - 3 * f.ff;                         // seconds per puff
      for (let k = 0; k < M; k += 1) {
        const j = i * M + k;
        const a = frac(rt / period + k / M + f.ph * 0.159);
        const rise = 1 - (1 - a) ** 2.2;                   // decelerates toward the top
        const z = f.top * rise;
        const lean = bend * a ** 2;
        col.pos[j * 3] = f.lon + ((w[0] / sp) * lean) / (111320 * cosl);
        col.pos[j * 3 + 1] = f.lat + ((w[1] / sp) * lean) / 110574;
        col.pos[j * 3 + 2] = g + z * vex;
        col.rad[j] = size * (0.6 + 0.5 * peat) * (1 + 2.6 * a);
        // flaming smoke is darker (black carbon); smoulder is paler grey
        const shade = 58 + 62 * peat + 60 * a;
        col.col[j * 4] = shade + 8; col.col[j * 4 + 1] = shade; col.col[j * 4 + 2] = shade - 10;
        const fadeIn = Math.min(1, a / 0.08);
        col.col[j * 4 + 3] = 255 * fadeIn * (1 - a) ** 1.3 * (0.40 + 0.35 * f.rel) * act;
      }

      // embers: more of them when flaming; rise and drift a little
      const nE = Math.round(E * (0.25 + 0.75 * f.ff));
      for (let k = 0; k < E; k += 1) {
        const j = i * E + k;
        if (k >= nE) { emb.col[j * 4 + 3] = 0; emb.pos[j * 3 + 2] = -1e5; continue; }
        const per = 2.4 + (k % 3) * 0.7;
        const a = frac(rt / per + k * 0.618 + f.ph);
        const hgt = (120 + 700 * f.ff) * a;
        const sw = Math.sin(rt * 3 + k * 2.1 + f.ph) * size * 0.25 * a;
        emb.pos[j * 3] = f.lon + (sw + w[0] * 200 * a) / (111320 * cosl);
        emb.pos[j * 3 + 1] = f.lat + ((k % 2 ? 1 : -1) * size * 0.2 * a + w[1] * 200 * a) / 110574;
        emb.pos[j * 3 + 2] = g + hgt * vex;
        emb.rad[j] = 1.3 + (k % 3) * 0.5;
        emb.col[j * 4] = 255; emb.col[j * 4 + 1] = 150 + 60 * (1 - a); emb.col[j * 4 + 2] = 40;
        emb.col[j * 4 + 3] = 255 * (1 - a) ** 0.8 * (still ? 0.8 : 1) * act;
      }
    }
  }
}
