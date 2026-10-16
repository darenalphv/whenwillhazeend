/* charts.js — Chart.js 4.
 *  - burned area per year as bars coloured by ENSO phase, ONI on a second
 *    axis, and (CONTRACT_V2) a whisker wherever the literature gives a range
 *  - season start -> end as a Gantt-style timeline across the calendar
 *  - (exported) the exhaustion trajectory fan used by the playground
 *  - (exported) the odds-over-time chart for the odds section
 */

import {
  $, el, panel, setPanelState, REDUCED_MOTION,
  fmtDate, fmtInt, fmtNum, fmtSig, dayOfYear, ensoLabel, COLOR, rangeOf, withAlpha,
} from './util.js';

let burnedChart = null;
let ganttChart = null;
let trajChart = null;
let oddsChart = null;

const MONTH_START = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const MONTH_NAME = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function baseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: REDUCED_MOTION ? { duration: 120 } : { duration: 600 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: COLOR.paperHi,
        borderColor: COLOR.line2,
        borderWidth: 1,
        titleColor: COLOR.ink,
        bodyColor: COLOR.ink2,
        footerColor: COLOR.ink3,
        titleFont: { family: '"Public Sans", system-ui, sans-serif', size: 13, weight: '600' },
        bodyFont: { family: '"IBM Plex Mono", ui-monospace, monospace', size: 12 },
        footerFont: { family: '"Public Sans", system-ui, sans-serif', size: 12, weight: '400' },
        padding: 10,
        displayColors: true,
        boxWidth: 10,
        boxHeight: 10,
        boxPadding: 4,
        cornerRadius: 6,
        caretSize: 5,
      },
    },
  };
}

const axisTicks = () => ({ color: COLOR.ink3, font: { size: 12 } });

/* ------------------------------------------------------- 1. burned + ONI */

export function initHistoryCharts(history) {
  setPanelState(panel('history'), 'ok');     // reveal before Chart.js measures
  if (!window.Chart) throw new Error('Chart.js failed to load from the CDN');
  const seasons = (history.seasons || []).slice().sort((a, b) => a.year - b.year);
  if (!seasons.length) throw new Error('the history endpoint returned no seasons');
  buildBurnedChart(seasons);
  buildGanttChart(seasons);
  buildEnsoLegend(seasons);
}

/** Draws a low–high whisker on each Kalimantan bar that has a literature range. */
const rangeWhiskers = {
  id: 'rangeWhiskers',
  afterDatasetsDraw(chart, _args, opts) {
    const ranges = opts && opts.ranges;
    if (!ranges || !ranges.some(Boolean)) return;
    const meta = chart.getDatasetMeta(opts.datasetIndex);
    const y = chart.scales.y;
    const c = chart.ctx;
    c.save();
    c.strokeStyle = opts.color;
    c.lineWidth = 1.5;
    meta.data.forEach((bar, i) => {
      const r = ranges[i];
      if (!r || bar.hidden) return;
      const x = bar.x;
      const y0 = y.getPixelForValue(r.low), y1 = y.getPixelForValue(r.high);
      c.beginPath();
      c.moveTo(x, y0); c.lineTo(x, y1);
      c.moveTo(x - 4, y0); c.lineTo(x + 4, y0);
      c.moveTo(x - 4, y1); c.lineTo(x + 4, y1);
      c.stroke();
    });
    c.restore();
  },
};

