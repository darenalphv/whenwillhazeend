/**
 * field.js — sampling the model's output in space and time.
 *
 * Concentration: frames[].values on the model grid (nodes from lon_min to
 * lon_max, rows south to north). Between hourly frames the field is linearly
 * interpolated in time; in space, bilinearly. Outside the grid it is zero:
 * the model says nothing there, and the view must not either.
 *
 * Wind: the v2 `wind` block (or the adapter's uniform stand-in), bilinear in
 * space over the same bbox, linear between height levels and between frames.
 */

export class Field {
  constructor(resp) {
    this.grid = resp.grid;
    this.frames = resp.frames;
    this.bg = resp.scale?.background_pm25 ?? 12;
    const g = this.grid;
    this.nx = g.nx;
    this.ny = g.ny;
    this.lon0 = g.lon_min;
    this.lat0 = g.lat_min;
    this.lon1 = g.lon_max;
    this.lat1 = g.lat_max;
    this.dlon = (g.lon_max - g.lon_min) / (g.nx - 1);
    this.dlat = (g.lat_max - g.lat_min) / (g.ny - 1);
    this.hours = this.frames.map((f) => f.hour);
    this.tEnd = this.hours[this.hours.length - 1] || 0;
    const n = g.nx * g.ny;
    this.cur = new Float32Array(n);       // blended surface values at this.t
    this.curAloft = resp.hasAloft ? new Float32Array(n) : null;
    this.t = -1;
    this.wind = new Wind(resp.wind, g, this.hours);
  }

  /** Frame bracket for model time t (hours): [k0, k1, a]. */
  bracket(t) {
    const h = this.hours;
    if (t <= h[0]) return [0, 0, 0];
    const last = h.length - 1;
    if (t >= h[last]) return [last, last, 0];
    let lo = 0;
    let hi = last;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (h[mid] <= t) lo = mid; else hi = mid;
    }
    const span = h[hi] - h[lo] || 1;
    return [lo, hi, (t - h[lo]) / span];
  }

  /** Blend frames into this.cur for time t. Cheap: nx*ny lerps. */
  setTime(t) {
    if (t === this.t) return;
    this.t = t;
    const [k0, k1, a] = this.bracket(t);
    const A = this.frames[k0].values;
    const B = this.frames[k1].values;
    const cur = this.cur;
    const b = 1 - a;
    for (let i = 0; i < cur.length; i += 1) cur[i] = A[i] * b + B[i] * a;
    if (this.curAloft) {
      const AA = this.frames[k0].values_aloft;
      const BB = this.frames[k1].values_aloft;
      for (let i = 0; i < cur.length; i += 1) {
        const x = AA ? AA[i] : 0;
        const y = BB ? BB[i] : 0;
        this.curAloft[i] = x * b + y * a;
      }
    }
    this.wind.setTime(t);
  }

  /** Scalar frame properties interpolated at t (mixing height etc). */
  scalarAt(t, key) {
    const [k0, k1, a] = this.bracket(t);
    const x = this.frames[k0][key];
    const y = this.frames[k1][key];
    if (typeof x !== 'number') return x;
    return x + ((typeof y === 'number' ? y : x) - x) * a;
  }

  /** Bilinear sample of an nx*ny array; 0 outside the grid. */
  sampleArr(arr, lon, lat) {
    const fx = (lon - this.lon0) / this.dlon;
    const fy = (lat - this.lat0) / this.dlat;
    if (fx < 0 || fy < 0 || fx > this.nx - 1 || fy > this.ny - 1) return 0;
    let ix = fx | 0;
    let iy = fy | 0;
    if (ix >= this.nx - 1) ix = this.nx - 2;
    if (iy >= this.ny - 1) iy = this.ny - 2;
    const tx = fx - ix;
    const ty = fy - iy;
    const i = iy * this.nx + ix;
    const a = arr[i] + (arr[i + 1] - arr[i]) * tx;
    const b = arr[i + this.nx] + (arr[i + this.nx + 1] - arr[i + this.nx]) * tx;
    return a + (b - a) * ty;
  }

  sample(lon, lat) { return this.sampleArr(this.cur, lon, lat); }

  sampleAloft(lon, lat) { return this.curAloft ? this.sampleArr(this.curAloft, lon, lat) : 0; }

  inside(lon, lat) {
    return lon >= this.lon0 && lon <= this.lon1 && lat >= this.lat0 && lat <= this.lat1;
  }

  /** Exact frame values (for verification and readouts). */
  sampleFrame(k, lon, lat) { return this.sampleArr(this.frames[k].values, lon, lat); }
}

