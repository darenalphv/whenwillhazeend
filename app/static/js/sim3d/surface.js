/**
 * surface.js — the model's surface PM2.5 as a smooth field draped on terrain.
 *
 * The field is the grid's values, bilinearly interpolated (in space, and in
 * time between hourly frames by field.js), coloured by AQI band through a LUT
 * and painted into a canvas that MapLibre drapes over the 3D terrain. Rows
 * are resampled in Web Mercator so the image lines up with the basemap.
 *
 * Contours at the model's own bands (35/55/150/250) are traced by marching
 * squares on the same bilinear field, chained into polylines, and drawn as
 * labelled vector lines. Both stop dead at the grid edge, because that is
 * where the model stops — the legend says so.
 */

import { buildFieldLut, lutIndex, CONTOURS } from './colors.js';

const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const invMercY = (y) => (360 / Math.PI) * Math.atan(Math.exp(y)) - 90;

export class Surface {
  constructor(map, { contours = true, beforeId, maxPx = 384, feather = 0, opacity = 0.88 } = {}) {
    this.feather = feather;     // cells of edge fade; 0 = honest hard edge
    this.opacity = opacity;
    this.map = map;
    this.withContours = contours;
    this.beforeId = beforeId;
    this.maxPx = maxPx;
    this.canvas = document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.floor = 20;
    this.lut = buildFieldLut(this.floor);
    this.visible = true;
    this.contoursVisible = contours;
    this.added = false;
  }

  setFloor(f) {
    this.floor = f;
    this.lut = buildFieldLut(f);
  }

  setField(field) {
    this.field = field;
    const g = field.grid;
    const W = Math.min(this.maxPx, Math.max(96, g.nx * 6));
    const span = (mercY(g.lat_max) - mercY(g.lat_min)) / (((g.lon_max - g.lon_min) * Math.PI) / 180);
    const H = Math.max(64, Math.round(W * span));
    this.canvas.width = W;
    this.canvas.height = H;
    this.img = this.ctx.createImageData(W, H);
    // precompute fractional grid coordinates per column and per row
    this.fx = new Float32Array(W);
    for (let x = 0; x < W; x += 1) this.fx[x] = ((x + 0.5) / W) * (g.nx - 1);
    this.fy = new Float32Array(H);
    const y0 = mercY(g.lat_max);
    const y1 = mercY(g.lat_min);
    for (let y = 0; y < H; y += 1) {
      const lat = invMercY(y0 + ((y + 0.5) / H) * (y1 - y0));
      this.fy[y] = (lat - g.lat_min) / ((g.lat_max - g.lat_min) / (g.ny - 1));
    }
    const coords = [
      [g.lon_min, g.lat_max], [g.lon_max, g.lat_max],
      [g.lon_max, g.lat_min], [g.lon_min, g.lat_min],
    ];
    const map = this.map;
    this.draw();
    if (!this.added) {
      map.addSource('haze-field', {
        type: 'canvas', canvas: this.canvas, coordinates: coords, animate: false,
      });
      map.addLayer({
        id: 'haze-field', type: 'raster', source: 'haze-field',
        paint: { 'raster-opacity': this.opacity, 'raster-resampling': 'linear', 'raster-fade-duration': 0 },
      }, this.beforeId && map.getLayer(this.beforeId) ? this.beforeId : undefined);
      if (this.withContours) {
        map.addSource('haze-contours', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addLayer({
          id: 'haze-contour-line', type: 'line', source: 'haze-contours',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: {
            'line-color': ['match', ['get', 'level'], 35, '#7A6347', 55, '#8F2E08', 150, '#6A1B2A', '#3a0f17'],
            'line-width': ['match', ['get', 'level'], 35, 1.0, 55, 1.3, 150, 1.7, 2.1],
            'line-opacity': 0.8,
            'line-dasharray': ['match', ['get', 'level'], 35, ['literal', [3, 2]], ['literal', [1, 0]]],
          },
        });
        map.addLayer({
          id: 'haze-contour-label', type: 'symbol', source: 'haze-contours',
          filter: ['>=', ['get', 'n'], 8],
          layout: {
            'symbol-placement': 'line', 'symbol-spacing': 320,
            'text-field': ['get', 'label'], 'text-font': ['Noto Sans Bold'],
            'text-size': 11, 'text-keep-upright': true, 'text-max-angle': 35,
          },
          paint: {
            'text-color': ['match', ['get', 'level'], 35, '#5E4B33', 55, '#8F2E08', '#5A1724'],
            'text-halo-color': 'rgba(247,244,238,0.95)', 'text-halo-width': 1.6,
          },
        });
      }
      this.added = true;
    } else {
      map.getSource('haze-field').setCoordinates(coords);
    }
    this.push();
  }

  /** Paint the current blended field (field.cur) into the canvas. */
  draw() {
    const f = this.field;
    if (!f) return;
    const { img, fx, fy, lut } = this;
    const W = this.canvas.width;
    const H = this.canvas.height;
    const nx = f.nx;
    const ny = f.ny;
    const cur = f.cur;
    const d = img.data;
    let o = 0;
    for (let y = 0; y < H; y += 1) {
      const gy = fy[y];
      if (gy < 0 || gy > ny - 1) {
        for (let x = 0; x < W; x += 1) { d[o + 3] = 0; o += 4; }
        continue;
      }
      let iy = gy | 0;
      if (iy >= ny - 1) iy = ny - 2;
      const ty = gy - iy;
      const r0 = iy * nx;
      const r1 = r0 + nx;
      for (let x = 0; x < W; x += 1) {
        const gx = fx[x];
        let ix = gx | 0;
        if (ix >= nx - 1) ix = nx - 2;
        const tx = gx - ix;
        const a = cur[r0 + ix] + (cur[r0 + ix + 1] - cur[r0 + ix]) * tx;
        const b = cur[r1 + ix] + (cur[r1 + ix + 1] - cur[r1 + ix]) * tx;
        const li = lutIndex(a + (b - a) * ty) * 4;
        d[o] = lut[li]; d[o + 1] = lut[li + 1]; d[o + 2] = lut[li + 2]; d[o + 3] = lut[li + 3];
        if (this.feather) {
          const e = Math.min(gx, nx - 1 - gx, gy, ny - 1 - gy) / this.feather;
          if (e < 1) d[o + 3] *= e < 0 ? 0 : e * e;
        }
        o += 4;
      }
    }
    this.ctx.putImageData(img, 0, 0);
  }

  /** Ask MapLibre to re-upload the canvas once (it is not animated). */
  push() {
    const src = this.map.getSource('haze-field');
    if (!src) return;
    src.play();
    this.map.once('render', () => src.pause());
    this.map.triggerRepaint();
  }

  update(withContours) {
    this.draw();
    this.push();
    if (withContours && this.withContours && this.contoursVisible) this.updateContours();
  }

  updateContours() {
    const src = this.map.getSource('haze-contours');
    if (!src || !this.field) return;
    src.setData(traceContours(this.field));
  }

  setVisible(v) {
    this.visible = v;
    if (this.map.getLayer('haze-field')) this.map.setLayoutProperty('haze-field', 'visibility', v ? 'visible' : 'none');
  }

  setContoursVisible(v) {
    this.contoursVisible = v;
    for (const id of ['haze-contour-line', 'haze-contour-label']) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', v ? 'visible' : 'none');
    }
    if (v) this.updateContours();
  }
}