function buildBurnedChart(seasons) {
  const canvas = $('#chart-burned');
  if (!canvas) return;
  if (burnedChart) burnedChart.destroy();

  const labels = seasons.map((s) => String(s.year));
  const kalRanges = seasons.map((s) => rangeOf(s, 'burned_area_mha_kalimantan'));
  const idnRanges = seasons.map((s) => rangeOf(s, 'burned_area_mha_indonesia'));
  const kal = seasons.map((s) => s.burned_area_mha_kalimantan);
  const idn = seasons.map((s) => s.burned_area_mha_indonesia);
  const oni = seasons.map((s) => s.oni_peak);
  const colors = seasons.map((s) => COLOR.enso(s.enso_phase));
  const anyRange = kalRanges.some(Boolean);
  if (anyRange) {
    const sub = $('#burned-sub');
    if (sub && !sub.dataset.ranged) {
      sub.dataset.ranged = '1';
      sub.textContent += ' A black whisker marks a year where published estimates disagree: it spans the lowest to the highest.';
    }
  }

  const opts = baseOptions();
  opts.onClick = (_e, elements) => {
    if (!elements.length) return;
    const year = seasons[elements[0].index].year;
    document.dispatchEvent(new CustomEvent('wwhe:select-year', { detail: { year } }));
  };
  opts.onHover = (e, elements) => {
    e.native.target.style.cursor = elements.length ? 'pointer' : 'default';
  };
  opts.plugins.rangeWhiskers = { ranges: kalRanges, datasetIndex: 1, color: COLOR.ink };
  opts.plugins.tooltip.callbacks = {
    title: (items) => {
      const s = seasons[items[0].dataIndex];
      return `${s.year} · ${ensoLabel(s.enso_phase)}`;
    },
    label: (item) => {
      const s = seasons[item.dataIndex];
      if (item.dataset.label === 'ONI peak') return ` Peak ONI ${fmtSig(s.oni_peak)}`;
      if (item.dataset.label === 'Indonesia') {
        const r = idnRanges[item.dataIndex];
        return r ? ` Indonesia ${fmtNum(r.low, 2)}–${fmtNum(r.high, 2)} Mha`
          : ` Indonesia ${fmtNum(s.burned_area_mha_indonesia, 2)} Mha`;
      }
      const r = kalRanges[item.dataIndex];
      return r ? ` Kalimantan ${fmtNum(r.low, 2)}–${fmtNum(r.high, 2)} Mha`
        : ` Kalimantan ${fmtNum(s.burned_area_mha_kalimantan, 2)} Mha`;
    },
    footer: (items) => {
      const s = seasons[items[0].dataIndex];
      const out = [];
      if (s.duration_days != null) out.push(`Season length ${fmtInt(s.duration_days)} days`);
      if (s.hotspot_count != null) out.push(`Hotspots ${fmtInt(s.hotspot_count)}`);
      const d = rangeOf(s, 'excess_deaths');
      if (d) out.push(`Excess deaths: studies give ${fmtInt(d.low)}–${fmtInt(d.high)}`);
      else if (s.excess_deaths_est != null) out.push(`Excess deaths (estimate) ${fmtInt(s.excess_deaths_est)}`);
      if (kalRanges[items[0].dataIndex] || idnRanges[items[0].dataIndex]) out.push('Bar = the season record’s single figure');
      out.push('Select to load this year on the map');
      return out;
    },
  };
  opts.scales = {
    x: {
      grid: { display: false },
      ticks: { ...axisTicks(), maxRotation: 0, autoSkipPadding: 10 },
      border: { color: COLOR.ink3 },
    },
    y: {
      title: { display: true, text: 'Burned area (Mha)', color: COLOR.ink3, font: { size: 12 } },
      grid: { color: COLOR.line, drawTicks: false },
      ticks: { ...axisTicks(), padding: 6 },
      border: { display: false },
      beginAtZero: true,
    },
    y1: {
      position: 'right',
      title: { display: true, text: 'Peak ONI (°C)', color: COLOR.cool, font: { size: 12 } },
      grid: { display: false },
      ticks: { color: COLOR.cool, padding: 6, font: { size: 12 } },
      border: { display: false },
      suggestedMin: -2.5,
      suggestedMax: 3,
    },
  };

  burnedChart = new Chart(canvas, {
    data: {
      labels,
      datasets: [
        {
          type: 'bar', label: 'Indonesia', data: idn,
          backgroundColor: colors.map((c) => withAlpha(c, 0.14)),
          borderColor: colors.map((c) => withAlpha(c, 0.4)),
          borderWidth: 1, borderRadius: 2, yAxisID: 'y', order: 3,
          barPercentage: 0.95, categoryPercentage: 0.84,
        },
        {
          type: 'bar', label: 'Kalimantan', data: kal,
          backgroundColor: colors, borderRadius: 2, yAxisID: 'y', order: 2,
          barPercentage: 0.56, categoryPercentage: 0.84,
        },
        {
          type: 'line', label: 'ONI peak', data: oni, yAxisID: 'y1',
          borderColor: COLOR.cool, backgroundColor: COLOR.cool,
          borderWidth: 1.8, pointRadius: 2.5, pointHoverRadius: 5,
          tension: 0.25, spanGaps: true, order: 1,
        },
      ],
    },
    options: opts,
    plugins: [rangeWhiskers],
  });
}

