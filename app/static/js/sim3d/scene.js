/**
 * scene.js — the 3D haze scene, shared by the simulator page and the hero
 * embed. One Scene owns one MapLibre map, one deck.gl overlay (interleaved,
 * so smoke is depth-tested against the terrain), the particle systems and
 * the draped surface field. It holds no page UI; callers subscribe to
 * `onFrame` and drive it with play/pause/seek.
 *
 * Time: `t` is model time in hours (0 .. last frame hour). In steady-state
 * runs playback loops and particles keep their own unwrapped clock so the
 * smoke never visibly resets at the loop; in release runs playback stops at
 * the end and every scrub re-integrates the particles from their births.
 */

import { loadLibs } from './libs.js';
import { buildStyle, skySpec } from './basemap.js';
import { registerProtocol, buildElevation, flatElevation, terrainStats } from './elevation.js';
import { adapt } from './adapter.js';
import { Field } from './field.js';
import { Smoke } from './particles.js';
import { Fires } from './fires.js';
import { Surface } from './surface.js';
import { buildLayers } from './layers.js';
import { bandRgbOf, bandOf, cssVar } from './colors.js';
import { readingAt } from './now.js';

export const BUDGETS = {
  low: { maxN: 5000, startN: 3500, minN: 1200, column: 8, embers: 5, contours: false, surfacePx: 256, surfaceHz: 6 },
  med: { maxN: 6500, startN: 4500, minN: 1500, column: 12, embers: 8, contours: true, surfacePx: 320, surfaceHz: 10 },
  high: { maxN: 16000, startN: 9000, minN: 2000, column: 14, embers: 10, contours: true, surfacePx: 384, surfaceHz: 12 },
};

/*
 * The default framing looks across the Strait of Malacca from behind the
 * peninsula: Kuala Lumpur / Selangor and Singapore in the foreground, Sumatra's
 * fires to the right, Kalimantan's in the distance — so the smoke crosses
 * toward the viewer's own city.
 */
export const VIEWS = {
  home: { center: [104.9, 0.2], zoom: 5.75, pitch: 66, bearing: 118 },
  singapore: { center: [103.6, 0.55], zoom: 7.2, pitch: 62, bearing: 196 },
  kalimantan: { center: [112.6, -0.9], zoom: 5.9, pitch: 56, bearing: -18 },
  sumatra: { center: [102.3, -0.4], zoom: 6.5, pitch: 60, bearing: 28 },
};
VIEWS.overview = VIEWS.home;
VIEWS.strait = VIEWS.singapore;

const EMPHASIS = new Set(['Kuching', 'Sibu', 'Miri', 'Kuala Lumpur', 'Singapore', 'Batam']);
const HOME = new Set(['Kuala Lumpur', 'Singapore']);

export class Scene {
  constructor(container, opts = {}) {
    this.el = container;
    this.o = {
      interactive: true,
      budget: 'high',
      labels: true,
      receptors: true,
      orbit: false,
      vex: 10,
      speed: 2,                 // model hours per wall-clock second
      reduced: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      view: VIEWS.home,
      ...opts,
    };
    this.B = BUDGETS[this.o.budget] || BUDGETS.high;
    this.t = 0;
    this.clock = 0;
    this.playing = false;
    this.speed = this.o.speed;
    this.vex = this.o.vex;
    this.show = { smoke: true, fires: true, field: true, contours: this.B.contours };
    this.onFrame = null;
    this.onReceptorClick = null;
    this.notices = [];
    this.rt = 0;
    this.raf = 0;
    this.inView = true;
    this.perf = { ema: 16.7, frames: 0, fps: 0, lastShrink: 0, fpsWin: [], lastAdapt: 0 };
    this.elev = flatElevation;
    this.markers = [];
    this.destroyed = false;
  }

  /* ------------------------------------------------------------ setup */

