/**
 * fire.js — the hero's haze veil.
 *
 * The page's one authored motion moment, driven by the model rather than
 * decorating it. A pale sky over a peat-swamp treeline, a low sun, and a warm
 * grey-ochre haze whose density is the season model's own reading:
 *
 *   density = mean of  KBDI / 800                     (how dry the peat is)
 *               and    P(season still burning in 14 days)
 *
 * A wet season leaves the far treeline crisp and the sun a pale glare. A bad
 * one erases the far forest, mutes the near one, and turns the sun into the
 * flat orange disc anyone who has lived through a haze season will recognise.
 *
 * Since CONTRACT_V2 M1 this is also the instant-paint layer under the 3D
 * hero and its permanent fallback: no WebGL, reduced motion, Save-Data, or a
 * failed load all leave this veil showing.
 *
 * Performance, because it runs behind the number people came for:
 *   - one canvas, bounded to the hero plate, never full-page
 *   - sky, sun and treelines are rendered once per (size, density) into two
 *     cached bitmaps; each frame is 2 blits plus ~18 pre-rendered sprites
 *   - no per-frame gradients, filters or shadowBlur
 *   - backing store capped at ~2.6 megapixels
 *   - the loop stops offscreen, on tab hide, and while the 3D scene covers it
 *   - prefers-reduced-motion never starts the loop: one static frame that
 *     still carries the reading
 */

const KBDI_MAX = 800;        // Keetch–Byram upper bound (imperial scale)
const MAX_PIXELS = 2.6e6;    // backing-store budget
const EASE_PER_S = 0.9;      // how quickly the haze settles to a new reading

/* ------------------------------------------------------------ helpers */