function buildEnsoLegend(seasons) {
  const box = $('#enso-legend');
  if (!box) return;
  box.textContent = '';
  const order = ['el_nino', 'neutral', 'la_nina'];
  const phases = [...new Set(seasons.map((s) => s.enso_phase).filter(Boolean))]
    .sort((a, b) => order.indexOf(a) - order.indexOf(b));
  for (const ph of phases) {
    box.append(el('span', { class: 'legend-row' }, [
      el('span', { class: 'legend-swatch', style: `background:${COLOR.enso(ph)}` }),
      el('span', { text: ensoLabel(ph) }),
    ]));
  }
  box.append(el('span', { class: 'legend-row' }, [
    el('span', { class: 'legend-swatch', style: `background:${withAlpha(COLOR.ink3, 0.14)};border:1px solid ${withAlpha(COLOR.ink3, 0.45)}` }),
    el('span', { text: 'All Indonesia (pale)' }),
  ]));
  box.append(el('span', { class: 'legend-row' }, [
    el('span', { class: 'legend-swatch legend-swatch--line', style: `background:${COLOR.cool}` }),
    el('span', { text: 'Peak ONI, right axis' }),
  ]));
}

/* ----------------------------------------------------------- 2. the Gantt */

function buildGanttChart(seasons) {
  const canvas = $('#chart-gantt');
  if (!canvas) return;
  if (ganttChart) ganttChart.destroy();

  const rows = seasons.filter((s) => s.start_date && s.end_date).slice().sort((a, b) => b.year - a.year);
  const holder = $('#gantt-holder');
  if (holder) holder.style.height = `${Math.max(240, rows.length * 19 + 50)}px`;

  const spans = rows.map((s) => {
    const a = dayOfYear(s.start_date);
    let b = dayOfYear(s.end_date);
    if (b != null && a != null && b < a) b = 366;   // runs past new year: clamp to the edge
    if (a != null && b != null && b === a) b = a + 1;
    return [a, b];
  });
  const allDays = spans.flat().filter((v) => v != null);
  const lo = MONTH_START.filter((m) => m <= Math.min(...allDays)).pop() || 1;
  const hiIdx = MONTH_START.findIndex((m) => m > Math.max(...allDays));
  const hi = hiIdx === -1 ? 366 : MONTH_START[hiIdx];

  const opts = baseOptions();
  opts.indexAxis = 'y';
  opts.interaction = { mode: 'nearest', intersect: true };
  opts.onClick = (_e, elements) => {
    if (!elements.length) return;
    document.dispatchEvent(new CustomEvent('wwhe:select-year', { detail: { year: rows[elements[0].index].year } }));
  };
  opts.onHover = (e, elements) => {
    e.native.target.style.cursor = elements.length ? 'pointer' : 'default';
  };
  opts.plugins.tooltip.callbacks = {
    title: (items) => {
      const s = rows[items[0].dataIndex];
      return `${s.year} · ${ensoLabel(s.enso_phase)}`;
    },
    label: (item) => {
      const s = rows[item.dataIndex];
      return ` ${fmtDate(s.start_date, { withYear: false })} → ${fmtDate(s.end_date, { withYear: false })} · ${fmtInt(s.duration_days)} days`;
    },
    footer: (items) => {
      const s = rows[items[0].dataIndex];
      const out = [];
      if (s.peak_date) out.push(`Peak ${fmtDate(s.peak_date, { withYear: false })}`);
      if (s.burned_area_mha_kalimantan != null) out.push(`Burned, Kalimantan ${fmtNum(s.burned_area_mha_kalimantan, 2)} Mha`);
      out.push('Select to load this year on the map');
      return out;
    },
  };
  opts.scales = {
    x: {
      min: lo, max: hi,
      grid: { color: COLOR.line },
      border: { display: false },
      afterBuildTicks: (axis) => {
        axis.ticks = MONTH_START.filter((d) => d >= axis.min && d <= axis.max).map((value) => ({ value }));
      },
      ticks: {
        ...axisTicks(),
        autoSkip: false,
        callback: (value) => {
          const i = MONTH_START.indexOf(value);
          return i === -1 ? '' : MONTH_NAME[i];
        },
      },
    },
    y: {
      grid: { display: false },
      border: { display: false },
      ticks: { ...axisTicks(), autoSkip: false, font: { size: 12, family: '"IBM Plex Mono", ui-monospace, monospace' } },
    },
  };

  ganttChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: rows.map((s) => String(s.year)),
      datasets: [{
        label: 'Burn window',
        data: spans,
        backgroundColor: rows.map((s) => COLOR.enso(s.enso_phase)),
        borderRadius: 2,
        borderSkipped: false,
        barPercentage: 0.7,
        categoryPercentage: 0.9,
      }],
    },
    options: opts,
  });
}

