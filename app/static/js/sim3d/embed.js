/**
 * embed.js — the burning map as a full-bleed hero background.
 *
 *   import { mountBurningMap } from '/static/js/sim3d/embed.js';
 *   const handle = await mountBurningMap(el, {
 *     mode: 'week',                    // default: replay the past 7 days
 *     autoplay: true, orbit: true, interactive: false, budget: 'low',
 *   });
 *   // mode: 'model' runs the dispersion model instead; year/date pick the
 *   // fire set (omit for the latest season on record).
 *
 * "This week" (default): real NASA FIRMS fires lit day by day and the CAMS
 * PM2.5 field (modelled), framed across the Strait of Malacca with Kuala
 * Lumpur and Singapore in the foreground, each pinned with its own index
 * label (PSI / API measured, or US AQI modelled). No smoke particles in this
 * mode: no wind field comes with the data, and the contract forbids
 * inventing one. If this week's data is unavailable the embed falls back to
 * a model run (fallback: true, the default) and says so in meta and caption.
 * "This week" degrades rather than disappears: with the fire detections but
 * no haze field (the launch configuration: no CAMS licence) it still runs —
 * real fires lit day by day, Singapore's official PSI, KL marked "no public
 * reading", no haze drawn, and the caption says the haze field is
 * unavailable. Only if the fire detections are missing too does it fall
 * back to the model run.
 *   handle.destroy();
 *
 * Same engine and the same visual truth rules as the simulator page: the
 * smoke is the model's run, advected by its wind, shaded by its values. No
 * panels; a one-line caption (on by default) says which run is on screen.
 *
 * Loading: importing this module costs only this file and the scene modules
 * (~88 KB, ~29 KB gzipped). MapLibre and deck.gl (~2.7 MB, ~750 KB
 * compressed) are injected as async scripts when mountBurningMap is called,
 * so nothing blocks the host's first paint. The promise resolves once the first frame with smoke is on screen
 * and REJECTS — with err.reason in {'webgl','saveData','lib','api','map',
 * 'aborted'} — so the host can keep its static fallback.
 *
 * Options (all optional):
 *   mode       'week' (default) | 'model'
 *   fallback   true: if this week's data is unavailable, run the model and
 *              say so (meta.mode === 'model', meta.fellBackFrom); false: reject
 *   autoplay   true    orbit  true (a slow sway that keeps the framing)
 *   interactive false: no gestures; pins are decoration (aria-hidden)
 *   budget     'low' | 'med' particle/texture budget
 *   pins       true: KL and Singapore readings with their index labels
 *   caption    true: one line naming the data on screen and its sources
 *   padding    {top, right, bottom, left} in px: MapLibre camera padding, so
 *              the area of interest sits clear of the host's own panels
 *              (e.g. {left: 520} puts the Strait to the right of a left card)
 *   year, date (model mode) the fire set; default: latest season on record
 *   signal     AbortSignal; aborting unmounts
 *
 * Pauses itself offscreen (IntersectionObserver) and on tab hide. Under
 * prefers-reduced-motion it renders one still frame and never animates.
 */

import { Scene, VIEWS } from './scene.js';
import { loadWeek } from './now.js';

const API = '/api/simulate/haze3d';

function fail(reason, message) {
  return Object.assign(new Error(message), { reason });
}

async function latestSeason() {
  const res = await fetch('/api/history');
  if (!res.ok) throw fail('api', `/api/history returned ${res.status}`);
  const { seasons } = await res.json();
  const ok = (seasons || []).filter((s) => (s.burned_area_mha_kalimantan || 0) > 0 && s.peak_date);
  if (!ok.length) throw fail('api', 'no fire season with a peak date on record');
  const s = ok[ok.length - 1];
  return { year: s.year, date: s.peak_date };
}