export class Wind {
  constructor(w, grid, hours) {
    this.nx = w.nx;
    this.ny = w.ny;
    this.levels = w.levels_m.slice();
    this.frames = w.frames;
    this.lon0 = grid.lon_min;
    this.lat0 = grid.lat_min;
    this.dlon = (grid.lon_max - grid.lon_min) / (w.nx - 1);
    this.dlat = (grid.lat_max - grid.lat_min) / (w.ny - 1);
    this.hours = hours;
    const n = w.nx * w.ny * this.levels.length;
    this.u = new Float32Array(n);
    this.v = new Float32Array(n);
    this.t = -1;
    this.per = w.nx * w.ny;
  }

  setTime(t) {
    if (t === this.t) return;
    this.t = t;
    const h = this.hours;
    const nf = this.frames.length;
    let k0 = 0;
    let k1 = 0;
    let a = 0;
    if (t > h[0]) {
      const last = Math.min(h.length, nf) - 1;
      if (t >= h[last]) { k0 = last; k1 = last; } else {
        k0 = 0;
        while (k0 < last && h[k0 + 1] <= t) k0 += 1;
        k1 = Math.min(k0 + 1, last);
        a = (t - h[k0]) / ((h[k1] - h[k0]) || 1);
      }
    }
    const A = this.frames[Math.min(k0, nf - 1)];
    const B = this.frames[Math.min(k1, nf - 1)];
    const b = 1 - a;
    for (let i = 0; i < this.u.length; i += 1) {
      this.u[i] = A.u[i] * b + B.u[i] * a;
      this.v[i] = A.v[i] * b + B.v[i] * a;
    }
  }

  /**
   * Wind at (lon, lat, z metres AGL) at the current time. Writes [u, v] into
   * out. Positions outside the wind bbox clamp to its edge.
   */
  sample(lon, lat, z, out) {
    let fx = (lon - this.lon0) / this.dlon;
    let fy = (lat - this.lat0) / this.dlat;
    const mx = this.nx - 1;
    const my = this.ny - 1;
    fx = fx < 0 ? 0 : (fx > mx ? mx : fx);
    fy = fy < 0 ? 0 : (fy > my ? my : fy);
    let ix = fx | 0;
    let iy = fy | 0;
    if (ix >= mx) ix = mx - 1;
    if (iy >= my) iy = my - 1;
    const tx = fx - ix;
    const ty = fy - iy;

    const L = this.levels;
    let l0 = 0;
    let l1 = 0;
    let lw = 0;
    if (L.length > 1) {
      if (z <= L[0]) { l0 = 0; l1 = 0; } else if (z >= L[L.length - 1]) {
        l0 = L.length - 1; l1 = l0;
      } else {
        while (l0 < L.length - 2 && L[l0 + 1] <= z) l0 += 1;
        l1 = l0 + 1;
        lw = (z - L[l0]) / (L[l1] - L[l0]);
      }
    }
    const i = iy * this.nx + ix;
    const bil = (arr, base) => {
      const j = base + i;
      const a = arr[j] + (arr[j + 1] - arr[j]) * tx;
      const c = arr[j + this.nx] + (arr[j + this.nx + 1] - arr[j + this.nx]) * tx;
      return a + (c - a) * ty;
    };
    const b0 = l0 * this.per;
    let u = bil(this.u, b0);
    let v = bil(this.v, b0);
    if (lw > 0) {
      const b1 = l1 * this.per;
      u += (bil(this.u, b1) - u) * lw;
      v += (bil(this.v, b1) - v) * lw;
    }
    out[0] = u;
    out[1] = v;
  }
}