/* ---------------------------------------------- 3. exhaustion trajectories */

/** Used by sim.js. Draws the p10/p50/p90 fan of remaining burnable stock. */
export function renderTrajectory(canvasSel, traj) {
  const canvas = $(canvasSel);
  if (!canvas || !window.Chart) return;
  if (trajChart) { trajChart.destroy(); trajChart = null; }
  if (!traj || !Array.isArray(traj.years) || !traj.years.length) return;

  const opts = baseOptions();
  opts.plugins.tooltip.callbacks = {
    title: (items) => `Year ${items[0].label}`,
    label: (item) => ` ${item.dataset.label}: ${fmtNum(item.parsed.y, 2)} Mha`,
  };
  opts.scales = {
    x: {
      grid: { display: false },
      border: { color: COLOR.ink3 },
      ticks: { ...axisTicks(), maxTicksLimit: 6, maxRotation: 0 },
    },
    y: {
      beginAtZero: true,
      grid: { color: COLOR.line2, drawTicks: false },
      border: { display: false },
      ticks: { ...axisTicks(), maxTicksLimit: 5 },
      title: { display: true, text: 'Burnable stock (Mha)', color: COLOR.ink3, font: { size: 12 } },
    },
  };
  opts.elements = { point: { radius: 0, hitRadius: 12 } };

  trajChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: traj.years.map(String),
      datasets: [
        { label: 'p90 (slowest)', data: traj.p90, borderColor: 'transparent', backgroundColor: withAlpha(COLOR.ember, 0.16), fill: '+1', tension: 0.2 },
        { label: 'p10 (fastest)', data: traj.p10, borderColor: 'transparent', backgroundColor: 'transparent', tension: 0.2 },
        { label: 'p50 (median)', data: traj.p50, borderColor: COLOR.ember2, borderWidth: 2, tension: 0.2, fill: false },
      ],
    },
    options: opts,
  });
}

/* ------------------------------------------------------ 4. odds over time */

const ODDS_COLORS = () => [COLOR.ink, COLOR.cool, COLOR.ember, COLOR.haze, COLOR.amber];

/** Short label for a market, for the legend: "by 30 Nov". */
function marketShort(m) {
  if (m && m.threshold_date) return `Ends by ${fmtDate(m.threshold_date, { withYear: false })}`;
  return m && m.question ? m.question : String(m && m.id);
}

/**
 * history: { series: [{ as_of, p50_end_date, markets: { id: p } }] }
 * markets: the current market list (for labels and order)
 */