const CAPTION_CSS = `
.hz-embed{position:absolute;inset:0;overflow:hidden}
.hz-embed__map,.hz-embed__map.maplibregl-map{position:absolute;inset:0}
.hz-embed .maplibregl-ctrl-attrib{font:11px/1.4 system-ui,sans-serif;background:rgba(247,244,238,.85)}
.hz-embed .maplibregl-ctrl-attrib.maplibregl-compact-show:not(:hover):not(:focus-within) .maplibregl-ctrl-attrib-inner{display:none}
.hz-embed .rpin{display:flex;flex-direction:column;align-items:center;background:none;border:0;padding:0;font:inherit;cursor:default;pointer-events:none}
.hz-embed .rpin--below{flex-direction:column-reverse}
.hz-embed .rpin__body{display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid #3D3630;border-radius:999px;padding:4px 10px 4px 6px;white-space:nowrap;box-shadow:1px 2px 8px rgba(60,45,30,.18)}
.hz-embed .rpin__dot{width:12px;height:12px;border-radius:50%;box-shadow:inset 0 0 0 1px rgba(0,0,0,.15)}
.hz-embed .rpin__name{font:600 12px/1.2 "Public Sans",system-ui,sans-serif;color:#1C1815}
.hz-embed .rpin__v{font:500 14px/1.2 "IBM Plex Mono",ui-monospace,monospace;color:#1C1815}
.hz-embed .rpin__tag{font:400 11px/1.2 "Public Sans",system-ui,sans-serif;color:#62584F}
.hz-embed .rpin__stem{width:1px;height:12px;background:#3D3630}
.hz-embed__cap{box-sizing:border-box;position:absolute;left:12px;bottom:12px;z-index:2;max-width:calc(100% - 64px);
  font:500 11.5px/1.4 "IBM Plex Mono",ui-monospace,monospace;color:#3D3630;
  background:rgba(255,255,255,.88);border:1px solid #DCD4C7;border-radius:6px;padding:4px 8px;pointer-events:none}
@media (max-width:640px){.hz-embed__cap{font-size:11px;bottom:8px;left:8px;max-width:calc(100% - 56px)}}
`;

function injectCss() {
  if (document.querySelector('style[data-hz-embed]')) return;
  const s = document.createElement('style');
  s.dataset.hzEmbed = '1';
  s.textContent = CAPTION_CSS;
  document.head.appendChild(s);
}

/**
 * @param {HTMLElement} container  positioned element the scene fills
 * @param {object} o
 * @returns {Promise<{destroy(): void, meta: object, metrics: object, scene: Scene}>}
 */