  async init() {
    const { maplibregl, deck } = await loadLibs();
    if (this.destroyed) throw new Error('destroyed');
    this.maplibregl = maplibregl;
    this.deck = deck;
    registerProtocol(maplibregl);
    const { style, fallback, reason } = await buildStyle({ vex: this.vex, labels: this.o.labels });
    if (fallback) this.notices.push(reason);
    const small = this.el.clientWidth < 640;
    const v = this.o.view;
    this.map = new maplibregl.Map({
      container: this.el,
      style,
      center: small && v === VIEWS.home ? [104.2, 0.75] : v.center,
      zoom: v.zoom - (small ? 0.75 : 0),
      pitch: v.pitch,
      bearing: v.bearing,
      maxPitch: 78,
      minZoom: 3.2,
      maxZoom: 11,
      interactive: this.o.interactive,
      attributionControl: { compact: true },
      fadeDuration: 0,
      canvasContextAttributes: { antialias: true },
    });
    if (this.o.interactive) {
      this.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    }
    // Host composition: shift the camera's focus clear of the host's own
    // panels (e.g. the hero's answer card) without cropping the canvas.
    if (this.o.padding) this.map.setPadding({ top: 0, right: 0, bottom: 0, left: 0, ...this.o.padding });
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Object.assign(new Error('The map style did not finish loading within 20 s.'), { what: 'Basemap' })), 20000);
      this.map.once('load', () => { clearTimeout(timer); resolve(); });
      this.map.on('error', (e) => {
        const msg = String(e?.error?.message || '');
        if (/hazedem|elevation-tiles/.test(msg) || e?.sourceId === 'dem' || e?.sourceId === 'dem-hs') return;
      });
    });
    this.overlay = new deck.MapboxOverlay({ interleaved: true, layers: [] });
    this.map.addControl(this.overlay);
    const beforeId = this.map.getLayer('boundary_3') ? 'boundary_3' : (this.map.getLayer('haze-label-country') ? 'haze-label-country' : undefined);
    this.surface = new Surface(this.map, {
      contours: this.B.contours, beforeId, maxPx: this.B.surfacePx,
      feather: this.o.fieldFeather || 0, opacity: this.o.fieldOpacity ?? 0.88,
    });
    this.smoke = new Smoke(this.B.maxN);
    this.smoke.n = this.B.startN;
    this.fires = new Fires({ column: this.B.column, embers: this.B.embers });

    this._vis = () => this.schedule();
    document.addEventListener('visibilitychange', this._vis);
    if ('IntersectionObserver' in window) {
      this.io = new IntersectionObserver((ents) => {
        this.inView = ents.some((e) => e.isIntersecting);
        this.schedule();
      }, { threshold: 0.01 });
      this.io.observe(this.el);
    }
    this.map.on('move', () => { if (!this.raf) this.renderStill(); });
    return this;
  }

  /* ------------------------------------------------------------- data */

  setData(resp) {
    const data = adapt(resp);
    this.mode = 'model';
    this.noField = false;
    this.surface.setVisible(this.show.field);
    this.surface.setContoursVisible(this.show.contours);
    this.week = null;
    this.show.smoke = this.showSmokePref ?? true;
    this.data = data;
    this.field = new Field(data);
    this.release = !!data.conditions?.release_from_start;
    const f = this.field;
    // lifetime: long enough to cross most of the domain at this wind
    const g = data.grid;
    const diagKm = Math.hypot((g.lon_max - g.lon_min) * 111 * Math.cos(((g.lat_min + g.lat_max) / 2) * Math.PI / 180), (g.lat_max - g.lat_min) * 111);
    const u = Math.max(1, data.conditions?.wind_speed_ms || 3);
    this.lifetime = Math.max(18, Math.min(60, (diagKm * 0.35) / (u * 3.6)));
    this.smoke.configure({ field: f, sources: data.sources, release: this.release, lifetime: this.lifetime, elev: this.elev });
    this.fires.configure(data.sources, this.elev);
    this.sumatra = data.sources.filter((s) => s.region === 'sumatra');
    this.surface.setField(f);
    this.t = 0;
    this.clock = 0;
    f.setTime(0);
    this.smoke.reset(this.clock);
    this.surface.update(true);
    this.buildMarkers();
    this.loadElevation(g);
    this.renderStill();
    this.schedule();
    return data;
  }

  /**
   * "This week": the CAMS field (modelled) and FIRMS fires (measured). No
   * wind comes with it, so no advected particles — see now.js.
   */
  setWeek(week) {
    this.mode = 'week';
    this.week = week;
    const data = {
      ...week.field,
      hasAloft: false,
      // a still, zero wind: used only to keep the fire columns upright
      wind: { nx: 2, ny: 2, levels_m: [10], frames: [{ u: [0, 0, 0, 0], v: [0, 0, 0, 0] }] },
      adapted: {},
    };
    this.data = data;
    this.field = new Field(data);
    this.release = false;
    this.show.smoke = false;
    this.fires.configure(week.sources, this.elev);
    this.sumatra = [];
    this.surface.setField(this.field);
    // No haze field (e.g. no CAMS licence): draw none, and keep the sky a
    // neutral overcast rather than letting it read as clean air.
    this.noField = !week.hasField;
    this.surface.setVisible(!this.noField && this.show.field);
    this.surface.setContoursVisible(!this.noField && this.show.contours);
    this.t = 0;
    this.clock = 0;
    this.field.setTime(0);
    this.surface.update(true);
    this.buildMarkers();
    this.loadElevation(week.field.grid);
    this.renderStill();
    this.schedule();
    return data;
  }

  async loadElevation(g) {
    try {
      const pad = 0.5;
      const el = await buildElevation([g.lon_min - pad, g.lat_min - pad, g.lon_max + pad, g.lat_max + pad]);
      if (this.destroyed) return;
      if (el.failed === el.total && el.total > 0) {
        this.notices.push('Terrain heights could not be fetched; smoke is drawn over a flat surface.');
        return;
      }
      this.elev = el;
      this.smoke.elev = el;
      this.fires.elev = el;
      this.fires.refreshGround();
      this.renderStill();
    } catch {
      this.notices.push('Terrain heights could not be fetched; smoke is drawn over a flat surface.');
    }
  }

  /* ---------------------------------------------------------- markers */

  buildMarkers() {
    for (const m of this.markers) m.marker.remove();
    this.markers = [];
    if (!this.o.receptors) return;
    if (this.mode === 'week') { this.buildPlaceMarkers(); return; }
    let rec = this.data.receptor_series || [];
    if (this.o.receptors === 'home') rec = rec.filter((r) => HOME.has(r.name));
    // Neighbourhood receptors (Selangor towns, Singapore's regions) sit
    // within a few km of KL / Singapore: pinned only when zoomed in.
    const local = (n) => /^Singapore .|^(Shah Alam|Klang|Petaling Jaya)$/.test(n);
    for (const r of rec) {
      const em = EMPHASIS.has(r.name);
      const below = r.name === 'Batam' || r.name === 'Jambi';
      const m = this.makePin(r.name, em, below, () => this.onReceptorClick?.(r));
      m.marker.setLngLat([r.lon, r.lat]).addTo(this.map);
      m.tag.textContent = 'µg/m³ · modelled';
      this.markers.push({ ...m, r, primary: !local(r.name) });
    }
    this.watchPinZoom();
  }

  watchPinZoom() {
    if (!this._zoomPins) {
      this._zoomPins = () => {
        const near = this.map.getZoom() >= 8.3;
        for (const m of this.markers) m.el.style.display = (m.primary || near) ? '' : 'none';
      };
      this.map.on('zoom', this._zoomPins);
    }
    this._zoomPins();
  }

  makePin(name, em, below, onClick) {
    // In a non-interactive (hero) scene a pin is decoration: not a button,
    // not focusable, hidden from assistive tech; the host's text carries it.
    const live = this.o.interactive;
    const el = document.createElement(live ? 'button' : 'div');
    if (live) el.type = 'button';
    else { el.setAttribute('aria-hidden', 'true'); el.style.pointerEvents = 'none'; }
    el.className = `rpin${em ? ' rpin--em' : ''}${below ? ' rpin--below' : ''}`;
    el.innerHTML = '<span class="rpin__body"><i class="rpin__dot"></i><span class="rpin__name"></span><b class="rpin__v"></b><span class="rpin__tag"></span></span><span class="rpin__stem"></span>';
    el.querySelector('.rpin__name').textContent = name;
    if (live) el.addEventListener('click', (e) => { e.stopPropagation(); onClick?.(); });
    const marker = new this.maplibregl.Marker({ element: el, anchor: below ? 'top' : 'bottom' });
    return {
      el, marker, last: null,
      v: el.querySelector('.rpin__v'), dot: el.querySelector('.rpin__dot'), tag: el.querySelector('.rpin__tag'),
    };
  }

  /**
   * This-week pins: each place shows its own index, measured or modelled.
   * The four Selangor places sit within 25 km (and CAMS cannot tell them
   * apart), Singapore's five regions within 15 km. At regional zoom only
   * Kuala Lumpur and Singapore's highest-reading region are pinned; zoom in
   * past ~8.3 and every place gets its own pin. The panel lists them all.
   */
  buildPlaceMarkers() {
    const all = this.week.places || [];
    const peak = (p) => (p.official?.series || p.modelled?.series || []).reduce((a, x) => Math.max(a, x.v ?? 0), 0);
    const kl = all.find((p) => p.id === 'my-kuala-lumpur') || all.find((p) => p.country === 'MY');
    const sg = all.filter((p) => p.country === 'SG').sort((a, b) => peak(b) - peak(a))[0];
    const primary = new Set([kl, sg].filter(Boolean));
    const places = this.o.receptors === 'home' ? [...primary] : all;
    for (const p of places) {
      const below = /petaling|klang|south|east/i.test(p.id || p.name);
      const m = this.makePin(p.name, true, below && !primary.has(p), () => this.onReceptorClick?.(p));
      m.marker.setLngLat([p.lon, p.lat]).addTo(this.map);
      this.markers.push({ ...m, place: p, primary: primary.has(p) });
    }
    this.watchPinZoom();
  }

  /** Earliest scene time at which a pinned place has a reading (week mode). */
  firstReadingT() {
    if (this.mode !== 'week') return 0;
    let best = Infinity;
    for (const m of this.markers) {
      const sr = m.place?.official?.series || m.place?.modelled?.series || [];
      if (sr.length) best = Math.min(best, (Date.parse(sr[0].t) - this.week.t0) / 3.6e6);
    }
    let t = Number.isFinite(best) ? best : 0;
    // FIRMS's first local day is partial (the window starts mid-afternoon),
    // so open on the first full day rather than on a near-empty map
    const s0 = this.week.sources.find((x) => x.daily && x.day0 != null);
    if (s0 && s0.daily.length > 2) t = Math.max(t, s0.day0 + 24);
    return Math.max(0, Math.min(this.field.tEnd, t));
  }

  /** Receptor PM2.5 at time t from receptor_series (exact at frame times). */
  receptorAt(r, t) {
    const [k0, k1, a] = this.field.bracket(t);
    const s = r.pm25 || [];
    const x = s[k0] ?? 0;
    const y = s[k1] ?? x;
    return x + (y - x) * a;
  }

  updateMarkers() {
    if (this.mode === 'week') {
      const ms = this.week.t0 + this.t * 3.6e6;
      for (const m of this.markers) {
        const r = readingAt(m.place, ms);
        const key = r ? `${r.value}|${r.label}|${r.tag}` : 'none';
        if (key === m.last) continue;
        m.last = key;
        if (!r || r.noReading) {
          m.v.textContent = '—';
          m.tag.textContent = r
            ? (this.el.clientWidth < 520 ? 'no public reading' : 'no public reading · see APIMS')
            : 'no reading for this hour';
          m.dot.style.background = 'transparent';
          m.dot.style.boxShadow = 'inset 0 0 0 2px #857A70';
          if (this.o.interactive) m.el.setAttribute('aria-label', `${m.place.name}: ${r ? 'no public reading; official readings are on APIMS' : 'no reading for this hour'}`);
          continue;
        }
        m.dot.style.boxShadow = '';
        m.v.textContent = Math.round(r.value).toLocaleString();
        m.tag.textContent = `${r.label} · ${r.tag}`;
        m.dot.style.background = cssVar(r.band.token, r.band.hex);
        if (this.o.interactive) m.el.setAttribute('aria-label', `${m.place.name}: ${r.label} ${Math.round(r.value)}, ${r.tag}, ${r.band.name}`);
      }
      return;
    }
    for (const m of this.markers) {
      const v = this.receptorAt(m.r, this.t);
      const rv = Math.round(v);
      if (rv === m.last) continue;
      m.last = rv;
      m.v.textContent = rv.toLocaleString();
      const c = bandRgbOf(v);
      m.dot.style.background = `rgb(${c[0]},${c[1]},${c[2]})`;
      m.el.setAttribute('aria-label', `${m.r.name}: ${rv} micrograms per cubic metre PM2.5, modelled, ${bandOf(v).name}`);
    }
  }

  /* ------------------------------------------------------------ views */

  viewFor(name) {
    const small = this.el.clientWidth < 640;
    const dz = small ? -0.75 : 0;
    if (name === 'sarawak') {
      const src = this.data?.sources || [];
      let lon = 112.6; let lat = -1.2;
      if (src.length) {
        const w = src.reduce((a, s) => a + (s.emission_g_s || 1), 0);
        lon = src.reduce((a, s) => a + s.lon * (s.emission_g_s || 1), 0) / w;
        lat = src.reduce((a, s) => a + s.lat * (s.emission_g_s || 1), 0) / w;
      }
      const target = [111.1, 1.9];                     // between Kuching and Sibu
      const dir = Math.atan2(target[0] - lon, target[1] - lat) * 180 / Math.PI;
      return {
        center: [lon + (target[0] - lon) * 0.55, lat + (target[1] - lat) * 0.55],
        zoom: 6.15 + dz, pitch: 64, bearing: dir,
      };
    }
    const v = VIEWS[name] || VIEWS.overview;
    // narrow screens: nudge the Strait framing so KL's pin is not clipped
    if (small && (name === 'home' || name === 'overview')) return { ...v, center: [104.2, 0.75], zoom: v.zoom + dz };
    return { ...v, zoom: v.zoom + dz };
  }

  flyTo(name) {
    const v = this.viewFor(name);
    this.orbiting = false;
    if (this.o.reduced) this.map.jumpTo(v);
    else this.map.flyTo({ ...v, duration: 2600, curve: 1.3, essential: false });
  }

  /* ---------------------------------------------------------- control */

  play() {
    if (!this.field) return;
    if (this.release && this.t >= this.field.tEnd - 1e-6) this.seek(0);
    this.playing = true;
    this.schedule();
  }

  pause() {
    this.playing = false;
    this.schedule();
    this.emit(true);
  }

  seek(t) {
    if (!this.field) return;
    this.t = Math.max(0, Math.min(this.field.tEnd, t));
    this.clock = this.t;
    this.field.setTime(this.t);
    if (this.mode !== 'week') this.smoke.reset(this.clock);
    this.surface.update(!this.noField);
    this.renderStill();
    this.emit(true);
  }

  setVex(v) {
    this.vex = v;
    if (this.map.getTerrain()) this.map.setTerrain({ source: 'dem', exaggeration: v });
    this.renderStill();
  }

  setFloor(f) {
    this.surface.setFloor(f);
    this.surface.update(false);
  }

  setShow(key, on) {
    if (key === 'smoke') this.showSmokePref = on;
    this.show[key] = on;
    if (key === 'field') this.surface.setVisible(on);
    if (key === 'contours') this.surface.setContoursVisible(on);
    this.renderStill();
  }

  setParticleBudget(n) {
    const old = this.smoke.n;
    const nn = Math.max(this.B.minN, Math.min(this.B.maxN, Math.round(n)));
    this.smoke.n = nn;
    if (nn > old) this.smoke.reset(this.clock, old, nn);
  }

  /* ------------------------------------------------------------- loop */

  canRun() {
    return !this.destroyed && !document.hidden && this.inView && !this.o.reduced && !!this.field;
  }

  schedule() {
    if (this.canRun()) {
      if (!this.raf) {
        this.last = performance.now();
        this.raf = requestAnimationFrame((ts) => this.tick(ts));
      }
    } else if (this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
  }

  tick(ts) {
    this.raf = 0;
    if (!this.canRun()) return;
    const dtMs = Math.min(100, Math.max(0, ts - this.last));
    this.last = ts;
    this.rt += dtMs / 1000;
    this.measure(dtMs);

    const f = this.field;
    let dModel = 0;
    if (this.playing) {
      dModel = (dtMs / 1000) * this.speed;
      let t = this.t + dModel;
      if (t >= f.tEnd) {
        if (this.release) { t = f.tEnd; this.playing = false; this.emit(true); } else {
          t = f.tEnd > 0 ? t % f.tEnd : 0;
        }
      }
      this.t = t;
      this.clock += dModel;
      f.setTime(this.t);
      if (this.mode !== 'week') this.smoke.step(this.clock, dModel);
      const now = performance.now();
      if (now - (this.lastSurface || 0) > 1000 / this.B.surfaceHz) {
        const contours = now - (this.lastContour || 0) > 280;
        this.surface.update(contours);
        if (contours) this.lastContour = now;
        this.lastSurface = now;
      }
    }
    if (this.o.orbit && this.orbiting !== false && !this.map.isMoving()) {
      if (this.o.orbit === 'sway') {
        // drift a few degrees either side of the framing, never losing it
        if (this.swayBase == null) { this.swayBase = this.map.getBearing(); this.swayT = 0; }
        this.swayT += dtMs / 1000;
        this.map.setBearing(this.swayBase + (this.o.swayDeg ?? 9) * Math.sin(this.swayT * (this.o.swayRate ?? 0.11)));
      } else {
        this.map.setBearing(this.map.getBearing() + dtMs * (this.o.orbitRate ?? 0.0014));
      }
    }
    this.draw(false);
    this.emit(false);
    this.raf = requestAnimationFrame((tt) => this.tick(tt));
  }

  measure(dtMs) {
    const p = this.perf;
    if (dtMs <= 0) return;
    p.frames += 1;
    p.ema += (dtMs - p.ema) * 0.08;
    p.fps = 1000 / p.ema;
    if (p.frames < 45 || !this.playing) return;
    const now = performance.now();
    if (now - p.lastAdapt < 700) return;
    p.lastAdapt = now;
    if (p.ema > 22 && this.smoke.n > this.B.minN) {
      this.setParticleBudget(this.smoke.n * 0.8);
      p.lastShrink = now;
    } else if (p.ema < 18.2 && this.smoke.n < this.B.maxN && now - p.lastShrink > 6000) {
      this.setParticleBudget(this.smoke.n * 1.12);
    }
  }

  draw(still) {
    const f = this.field;
    if (!f || !this.overlay) return;
    const reducedStill = still || this.o.reduced;
    if (this.mode !== 'week') this.smoke.fill(this.clock, this.vex);
    const wind = f.wind;
    this.fires.update(reducedStill && this.o.reduced ? 3.7 : this.rt, this.vex,
      (lon, lat, z, out) => wind.sample(lon, lat, z, out), this.o.reduced, this.nightAt(this.t), this.t);
    this.overlay.setProps({
      layers: buildLayers(this.deck, {
        smoke: this.smoke,
        fires: this.fires,
        sumatra: this.sumatra.map((s) => ({ pos: [s.lon, s.lat, ((this.elev(s.lon, s.lat) || 0) + 60) * this.vex] })),
        showSmoke: this.show.smoke && this.mode !== 'week',
        showFires: this.show.fires,
        puffMinPx: this.o.budget === 'low' ? 4 : 3,
      }),
    });
    const now = performance.now();
    if (now - (this.lastSky || 0) > 250 || still) {
      this.lastSky = now;
      this.updateSky();
      this.updateMarkers();
    }
  }

  /** 1 at ~01:00 local, 0 at ~13:00: fire glow reads stronger at night. */
  nightAt(t) {
    const hod = ((this.data.frames[0].hour_of_day ?? 0) + t) % 24;   // local (UTC+8) in week mode
    return 0.5 + 0.5 * Math.cos((2 * Math.PI * (hod - 1)) / 24);
  }

  renderStill() {
    if (!this.field || !this.overlay) return;
    this.field.setTime(this.t);
    this.draw(true);
  }

  updateSky() {
    if (this.noField) {
      if (this.haze !== 0.3) { this.haze = 0.3; this.map.setSky(skySpec(0.3)); }
      return;
    }
    const c = this.map.getCenter();
    const bg = Math.max(12, this.field.bg || 0);
    const v = Math.max(this.field.sample(c.lng, c.lat), bg);
    const h = Math.min(1, Math.max(0, Math.log(v / bg) / Math.log(700 / bg)));
    this.haze = this.haze == null ? h : this.haze + (h - this.haze) * 0.35;
    this.map.setSky(skySpec(this.haze));
  }

  emit(force) {
    if (!this.onFrame) return;
    const now = performance.now();
    if (!force && now - (this.lastEmit || 0) < 110) return;
    this.lastEmit = now;
    this.onFrame(this.state());
  }

  state() {
    const f = this.field;
    return {
      t: this.t,
      tEnd: f ? f.tEnd : 0,
      playing: this.playing,
      bracket: f ? f.bracket(this.t) : [0, 0, 0],
      particles: this.smoke ? this.smoke.n : 0,
      visible: this.smoke ? this.smoke.visible : 0,
      fps: this.perf.fps,
      haze: this.haze || 0,
      terrain: { ...terrainStats },
    };
  }

  destroy() {
    this.destroyed = true;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    document.removeEventListener('visibilitychange', this._vis);
    this.io?.disconnect();
    for (const m of this.markers) m.marker.remove();
    try { if (this.overlay) this.map.removeControl(this.overlay); } catch { /* already gone */ }
    try { this.overlay?.finalize(); } catch { /* already gone */ }
    try { this.map?.remove(); } catch { /* already gone */ }
  }
}
