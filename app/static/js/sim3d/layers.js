/**
 * layers.js — deck.gl layers for smoke, fires and embers.
 *
 * Everything is a ScatterplotLayer fed binary attributes (typed arrays the
 * particle systems write each frame), with a fragment-shader hook that turns
 * the hard disc into a soft puff or a hot glow. Billboarded, depth-tested
 * against MapLibre's terrain (so ridges hide smoke behind them) but not
 * depth-writing (so translucent puffs blend instead of punching holes).
 */

let classes = null;

export function layerClasses(deck) {
  if (classes) return classes;
  const Base = deck.ScatterplotLayer;

  class SoftPuffLayer extends Base {
    getShaders() {
      const s = super.getShaders();
      return {
        ...s,
        inject: {
          ...(s.inject || {}),
          'fs:DECKGL_FILTER_COLOR': `
            float d = length(geometry.uv);
            float k = max(0.0, 1.0 - d);
            color.a *= k * k * (3.0 - 2.0 * k);
          `,
        },
      };
    }
  }
  SoftPuffLayer.layerName = 'HazeSoftPuffLayer';

  class GlowLayer extends Base {
    getShaders() {
      const s = super.getShaders();
      return {
        ...s,
        inject: {
          ...(s.inject || {}),
          'fs:DECKGL_FILTER_COLOR': `
            float d = length(geometry.uv);
            float k = max(0.0, 1.0 - d);
            float core = pow(k, 2.4);
            color.rgb = mix(color.rgb, vec3(1.0, 0.96, 0.84), core * 0.75);
            color.a *= pow(k, 1.25);
          `,
        },
      };
    }
  }
  GlowLayer.layerName = 'HazeGlowLayer';

  classes = { SoftPuffLayer, GlowLayer };
  return classes;
}

const TRANSLUCENT = { depthWriteEnabled: false, depthCompare: 'less-equal' };
// Soft puffs are not depth-tested: a 20 km billboard sitting a few hundred
// metres above the ground would be sliced by the terrain into hard-edged
// domes. Haze is translucent and lies mostly over the lowlands, so drawing it
// over the occasional ridge is the lesser error.
const VEIL = { depthWriteEnabled: false, depthCompare: 'always' };

const bin = (buf, n) => ({
  length: n,
  attributes: {
    getPosition: { value: buf.pos, size: 3 },
    getFillColor: { value: buf.col, size: 4, normalized: true },
    getRadius: { value: buf.rad, size: 1 },
  },
});

/**
 * Build the layer list. `s` is the scene's per-frame render state.
 */
export function buildLayers(deck, s) {
  const { SoftPuffLayer, GlowLayer } = layerClasses(deck);
  const out = [];
  const common = { billboard: true, stroked: false, antialiasing: false, parameters: TRANSLUCENT, pickable: false };

  if (s.smoke && s.showSmoke) {
    out.push(new SoftPuffLayer({
      ...common, id: 'smoke', parameters: VEIL,
      data: bin({ pos: s.smoke.pos, col: s.smoke.col, rad: s.smoke.rad }, s.smoke.n),
      radiusUnits: 'meters', radiusMinPixels: s.puffMinPx || 3, radiusMaxPixels: 90,
    }));
  }

  if (s.fires && s.showFires) {
    out.push(new GlowLayer({
      ...common, id: 'fire-glow', parameters: VEIL,
      data: bin(s.fires.glow, s.fires.glow.n), radiusUnits: 'meters', radiusMinPixels: 7, radiusMaxPixels: 140,
    }));
    out.push(new SoftPuffLayer({
      ...common, id: 'fire-column', parameters: VEIL,
      data: bin(s.fires.col, s.fires.col.n), radiusUnits: 'meters', radiusMinPixels: 2.5, radiusMaxPixels: 80,
    }));
    out.push(new GlowLayer({
      ...common, id: 'fire-flame',
      data: bin(s.fires.flame, s.fires.flame.n), radiusUnits: 'meters', radiusMinPixels: 4.5, radiusMaxPixels: 46,
    }));
    out.push(new GlowLayer({
      ...common, id: 'fire-embers',
      data: bin(s.fires.emb, s.fires.emb.n), radiusUnits: 'pixels',
    }));
    if (s.sumatra && s.sumatra.length) {
      out.push(new deck.ScatterplotLayer({
        id: 'fire-sumatra', data: s.sumatra, billboard: true, stroked: true, filled: false,
        radiusUnits: 'pixels', getRadius: 11, lineWidthUnits: 'pixels', getLineWidth: 2,
        getLineColor: [47, 93, 140, 230], getPosition: (d) => d.pos,
        parameters: TRANSLUCENT,
      }));
    }
  }
  return out;
}