export async function mountBurningMap(container, o = {}) {
  const opt = {
    mode: 'week',
    fallback: true,
    fixture: false,           // true | 'nofield': synthetic week data, integration tests only
    pins: true,               // KL and Singapore readings in "this week" mode
    year: undefined,
    date: undefined,
    autoplay: true,
    orbit: true,
    interactive: false,
    budget: 'low',
    caption: true,
    respectSaveData: true,
    windFromDeg: 150,         // south-easterly: the season's monsoon, toward Sarawak
    windSpeedMs: 4.5,
    orbitDegPerSec: 1.4,
    signal: undefined,
    padding: undefined,       // {top, right, bottom, left} px kept clear of the focus
    ...o,
  };
  const t0 = performance.now();
  if (!container) throw fail('map', 'mountBurningMap needs a container element');
  if (!container.clientWidth || !container.clientHeight) {
    throw fail('map', 'the container has no size; give it a height before mounting');
  }
  if (opt.respectSaveData && navigator.connection && navigator.connection.saveData) {
    throw fail('saveData', 'Data saver is on; the 3D scene is skipped.');
  }
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  injectCss();
  const root = document.createElement('div');
  root.className = 'hz-embed';
  root.setAttribute('aria-hidden', 'true');
  const mapEl = document.createElement('div');
  mapEl.className = 'hz-embed__map';
  root.appendChild(mapEl);
  container.appendChild(root);

  let scene = null;
  let destroyed = false;
  const destroy = () => {
    if (destroyed) return;
    destroyed = true;
    try { scene?.destroy(); } catch { /* already torn down */ }
    root.remove();
  };
  opt.signal?.addEventListener('abort', destroy, { once: true });
  const check = () => { if (destroyed) throw fail('aborted', 'unmounted before the first frame'); };

  try {
    // data and libraries in parallel
    const weekP = opt.mode === 'week'
      ? loadWeek({ fixture: opt.fixture }).catch((e) => ({ available: false, reason: String(e.message || e) }))
      : Promise.resolve(null);
    const modelP = async () => {
      const { year, date } = (opt.year && opt.date)
        ? { year: opt.year, date: opt.date }
        : await latestSeason().then((s) => ({ year: opt.year || s.year, date: opt.date || s.date }));
      const body = {
        year, start_date: date, hours: 24, step_hours: 1,
        grid_n: opt.budget === 'low' ? 40 : 48,
        wind_speed_ms: opt.windSpeedMs, wind_dir_deg: opt.windFromDeg,
        mixing_height_m: 800, diurnal: true, release_from_start: false,
      };
      let res;
      try {
        res = await fetch(API, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      } catch { throw fail('api', 'the model API could not be reached'); }
      if (!res.ok) throw fail('api', `the model API returned ${res.status}`);
      const j = await res.json();
      if (!j.grid || !j.frames?.length) throw fail('api', j.note || 'the model returned no field');
      return { j, year, date };
    };
    // start the model fetch early only when it is the plan
    const modelEarly = opt.mode === 'model' ? modelP() : null;
    modelEarly?.catch(() => {});

    scene = new Scene(mapEl, {
      interactive: opt.interactive,
      budget: opt.budget,
      receptors: opt.pins ? 'home' : false,
      labels: true,
      orbit: opt.orbit && !reduced ? 'sway' : false,
      reduced,
      vex: 10,
      speed: 1.5,
      view: VIEWS.home,
      fieldFeather: 3,       // no legend here to explain a straight grid edge
      fieldOpacity: 0.62,
      padding: opt.padding,
    });
    try {
      await scene.init();
    } catch (err) {
      throw fail(err.kind === 'webgl' ? 'webgl' : (err.kind === 'lib' ? 'lib' : 'map'), String(err.message || err));
    }
    check();

    const week = await weekP;
    check();
    let meta;
    const cap = document.createElement('p');
    cap.className = 'hz-embed__cap';
    const fmtD = (d) => new Date(d).toLocaleString('en-GB', { timeZone: 'Asia/Kuala_Lumpur', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
    if (week && week.available) {
      scene.setWeek(week);
      scene.speed = 8;               // the week in ~21 s
      // open on the first hour the pinned cities have a reading, not on a
      // blank "no reading" pin
      const t1 = scene.firstReadingT();
      if (t1 > 0) scene.seek(t1);
      meta = {
        mode: 'week', fixture: !!week.fixture, headline: week.headline,
        hasField: week.hasField, fieldReason: week.fieldReason || null,
        fires: week.sources.length, countByRegion: week.hotspots?.count_by_region || null,
        window: { start: new Date(week.t0).toISOString(), hours: week.tEnd },
        attribution: week.attribution, reducedMotion: reduced,
      };
      const sg = week.places.some((p) => p.country === 'SG' && p.official) ? ' · Singapore: NEA PSI (measured)' : '';
      scene.onFrame = (st) => {
        const when = `${fmtD(week.t0 + st.t * 3.6e6)} MYT`;
        cap.textContent = week.hasField
          ? `${week.fixture ? 'FIXTURE DATA, NOT REAL · ' : ''}This week · ${when} · haze: CAMS (modelled) · fires: NASA FIRMS (measured)${sg}`
          : `${week.fixture ? 'FIXTURE DATA, NOT REAL · ' : ''}This week · ${when} · fires: NASA FIRMS satellite detections (measured)${sg} · haze field unavailable, so no haze is drawn`;
      };
    } else {
      if (opt.mode === 'week' && !opt.fallback) throw fail('api', week?.reason || "this week's data is unavailable");
      const { j, year, date } = await (modelEarly || modelP());
      check();
      // No city numbers in the fallback: a Kalimantan-only model run reads
      // ~12 at KL and Singapore, which a reader would take as "the air is fine".
      scene.o.receptors = false;
      const data = scene.setData(j);
      const d = new Date(`${date}T00:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
      cap.textContent = `Model run, not this week's data · ${data.sources_used} Kalimantan fires burning ${d} · wind from ${opt.windFromDeg}°`;
      meta = {
        mode: 'model', fellBackFrom: opt.mode === 'week' ? (week?.reason || 'unavailable') : null,
        year, date, sources: data.sources_used, windFromDeg: opt.windFromDeg,
        windSpeedMs: opt.windSpeedMs, reducedMotion: reduced,
        caveat: 'Kalimantan fires only; Sumatran smoke is not modelled.',
      };
    }
    scene.setShow('contours', false);
    if (opt.caption) root.appendChild(cap);
    scene.onFrame?.(scene.state());

    // resolve when the first frame has been drawn (or 6 s)
    await new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      scene.map.once('idle', finish);
      setTimeout(finish, 6000);
    });
    check();
    // keep the attribution to its (i) button so it never sits on the caption;
    // the caption itself names every data source
    root.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
    if (opt.autoplay && !reduced) scene.play();
    const metrics = { msToFirstFrame: Math.round(performance.now() - t0) };
    return { destroy, scene, metrics, meta };
  } catch (err) {
    destroy();
    throw err.reason ? err : fail('map', String(err.message || err));
  }
}

export default mountBurningMap;
