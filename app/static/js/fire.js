/**
 * fire.js — the hero's ember bed.
 *
 * This is the page's one authored motion moment, and it is driven by the model
 * rather than decorating it. Intensity comes from the season model's KBDI
 * driver: the Keetch-Byram Drought Index is literally "how ready the peat is to
 * keep burning", scaled 0..800. The hero's own reason line says "peat is dry to
 * roughly 1.0 m depth" — this draws that sentence. A wet year burns down to a
 * dull red bed; a drought year throws embers.
 *
 * Performance notes, because this runs behind the number people came for:
 *   - one canvas, bounded to the hero card, never full-page
 *   - particles are drawn from two pre-rendered sprites via drawImage; no
 *     per-particle gradients or shadowBlur in the loop
 *   - device pixel ratio capped at 2
 *   - the loop stops when the hero scrolls out of view or the tab is hidden
 *   - prefers-reduced-motion never starts the loop; it paints one static frame
 */

const MAX_DPR = 2;
const EMBERS = 96;        // hard ceiling; live count scales with intensity
const SMOKE = 14;
const KBDI_MAX = 800;     // Keetch-Byram upper bound (imperial scale)

/** Pre-render a radial sprite once, then blit it. */
function sprite(size, stops) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  for (const [at, color] of stops) grad.addColorStop(at, color);
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  return c;
}

function rand(a, b) { return a + Math.random() * (b - a); }

export class EmberBed {
  constructor(host) {
    this.host = host;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'emberbed';
    this.canvas.setAttribute('aria-hidden', 'true');
    host.prepend(this.canvas);
    this.ctx = this.canvas.getContext('2d');

    this.w = 0; this.h = 0;
    this.intensity = 0.55;      // 0..1, set by setIntensity()
    this.target = 0.55;
    this.running = false;
    this.visible = true;
    this.raf = 0;
    this.last = 0;
    this.t = 0;

    this.emberSprite = sprite(24, [
      [0, 'rgba(255,240,214,1)'],
      [0.28, 'rgba(255,164,74,0.92)'],
      [0.62, 'rgba(255,102,38,0.28)'],
      [1, 'rgba(255,92,30,0)'],
    ]);
    this.smokeSprite = sprite(128, [
      [0, 'rgba(126,112,104,0.40)'],
      [0.55, 'rgba(96,86,80,0.16)'],
      [1, 'rgba(78,70,66,0)'],
    ]);

    this.embers = Array.from({ length: EMBERS }, () => this.spawnEmber(true));
    this.smoke = Array.from({ length: SMOKE }, () => this.spawnSmoke(true));

    this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    this.onMotionChange = () => (this.reduced.matches ? this.stop(true) : this.start());
    this.reduced.addEventListener('change', this.onMotionChange);

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.resize();

    this.io = new IntersectionObserver(
      ([e]) => { this.visible = e.isIntersecting; this.visible ? this.start() : this.stop(); },
      { threshold: 0.01 },
    );
    this.io.observe(host);

    this.onVis = () => (document.hidden ? this.stop() : this.start());
    document.addEventListener('visibilitychange', this.onVis);

    if (this.reduced.matches) this.stop(true); else this.start();
  }

  /**
   * @param {object} season a /api/simulate/season response
   * Maps the KBDI driver to flame intensity. Falls back to a mid bed if the
   * model did not report one, rather than inventing a dramatic fire.
   */
  setIntensity(season) {
    const drivers = (season && season.drivers) || [];
    const kbdi = drivers.find((d) => d.id === 'kbdi');
    if (kbdi && Number.isFinite(Number(kbdi.value))) {
      const f = Math.min(1, Math.max(0, Number(kbdi.value) / KBDI_MAX));
      // 0.18 floor so an inactive season still smoulders rather than going black.
      this.target = 0.18 + 0.82 * f;
    }
    if (season && season.active === false) this.target = Math.min(this.target, 0.22);
    if (this.reduced.matches) { this.intensity = this.target; this.paintStatic(); }
  }