export function renderOddsHistory(canvasSel, legendSel, history, markets) {
  const canvas = $(canvasSel);
  if (!canvas || !window.Chart) return;
  if (oddsChart) { oddsChart.destroy(); oddsChart = null; }
  const series = (history.series || []).slice().sort((a, b) => String(a.as_of).localeCompare(String(b.as_of)));
  const ids = [];
  for (const m of markets || []) ids.push(m.id);
  for (const pt of series) for (const k of Object.keys(pt.markets || {})) if (!ids.includes(k)) ids.push(k);
  const byId = new Map((markets || []).map((m) => [m.id, m]));
  const palette = ODDS_COLORS();

  const datasets = ids.map((id, i) => ({
    label: marketShort(byId.get(id) || { id }),
    data: series.map((pt) => {
      const v = pt.markets ? Number(pt.markets[id]) : NaN;
      return Number.isFinite(v) ? v * 100 : null;
    }),
    borderColor: palette[i % palette.length],
    backgroundColor: palette[i % palette.length],
    borderWidth: i === 0 ? 2.4 : 1.8,
    pointRadius: 0,
    pointHoverRadius: 4,
    stepped: false,
    tension: 0,
    spanGaps: true,
  }));

  const legend = $(legendSel);
  if (legend) {
    legend.textContent = '';
    datasets.forEach((d) => legend.append(el('span', { class: 'legend-row' }, [
      el('span', { class: 'legend-swatch legend-swatch--line', style: `background:${d.borderColor}` }),
      el('span', { text: d.label }),
    ])));
  }

  const opts = baseOptions();
  opts.plugins.tooltip.callbacks = {
    title: (items) => `As of ${fmtDate(series[items[0].dataIndex].as_of)}`,
    label: (item) => ` ${item.dataset.label}: ${item.parsed.y == null ? '—' : `${Math.round(item.parsed.y)}%`} yes`,
    footer: (items) => {
      const pt = series[items[0].dataIndex];
      return pt.p50_end_date ? `Median end date then: ${fmtDate(pt.p50_end_date)}` : '';
    },
  };
  opts.scales = {
    x: {
      grid: { display: false },
      border: { color: COLOR.ink3 },
      ticks: {
        ...axisTicks(),
        maxRotation: 0,
        autoSkipPadding: 18,
        callback(value) { return fmtDate(series[value].as_of, { withYear: false }); },
      },
    },
    y: {
      min: 0, max: 100,
      grid: { color: COLOR.line },
      border: { display: false },
      ticks: { ...axisTicks(), stepSize: 25, callback: (v) => `${v}%` },
    },
  };

  oddsChart = new Chart(canvas, {
    type: 'line',
    data: { labels: series.map((pt) => pt.as_of), datasets },
    options: opts,
  });
}

/* ------------------------------------------------- 5. this week (M5) */

const nowCharts = new Map();

/** A dashed horizontal line at the 200 mark, labelled with the index's own words. */
const thresholdLine = {
  id: 'thresholdLine',
  afterDatasetsDraw(chart, _a, opts) {
    if (!opts || opts.value == null) return;
    const y = chart.scales.y.getPixelForValue(opts.value);
    const { left, right } = chart.chartArea;
    const c = chart.ctx;
    c.save();
    c.strokeStyle = COLOR.ink;
    c.setLineDash([4, 4]);
    c.lineWidth = 1;
    c.beginPath(); c.moveTo(left, y); c.lineTo(right, y); c.stroke();
    c.setLineDash([]);
    // label at the left edge on a paper backing, so a line crossing it stays legible
    const text = opts.label || String(opts.value);
    c.font = '500 12px "Public Sans", system-ui, sans-serif';
    const w = c.measureText(text).width;
    c.fillStyle = COLOR.paperHi;
    c.fillRect(left + 2, y - 19, w + 8, 16);
    c.fillStyle = COLOR.ink;
    c.textAlign = 'left';
    c.fillText(text, left + 6, y - 7);
    c.restore();
  },
};

const tzFmt = (t, opts) => {
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return String(t);
  return d.toLocaleString('en-GB', { timeZone: 'Asia/Kuala_Lumpur', ...opts });
};
export const fmtLocalTime = (t) => tzFmt(t, { day: 'numeric', month: 'short', hour: 'numeric', hour12: true });

/**
 * One chart per index. cfg: { lines: [{label, points:[{t,v}]}], yLabel, threshold, thresholdLabel, unit }
 */