function css(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
function hex(h) {
  const s = h.replace('#', '');
  const v = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}
function mix(a, b, t) {
  const k = Math.min(1, Math.max(0, t));
  return a.map((v, i) => Math.round(v + (b[i] - v) * k));
}
const rgb = (c, a = 1) => (a >= 1 ? `rgb(${c[0]},${c[1]},${c[2]})` : `rgba(${c[0]},${c[1]},${c[2]},${a})`);
const smooth = (e0, e1, x) => {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};

/** Deterministic RNG, so the treeline is the same island every visit. */
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * A peat-swamp canopy: nearly flat, a broken line of rounded crowns with the
 * occasional emergent tree. Returns a Path2D closed to the bottom edge.
 */
function canopy(w, h, baseY, { amp, crownMin, crownMax, emergent, seed }) {
  const r = rng(seed);
  const p = new Path2D();
  p.moveTo(-10, h + 2);
  const ph1 = r() * 6.28, ph2 = r() * 6.28;
  const yAt = (x) => baseY
    + Math.sin(x / (w * 0.21) + ph1) * amp
    + Math.sin(x / (w * 0.07) + ph2) * amp * 0.35;
  let x = -10;
  p.lineTo(x, yAt(x));
  while (x < w + 10) {
    const cw = crownMin + r() * (crownMax - crownMin);
    const tall = r() < emergent ? 1.6 + r() * 1.4 : 1;
    const rise = cw * (0.32 + r() * 0.25) * tall;
    const x1 = x + cw;
    const y0 = yAt(x), y1 = yAt(x1);
    p.bezierCurveTo(x + cw * 0.05, y0 - rise, x1 - cw * 0.05, y1 - rise, x1, y1);
    x = x1;
  }
  p.lineTo(w + 10, h + 2);
  p.closePath();
  return p;
}

/** A soft, wide haze puff, pre-rendered once per tint. */
function puffSprite(color) {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 128;
  const g = c.getContext('2d');
  g.translate(128, 64);
  g.scale(1, 0.5);
  const grad = g.createRadialGradient(0, 0, 0, 0, 0, 128);
  grad.addColorStop(0, rgb(color, 1));
  grad.addColorStop(0.45, rgb(color, 0.62));
  grad.addColorStop(0.8, rgb(color, 0.16));
  grad.addColorStop(1, rgb(color, 0));
  g.fillStyle = grad;
  g.fillRect(-128, -128, 256, 256);
  return c;
}

/* --------------------------------------------------------------- reading */

/**
 * The model reading the veil draws. Exported so the caption under the plate
 * says the same thing the picture shows.
 */
export function hazeReading(season) {
  const out = { density: 0.35, kbdi: null, readiness: null, pStill: null, horizonDays: 14, active: true, basis: [] };
  if (!season) return out;
  const drivers = Array.isArray(season.drivers) ? season.drivers : [];
  const k = drivers.find((d) => d && d.id === 'kbdi');
  // CONTRACT M4: key off drought_readiness; KBDI/800 only when it is absent
  if (Number.isFinite(Number(season.drought_readiness)) && season.drought_readiness !== null) {
    out.readiness = Math.min(1, Math.max(0, Number(season.drought_readiness)));
    out.basis.push(out.readiness);
  } else if (k && Number.isFinite(Number(k.value))) {
    out.kbdi = Number(k.value);
    out.basis.push(Math.min(1, Math.max(0, out.kbdi / KBDI_MAX)));
  }
  const daily = Array.isArray(season.daily_end_probability) ? season.daily_end_probability : [];
  if (daily.length) {
    const i = Math.min(daily.length - 1, out.horizonDays - 1);
    const p = Number(daily[i] && daily[i].p_still_burning);
    if (Number.isFinite(p)) {
      out.pStill = p;
      out.horizonDays = i + 1;
      out.basis.push(Math.min(1, Math.max(0, p)));
    }
  }
  if (out.basis.length) out.density = out.basis.reduce((a, b) => a + b, 0) / out.basis.length;
  if (season.active === false) { out.active = false; out.density = Math.min(out.density, 0.08); }
  out.density = Math.min(0.96, Math.max(0.04, out.density));
  return out;
}

/* ------------------------------------------------------------- the veil */

export class HazeVeil {
  /**
   * @param {HTMLElement} host   the hero plate (position: relative)
   * @param {object} opts        { panel, land } elements used for layout
   */
  constructor(host, { panel = null, land = null } = {}) {
    this.host = host;
    this.panel = panel;
    this.land = land;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'plate__veil';
    this.canvas.setAttribute('aria-hidden', 'true');
    host.prepend(this.canvas);
    this.ctx = this.canvas.getContext('2d', { alpha: false });
    if (!this.ctx) throw new Error('2D canvas unavailable');

    this.density = 0.12;     // the page opens nearly clear …
    this.target = 0.12;      // … and the model's reading rolls in
    this.built = -1;
    this.running = false;
    this.visible = true;
    this.covered = false;
    this.raf = 0;
    this.last = 0;
    this.t = 0;
    this.frameN = 0;
    this.fps = 0;
    this.frames = 0;          // total frames drawn, for inspection
    this._fpsN = 0;
    this._fpsT = 0;

    this.pal = this.readPalette();
    this.sprites = [];

    this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    this.onMotion = () => { if (this.reduced.matches) this.stop(true); else this.start(); };
    this.reduced.addEventListener('change', this.onMotion);

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);

    this.io = new IntersectionObserver(([e]) => {
      this.visible = e.isIntersecting;
      if (this.visible) this.start(); else this.stop();
    }, { threshold: 0 });
    this.io.observe(host);

    this.onVis = () => (document.hidden ? this.stop() : this.start());
    document.addEventListener('visibilitychange', this.onVis);

    this.resize();
    if (this.reduced.matches) this.paintStatic(); else this.start();
    window.__hazeVeil = this;   // inspection hook for fps checks; harmless
  }

  readPalette() {
    return {
      skyClear: hex(css('--sky-clear', '#EEF0EA')),
      skyClearLow: hex(css('--sky-clear-low', '#F6F0E3')),
      skyHaze: hex(css('--sky-haze', '#E2D7C4')),
      skyHazeLow: hex(css('--sky-haze-low', '#D5C2A2')),
      veil: hex(css('--veil', '#E6DFD2')),
      veilDense: hex(css('--veil-dense', '#C9B694')),
      sunClear: hex(css('--sun-clear', '#FFF7E6')),
      sunHaze: hex(css('--sun-haze', '#CF5F2B')),
      near: hex(css('--land-near', '#3B392E')),
      mid: hex(css('--land-mid', '#6E6B58')),
      far: hex(css('--land-far', '#9C9783')),
    };
  }

  /** Set the model reading. Returns the reading so callers can caption it. */
  setReading(season) {
    const r = hazeReading(season);
    this.target = r.density;
    if (this.reduced.matches || !this.running) {
      // No roll-in without motion: show the reading as it is.
      this.density = this.target;
      this.paintStatic();
    }
    return r;
  }

  /** The 3D scene is fully over the veil: stop spending frames under it. */
  setCovered(covered) {
    this.covered = !!covered;
    if (this.covered) this.stop(); else this.start();
  }

  /* ---------------------------------------------------------- layout */

  resize() {
    const r = this.host.getBoundingClientRect();
    if (!r.width || !r.height) return;
    let dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (r.width * r.height * dpr * dpr > MAX_PIXELS) dpr = Math.sqrt(MAX_PIXELS / (r.width * r.height));
    this.dpr = dpr;
    this.w = r.width;
    this.h = r.height;
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const landR = this.land ? this.land.getBoundingClientRect() : null;
    this.landTop = landR ? landR.top - r.top : this.h * 0.72;
    this.landH = Math.max(80, this.h - this.landTop);
    const panelR = this.panel ? this.panel.getBoundingClientRect() : null;
    const panelRight = panelR && panelR.width ? panelR.right - r.left : 0;
    const roomRight = this.w - panelRight;

    if (roomRight > this.w * 0.3) {
      // wide: the sun hangs in the open sky beside the answer panel
      this.sunR = Math.max(26, Math.min(56, this.w * 0.032));
      this.sunX = panelRight + roomRight * 0.55;
      this.sunY = this.landTop - Math.min(this.landTop * 0.2, 150);
    } else {
      // narrow: the sun sits low over the treeline, below the panel
      this.sunR = 20;
      this.sunX = this.w * 0.74;
      this.sunY = this.landTop + this.landH * 0.24;
    }

    const lh = this.landH;
    this.ridges = {
      far: canopy(this.w, this.h, this.h - lh * 0.6, { amp: 5, crownMin: 16, crownMax: 34, emergent: 0.06, seed: 11 }),
      mid: canopy(this.w, this.h, this.h - lh * 0.4, { amp: 4, crownMin: 12, crownMax: 26, emergent: 0.09, seed: 23 }),
      near: canopy(this.w, this.h, this.h - lh * 0.17, { amp: 3, crownMin: 9, crownMax: 20, emergent: 0.14, seed: 37 }),
    };
    this.horizonY = this.h - lh * 0.6;

    this.base = this.base || document.createElement('canvas');
    this.nearC = this.nearC || document.createElement('canvas');
    for (const c of [this.base, this.nearC]) {
      c.width = this.canvas.width;
      c.height = this.canvas.height;
    }
    this.seedSprites();
    this.built = -1;
    if (!this.running) this.paintStatic();
  }

  seedSprites() {
    const r = rng(101);
    const w = this.w, h = this.h, hy = this.horizonY;
    const s = [];
    // the sky veil: wide, slow, behind the answer panel
    for (let i = 0; i < 6; i += 1) {
      s.push({ band: 'sky', x: r() * w, y: hy * (0.15 + r() * 0.75), sw: w * (0.7 + r() * 0.6), sh: hy * (0.35 + r() * 0.3), v: 3 + r() * 4, ph: r() * 6.28 });
    }
    // the horizon bank: lies across the far and mid forest
    for (let i = 0; i < 7; i += 1) {
      s.push({ band: 'bank', x: r() * w, y: hy + this.landH * (r() * 0.25), sw: w * (0.35 + r() * 0.45), sh: 60 + r() * 70, v: 5 + r() * 7, ph: r() * 6.28 });
    }
    // low smoke drifting in front of the near trees
    for (let i = 0; i < 5; i += 1) {
      s.push({ band: 'low', x: r() * w, y: h - this.landH * (0.05 + r() * 0.2), sw: w * (0.3 + r() * 0.35), sh: 40 + r() * 40, v: 9 + r() * 9, ph: r() * 6.28 });
    }
    this.sprites = s;
  }

  /* ---------------------------------------------------------- painting */

  /** Rebuild the cached sky+sun+far/mid canopy, and the near canopy, for density d. */
  build(d) {
    const P = this.pal;
    const b = this.base.getContext('2d');
    b.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    const veil = mix(P.veil, P.veilDense, d);

    const top = mix(P.skyClear, P.skyHaze, d);
    const low = mix(P.skyClearLow, P.skyHazeLow, d);
    const g = b.createLinearGradient(0, 0, 0, this.horizonY + 20);
    g.addColorStop(0, rgb(top));
    g.addColorStop(1, rgb(low));
    b.fillStyle = g;
    b.fillRect(0, 0, this.w, this.h);

    // sun: a white glare in clean air (cached), a flat orange disc in smoke
    // (drawn per frame, above the sky veil, so the haze cannot wash it out)
    const glare = 1 - smooth(0.05, 0.55, d);
    if (glare > 0.01) {
      const halo = b.createRadialGradient(this.sunX, this.sunY, this.sunR * 0.6, this.sunX, this.sunY, this.sunR * 4);
      halo.addColorStop(0, rgb(P.sunClear, 0.85 * glare));
      halo.addColorStop(1, rgb(P.sunClear, 0));
      b.fillStyle = halo;
      b.fillRect(this.sunX - this.sunR * 4, this.sunY - this.sunR * 4, this.sunR * 8, this.sunR * 8);
    }
    const hazed = smooth(0.18, 0.85, d);
    this.sunFill = rgb(mix(P.sunClear, P.sunHaze, hazed), 0.55 + 0.45 * hazed);

    // aerial perspective: the further canopy disappears first
    const aFar = 1 - Math.exp(-(0.4 + 2.9 * d));
    const aMid = 1 - Math.exp(-(0.2 + 1.6 * d));
    const aNear = 1 - Math.exp(-(0.05 + 0.6 * d));
    b.fillStyle = rgb(mix(P.far, low, aFar));
    b.fill(this.ridges.far);
    b.fillStyle = rgb(veil, 0.12 + 0.4 * d);
    b.fillRect(0, this.horizonY - 40, this.w, this.h);
    b.fillStyle = rgb(mix(P.mid, veil, aMid));
    b.fill(this.ridges.mid);

    const n = this.nearC.getContext('2d');
    n.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    n.clearRect(0, 0, this.w, this.h);
    n.fillStyle = rgb(mix(P.near, veil, aNear));
    n.fill(this.ridges.near);

    this.puff = puffSprite(veil);
    this.built = d;
  }

  draw(d) {
    if (Math.abs(this.built - d) > 0.004) {
      // During a roll-in, rebuild every third frame; the drifting puffs hide the steps.
      if (this.built < 0 || this.frameN % 3 === 0 || !this.running) this.build(d);
    }
    const ctx = this.ctx;
    const dpr = this.dpr;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(this.base, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const t = this.t;
    const aSky = 0.04 + 0.26 * d;
    const aBank = 0.1 + 0.6 * d;
    const aLow = 0.03 + 0.32 * d;
    const nearTop = Math.max(0, Math.floor((this.h - this.landH * 0.45) * dpr));

    ctx.globalAlpha = aSky;
    for (const s of this.sprites) {
      if (s.band !== 'sky') continue;
      const y = s.y + Math.sin(t * 0.13 + s.ph) * 6;
      ctx.drawImage(this.puff, s.x - s.sw / 2, y - s.sh / 2, s.sw, s.sh);
    }
    ctx.globalAlpha = 1;
    ctx.fillStyle = this.sunFill;
    ctx.beginPath();
    ctx.arc(this.sunX, this.sunY, this.sunR, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = aBank;
    for (const s of this.sprites) {
      if (s.band !== 'bank') continue;
      const y = s.y + Math.sin(t * 0.13 + s.ph) * 6;
      ctx.drawImage(this.puff, s.x - s.sw / 2, y - s.sh / 2, s.sw, s.sh);
    }
    ctx.globalAlpha = 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(this.nearC, 0, nearTop, this.nearC.width, this.nearC.height - nearTop,
      0, nearTop, this.nearC.width, this.nearC.height - nearTop);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = aLow;
    for (const s of this.sprites) {
      if (s.band !== 'low') continue;
      const y = s.y + Math.sin(t * 0.2 + s.ph) * 4;
      ctx.drawImage(this.puff, s.x - s.sw / 2, y - s.sh / 2, s.sw, s.sh);
    }
    ctx.globalAlpha = 1;
    this.frames += 1;
  }

  paintStatic() {
    if (!this.w || !this.ctx) return;
    this.build(this.density);
    this.draw(this.density);
  }

  /* ---------------------------------------------------------- the loop */

  start() {
    if (this.running || this.reduced.matches || !this.visible || this.covered || document.hidden || !this.w) return;
    this.running = true;
    this.last = performance.now();
    this._fpsT = this.last;
    this._fpsN = 0;
    this.raf = requestAnimationFrame((n) => this.frame(n));
  }

  stop(paint) {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.fps = 0;
    if (paint) this.paintStatic();
  }

  frame(now) {
    if (!this.running) return;
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    this.t += dt;
    this.frameN += 1;
    this.density += (this.target - this.density) * Math.min(1, dt * EASE_PER_S);

    const w = this.w;
    for (const s of this.sprites) {
      s.x += s.v * dt;
      if (s.x - s.sw / 2 > w) s.x = -s.sw / 2;
    }
    this.draw(this.density);

    this._fpsN += 1;
    if (now - this._fpsT >= 1000) {
      this.fps = Math.round((this._fpsN * 1000) / (now - this._fpsT));
      this._fpsN = 0;
      this._fpsT = now;
    }
    this.raf = requestAnimationFrame((n) => this.frame(n));
  }

  destroy() {
    this.stop();
    this.ro.disconnect();
    this.io.disconnect();
    document.removeEventListener('visibilitychange', this.onVis);
    this.reduced.removeEventListener('change', this.onMotion);
    this.canvas.remove();
  }
}