/* --------------------------------------------------- marching squares */

export function traceContours(field) {
  const { nx, ny, cur, lon0, lat0, dlon, dlat } = field;
  const features = [];
  for (const L of CONTOURS) {
    const segs = [];          // [keyA, keyB, ptA, ptB]
    const pt = (key, i0, i1) => {
      // interpolate along the edge between node i0 and node i1
      const a = cur[i0];
      const b = cur[i1];
      const t = b === a ? 0.5 : (L - a) / (b - a);
      const x0 = i0 % nx; const y0 = (i0 / nx) | 0;
      const x1 = i1 % nx; const y1 = (i1 / nx) | 0;
      return [lon0 + (x0 + (x1 - x0) * t) * dlon, lat0 + (y0 + (y1 - y0) * t) * dlat];
    };
    for (let j = 0; j < ny - 1; j += 1) {
      for (let i = 0; i < nx - 1; i += 1) {
        const n00 = j * nx + i;
        const n10 = n00 + 1;
        const n01 = n00 + nx;
        const n11 = n01 + 1;
        const c = (cur[n00] >= L ? 1 : 0) | (cur[n10] >= L ? 2 : 0)
          | (cur[n11] >= L ? 4 : 0) | (cur[n01] >= L ? 8 : 0);
        if (c === 0 || c === 15) continue;
        const E = [
          () => ['h' + n00, pt('h' + n00, n00, n10)],     // e0 bottom
          () => ['v' + n10, pt('v' + n10, n10, n11)],     // e1 right
          () => ['h' + n01, pt('h' + n01, n01, n11)],     // e2 top
          () => ['v' + n00, pt('v' + n00, n00, n01)],     // e3 left
        ];
        const add = (p, q) => { const A = E[p](); const B = E[q](); segs.push([A[0], B[0], A[1], B[1]]); };
        const centre = (cur[n00] + cur[n10] + cur[n11] + cur[n01]) / 4 >= L;
        switch (c) {
          case 1: case 14: add(0, 3); break;
          case 2: case 13: add(0, 1); break;
          case 4: case 11: add(1, 2); break;
          case 8: case 7: add(2, 3); break;
          case 3: case 12: add(3, 1); break;
          case 6: case 9: add(0, 2); break;
          case 5: if (centre) { add(0, 1); add(2, 3); } else { add(0, 3); add(1, 2); } break;
          case 10: if (centre) { add(0, 3); add(1, 2); } else { add(0, 1); add(2, 3); } break;
          default: break;
        }
      }
    }
    // chain segments that share an edge key
    const byKey = new Map();
    segs.forEach((s, idx) => {
      for (const k of [s[0], s[1]]) {
        if (!byKey.has(k)) byKey.set(k, []);
        byKey.get(k).push(idx);
      }
    });
    const used = new Uint8Array(segs.length);
    const walk = (startKey, line, pushFn) => {
      let key = startKey;
      for (;;) {
        const cand = (byKey.get(key) || []).find((s) => !used[s]);
        if (cand === undefined) break;
        used[cand] = 1;
        const s = segs[cand];
        const nextKey = s[0] === key ? s[1] : s[0];
        pushFn(s[0] === key ? s[3] : s[2]);
        key = nextKey;
      }
    };
    for (let s = 0; s < segs.length; s += 1) {
      if (used[s]) continue;
      used[s] = 1;
      const line = [segs[s][2], segs[s][3]];
      walk(segs[s][1], line, (p) => line.push(p));
      const back = [];
      walk(segs[s][0], back, (p) => back.push(p));
      const coords = back.reverse().concat(line);
      if (coords.length < 3) continue;
      features.push({
        type: 'Feature',
        properties: { level: L, label: `${L} µg/m³`, n: coords.length },
        geometry: { type: 'LineString', coordinates: coords },
      });
    }
  }
  return { type: 'FeatureCollection', features };
}