  resize() {
    const r = this.host.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    this.w = r.width;
    this.h = r.height;
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(this.h * dpr);
    this.canvas.style.width = `${this.w}px`;
    this.canvas.style.height = `${this.h}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!this.running) this.paintStatic();
  }

  spawnEmber(seeded) {
    const i = this.intensity;
    return {
      x: rand(0, Math.max(1, this.w)),
      y: seeded ? rand(0, Math.max(1, this.h)) : this.h + rand(0, 14),
      // Fast and long-lived enough that heat carries past the number, which is
      // the whole point of putting it there.
      vy: rand(26, 74) * (0.6 + i * 0.8),
      vx: rand(-7, 7),
      size: rand(2.4, 7.5),
      life: 0,
      ttl: rand(3.2, 7.0),
      wob: rand(0, Math.PI * 2),
    };
  }

  spawnSmoke(seeded) {
    return {
      x: rand(-0.1, 1.1) * Math.max(1, this.w),
      y: seeded ? rand(0, Math.max(1, this.h)) : this.h + rand(10, 50),
      vy: rand(5, 15),
      vx: rand(-5, 9),
      size: rand(60, 170),
      life: 0,
      ttl: rand(6, 13),
    };
  }

  start() {
    if (this.running || this.reduced.matches || !this.visible || document.hidden) return;
    this.running = true;
    this.last = performance.now();
    this.raf = requestAnimationFrame((t) => this.frame(t));
  }

  stop(paintStatic) {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (paintStatic) this.paintStatic();
  }

  destroy() {
    this.stop();
    this.ro.disconnect();
    this.io.disconnect();
    document.removeEventListener('visibilitychange', this.onVis);
    this.reduced.removeEventListener('change', this.onMotionChange);
    this.canvas.remove();
  }

  /** The glowing bed at the base of the card. Drawn every frame, cheap. */
  drawBed(ctx, i, t) {
    const bedH = this.h * 0.46;
    const y0 = this.h - bedH;
    const breathe = 0.86 + 0.14 * Math.sin(t * 0.7) * Math.sin(t * 0.31 + 1.1);
    const g = ctx.createLinearGradient(0, this.h, 0, y0);
    g.addColorStop(0, `rgba(255,108,44,${0.38 * i * breathe})`);
    g.addColorStop(0.42, `rgba(214,68,24,${0.17 * i * breathe})`);
    g.addColorStop(1, 'rgba(120,32,10,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, y0, this.w, bedH);

    // Three hot spots along the base, drifting slowly out of phase.
    for (let k = 0; k < 3; k += 1) {
      const cx = this.w * (0.18 + 0.32 * k) + Math.sin(t * 0.23 + k * 2.1) * this.w * 0.06;
      const r = this.w * (0.16 + 0.04 * Math.sin(t * 0.41 + k));
      const rg = ctx.createRadialGradient(cx, this.h + 6, 0, cx, this.h + 6, r);
      rg.addColorStop(0, `rgba(255,150,70,${0.20 * i})`);
      rg.addColorStop(1, 'rgba(255,120,50,0)');
      ctx.fillStyle = rg;
      ctx.fillRect(cx - r, this.h - r, r * 2, r + 12);
    }
  }

  frame(now) {
    if (!this.running) return;
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    this.t += dt;
    // Ease toward the model's intensity so a playground re-run visibly changes
    // the fire rather than snapping.
    this.intensity += (this.target - this.intensity) * Math.min(1, dt * 1.6);

    const ctx = this.ctx;
    const i = this.intensity;
    ctx.clearRect(0, 0, this.w, this.h);

    // Smoke sits behind the embers and reads as haze, not fire.
    ctx.globalCompositeOperation = 'source-over';
    for (const s of this.smoke) {
      s.life += dt;
      if (s.life > s.ttl || s.y < -s.size) Object.assign(s, this.spawnSmoke(false));
      s.y -= s.vy * dt;
      s.x += s.vx * dt;
      const k = s.life / s.ttl;
      const a = Math.sin(Math.PI * Math.min(1, k)) * 0.5 * (0.45 + i * 0.55);
      const sz = s.size * (0.7 + k * 0.7);
      ctx.globalAlpha = a;
      ctx.drawImage(this.smokeSprite, s.x - sz / 2, s.y - sz / 2, sz, sz);
    }
    ctx.globalAlpha = 1;

    this.drawBed(ctx, i, this.t);

    // Embers are additive so overlaps read as heat.
    ctx.globalCompositeOperation = 'lighter';
    const live = Math.round(EMBERS * (0.25 + 0.75 * i));
    for (let n = 0; n < live; n += 1) {
      const e = this.embers[n];
      e.life += dt;
      if (e.life > e.ttl || e.y < -10) Object.assign(e, this.spawnEmber(false));
      e.wob += dt * 2.2;
      e.y -= e.vy * dt;
      e.x += (e.vx + Math.sin(e.wob) * 9) * dt;
      const k = e.life / e.ttl;
      const sz = e.size * (1 - k * 0.45);
      ctx.globalAlpha = Math.sin(Math.PI * k) * (0.35 + i * 0.5);
      ctx.drawImage(this.emberSprite, e.x - sz / 2, e.y - sz / 2, sz, sz);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';

    this.raf = requestAnimationFrame((t) => this.frame(t));
  }

  /**
   * The reduced-motion and paused state. Still a fire — still carries the
   * drought reading — just not a moving one.
   */
  paintStatic() {
    if (!this.ctx || !this.w) return;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    this.drawBed(ctx, this.intensity, 0);
    ctx.globalCompositeOperation = 'lighter';
    for (let n = 0; n < 26; n += 1) {
      const e = this.embers[n];
      ctx.globalAlpha = 0.28 * (0.4 + this.intensity * 0.6);
      ctx.drawImage(this.emberSprite, e.x - e.size / 2, e.y - e.size / 2, e.size, e.size);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }
}
