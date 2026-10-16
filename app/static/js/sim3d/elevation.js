/**
 * elevation.js — AWS Terrain Tiles (terrarium encoding), sea clamped to zero.
 *
 * The terrarium tiles carry bathymetry. Under a vertical exaggeration the
 * South China Sea becomes a 30 km trench and the Java Sea a moat, which says
 * nothing about haze and a lot that is false about the land. So tiles are
 * re-encoded with negative elevations clamped to sea level before MapLibre
 * sees them (via a custom protocol), and the same decode feeds a coarse
 * lookup the particle system uses to sit smoke on the ground.
 *
 *   elevation_m = R*256 + G + B/256 - 32768
 */

export const TERRARIUM = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
export const PROTOCOL = 'hazedem';

async function fetchBitmap(url, signal) {
  const res = await fetch(url, { signal, mode: 'cors' });
  if (!res.ok) throw new Error(`terrain tile ${res.status}`);
  const blob = await res.blob();
  return createImageBitmap(blob);
}

function canvasOf(w, h) {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(w, h);
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

/** Clamp negative terrarium elevations to 0 in place. */
function clampSea(data) {
  for (let i = 0; i < data.length; i += 4) {
    if (data[i] < 128) { data[i] = 128; data[i + 1] = 0; data[i + 2] = 0; }
  }
}

async function toPngBuffer(canvas) {
  if (canvas.convertToBlob) {
    const b = await canvas.convertToBlob({ type: 'image/png' });
    return b.arrayBuffer();
  }
  const b = await new Promise((r) => canvas.toBlob(r, 'image/png'));
  return b.arrayBuffer();
}

/** Register the clamped-terrain protocol with MapLibre. Idempotent. */
let registered = false;
export const terrainStats = { ok: 0, failed: 0 };
export function registerProtocol(maplibregl) {
  if (registered) return;
  registered = true;
  maplibregl.addProtocol(PROTOCOL, async (params, abort) => {
    const url = params.url.replace(`${PROTOCOL}://`, 'https://');
    try {
      const bmp = await fetchBitmap(url, abort?.signal);
      const c = canvasOf(bmp.width, bmp.height);
      const ctx = c.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(bmp, 0, 0);
      const img = ctx.getImageData(0, 0, bmp.width, bmp.height);
      clampSea(img.data);
      ctx.putImageData(img, 0, 0);
      const buf = await toPngBuffer(c);
      terrainStats.ok += 1;
      return { data: buf };
    } catch (err) {
      if (err?.name !== 'AbortError') terrainStats.failed += 1;
      throw err;
    }
  });
}

export const demUrl = () => TERRARIUM.replace('https://', `${PROTOCOL}://`);

/* ------------------------------------------------------- lookup grid */

const lon2x = (lon, z) => ((lon + 180) / 360) * 2 ** z;
const lat2y = (lat, z) => {
  const r = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z;
};

/**
 * Elevation lookup over a bbox, from zoom-5 tiles (~5 km/px at the equator,
 * plenty for sitting smoke on the ground). Resolves to a sampler; on any
 * failure resolves to a flat-zero sampler and reports why.
 */
export async function buildElevation(bbox, z = 5) {
  const x0 = Math.floor(lon2x(bbox[0], z));
  const x1 = Math.floor(lon2x(bbox[2], z));
  const y0 = Math.floor(lat2y(bbox[3], z));
  const y1 = Math.floor(lat2y(bbox[1], z));
  const nx = x1 - x0 + 1;
  const ny = y1 - y0 + 1;
  const W = nx * 256;
  const H = ny * 256;
  const elev = new Float32Array(W * H);
  const jobs = [];
  let failed = 0;
  for (let ty = y0; ty <= y1; ty += 1) {
    for (let tx = x0; tx <= x1; tx += 1) {
      const url = TERRARIUM.replace('{z}', z).replace('{x}', tx).replace('{y}', ty);
      jobs.push(fetchBitmap(url).then((bmp) => {
        const c = canvasOf(256, 256);
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(bmp, 0, 0);
        const d = ctx.getImageData(0, 0, 256, 256).data;
        const ox = (tx - x0) * 256;
        const oy = (ty - y0) * 256;
        for (let py = 0; py < 256; py += 1) {
          for (let px = 0; px < 256; px += 1) {
            const i = (py * 256 + px) * 4;
            const e = d[i] * 256 + d[i + 1] + d[i + 2] / 256 - 32768;
            elev[(oy + py) * W + ox + px] = e > 0 ? e : 0;
          }
        }
      }).catch(() => { failed += 1; }));
    }
  }
  await Promise.all(jobs);
  const scale = 2 ** z;
  const sampler = (lon, lat) => {
    const px = (lon2x(lon, z) - x0) * 256;
    const py = (lat2y(lat, z) - y0) * 256;
    if (px < 0 || py < 0 || px >= W - 1 || py >= H - 1) return 0;
    const ix = px | 0;
    const iy = py | 0;
    const tx = px - ix;
    const ty = py - iy;
    const i = iy * W + ix;
    const a = elev[i] + (elev[i + 1] - elev[i]) * tx;
    const b = elev[i + W] + (elev[i + W + 1] - elev[i + W]) * tx;
    return a + (b - a) * ty;
  };
  sampler.failed = failed;
  sampler.total = jobs.length;
  sampler.scale = scale;
  return sampler;
}

export const flatElevation = Object.assign(() => 0, { failed: 0, total: 0 });
