/**
 * libs.js — load MapLibre GL and deck.gl on demand, once per page.
 *
 * Both libraries ship as UMD bundles that publish globals (`maplibregl`,
 * `deck`). They are injected as async scripts only when a scene is mounted, so
 * nothing here blocks a host page's first paint. The load promise is kept on
 * `window` under one namespaced key, so any number of scenes — or two copies
 * of this module served under different hashed URLs — share one load and
 * never inject a second copy that would clobber the first's globals.
 *
 * Every failure names what failed, so the error state can say so.
 */

export const VERSIONS = {
  maplibre: '5.24.0',
  deck: '9.3.11',
};

export const URLS = {
  maplibreJs: `https://unpkg.com/maplibre-gl@${VERSIONS.maplibre}/dist/maplibre-gl.js`,
  maplibreCss: `https://unpkg.com/maplibre-gl@${VERSIONS.maplibre}/dist/maplibre-gl.css`,
  deckJs: `https://unpkg.com/deck.gl@${VERSIONS.deck}/dist.min.js`,
};

const KEY = '__hazeSim3dLibs';

export class LibError extends Error {
  constructor(what, detail) {
    super(`${what} ${detail}`);
    this.what = what;
    this.kind = 'lib';
  }
}

function injectScript(src, label, timeoutMs) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-haze-lib="${label}"]`);
    if (existing && existing.dataset.loaded === '1') { resolve(); return; }
    const s = existing || document.createElement('script');
    const timer = setTimeout(() => reject(new LibError(label, `did not load from ${new URL(src).host} within ${Math.round(timeoutMs / 1000)} s.`)), timeoutMs);
    s.addEventListener('load', () => { clearTimeout(timer); s.dataset.loaded = '1'; resolve(); }, { once: true });
    s.addEventListener('error', () => { clearTimeout(timer); reject(new LibError(label, `could not be fetched from ${new URL(src).host}.`)); }, { once: true });
    if (!existing) {
      s.src = src;
      s.async = true;
      s.crossOrigin = 'anonymous';
      s.dataset.hazeLib = label;
      document.head.appendChild(s);
    }
  });
}

function injectCss(href) {
  if (document.querySelector(`link[data-haze-lib-css]`)) return;
  const l = document.createElement('link');
  l.rel = 'stylesheet';
  l.href = href;
  l.dataset.hazeLibCss = '1';
  document.head.appendChild(l);
}

/** WebGL2 is a hard requirement of MapLibre 5. */
export function webglProblem() {
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2');
    if (!gl) return 'This browser could not create a WebGL2 context, which the 3D map needs. Hardware acceleration may be switched off.';
    const lose = gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    return null;
  } catch {
    return 'This browser could not create a WebGL2 context, which the 3D map needs.';
  }
}

/**
 * Resolves to { maplibregl, deck }. Rejects with a LibError naming the
 * library that failed. Shared across every caller on the page.
 */
export function loadLibs(timeoutMs = 20000) {
  if (window[KEY]) return window[KEY];
  const p = (async () => {
    const gl = webglProblem();
    if (gl) throw Object.assign(new Error(gl), { what: 'WebGL', kind: 'webgl' });
    injectCss(URLS.maplibreCss);
    const jobs = [];
    if (!window.maplibregl) jobs.push(injectScript(URLS.maplibreJs, 'MapLibre GL', timeoutMs));
    if (!window.deck || !window.deck.MapboxOverlay) jobs.push(injectScript(URLS.deckJs, 'deck.gl', timeoutMs));
    await Promise.all(jobs);
    if (!window.maplibregl) throw new LibError('MapLibre GL', 'loaded but did not define its global.');
    if (!window.deck || !window.deck.MapboxOverlay) throw new LibError('deck.gl', 'loaded without its MapLibre overlay.');
    return { maplibregl: window.maplibregl, deck: window.deck };
  })();
  window[KEY] = p;
  // A failed load must not poison later retries.
  p.catch(() => { if (window[KEY] === p) delete window[KEY]; });
  return p;
}