export function renderNowChart(canvas, legendBox, cfg) {
  if (!canvas || !window.Chart) return;
  const old = nowCharts.get(canvas.id);
  if (old) old.destroy();
  const palette = [COLOR.ink, COLOR.cool, COLOR.ember, COLOR.haze, COLOR.amber];
  const datasets = cfg.lines.map((l, i) => ({
    label: l.label,
    data: l.points.map((p) => ({ x: new Date(p.t).getTime(), y: p.v == null ? null : Number(p.v) })),
    borderColor: palette[i % palette.length],
    backgroundColor: palette[i % palette.length],
    borderWidth: i === 0 ? 2.2 : 1.6,
    pointRadius: 0,
    pointHoverRadius: 4,
    tension: 0.15,
    spanGaps: true,
  }));
  const allV = datasets.flatMap((d) => d.data.map((p) => p.y)).filter((v) => v != null);
  const xs = datasets.flatMap((d) => d.data.map((p) => p.x)).filter(Number.isFinite);
  const yMax = Math.max(cfg.threshold ? cfg.threshold * 1.15 : 0, ...allV) * 1.05;

  if (legendBox) {
    legendBox.textContent = '';
    if (datasets.length > 1) {
      datasets.forEach((d) => legendBox.append(el('span', { class: 'legend-row' }, [
        el('span', { class: 'legend-swatch legend-swatch--line', style: `background:${d.borderColor}` }),
        el('span', { text: d.label }),
      ])));
    }
  }

  const opts = baseOptions();
  opts.interaction = { mode: 'nearest', axis: 'x', intersect: false };
  opts.plugins.thresholdLine = { value: cfg.threshold, label: cfg.thresholdLabel };
  opts.plugins.tooltip.callbacks = {
    title: (items) => fmtLocalTime(items[0].parsed.x),
    label: (item) => ` ${item.dataset.label}: ${item.parsed.y == null ? '—' : fmtInt(item.parsed.y)} ${cfg.unit || ''}`.trimEnd(),
  };
  opts.scales = {
    x: {
      type: 'linear',
      min: Math.min(...xs),
      max: Math.max(...xs),
      grid: { display: false },
      border: { color: COLOR.ink3 },
      ticks: {
        ...axisTicks(),
        maxRotation: 0,
        autoSkipPadding: 14,
        stepSize: 86400000,
        maxTicksLimit: 8,
        callback: (v) => tzFmt(v, { weekday: 'short', day: 'numeric' }),
      },
    },
    y: {
      min: 0,
      suggestedMax: yMax,
      grid: { color: COLOR.line },
      border: { display: false },
      ticks: { ...axisTicks(), maxTicksLimit: 6 },
      title: { display: !!cfg.yLabel, text: cfg.yLabel, color: COLOR.ink3, font: { size: 12 } },
    },
  };
  nowCharts.set(canvas.id, new Chart(canvas, { type: 'line', data: { datasets }, options: opts, plugins: [thresholdLine] }));
}

let firesChart = null;

/** Satellite detections per day, stacked by region. */
export function renderFiresByDay(canvasSel, byDay, regions) {
  const canvas = $(canvasSel);
  if (!canvas || !window.Chart) return;
  if (firesChart) firesChart.destroy();
  const colors = { kalimantan: COLOR.ember, sumatra: COLOR.amber, peninsular_malaysia: COLOR.cool };
  const fallback = [COLOR.haze, COLOR.ink3, COLOR.ink];
  const opts = baseOptions();
  opts.scales = {
    x: { stacked: true, grid: { display: false }, border: { color: COLOR.ink3 }, ticks: { ...axisTicks(), maxRotation: 0 } },
    y: { stacked: true, beginAtZero: true, grid: { color: COLOR.line }, border: { display: false }, ticks: { ...axisTicks(), maxTicksLimit: 5 } },
  };
  opts.plugins.tooltip.callbacks = {
    label: (item) => ` ${item.dataset.label}: ${fmtInt(item.parsed.y)} detections`,
  };
  firesChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: byDay.map((d) => fmtDate(d.date, { withYear: false })),
      datasets: regions.map((r, i) => ({
        label: r.label,
        data: byDay.map((d) => Number(d[r.id]) || 0),
        backgroundColor: colors[r.id] || fallback[i % fallback.length],
        borderRadius: 1,
        barPercentage: 0.8,
      })),
    },
    options: opts,
  });
}
