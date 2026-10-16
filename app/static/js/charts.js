/* charts.js — Chart.js 4.
 *  - burned area per year as bars coloured by ENSO phase, ONI on a second axis
 *  - season start -> end as a Gantt-style timeline across the calendar
 *  - (exported) the exhaustion trajectory fan used by the playground
 */

import {
  $, el, panel, setPanelState, REDUCED_MOTION,
  fmtDate, fmtInt, fmtNum, fmtSig, dayOfYear, ensoLabel, COLOR,
} from './util.js';

let burnedChart = null;
let ganttChart = null;
let trajChart = null;

const MONTH_START = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const MONTH_NAME = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function gridColor() { return COLOR.line; }

function baseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: REDUCED_MOTION ? false : { duration: 650 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: 'rgba(12,10,10,.96)',
        borderColor: COLOR.line,
        borderWidth: 1,
        titleColor: COLOR.ink,
        bodyColor: COLOR.ink2,
        padding: 10,
        displayColors: true,
        boxWidth: 9,
        boxHeight: 9,
        cornerRadius: 6,
      },
    },
  };
}

/* ------------------------------------------------------- 1. burned + ONI */

export function initHistoryCharts(history) {
  // reveal before Chart.js measures the canvas
  setPanelState(panel('history'), 'ok');

  if (!window.Chart) throw new Error('Chart.js failed to load from the CDN');

  const seasons = (history.seasons || []).slice().sort((a, b) => a.year - b.year);
  if (!seasons.length) throw new Error('the history endpoint returned no seasons');

  buildBurnedChart(seasons);
  buildGanttChart(seasons);
  buildEnsoLegend(seasons);
}

function buildBurnedChart(seasons) {
  const canvas = $('#chart-burned');
  if (!canvas) return;
  if (burnedChart) burnedChart.destroy();

  const labels = seasons.map((s) => String(s.year));
  const kal = seasons.map((s) => s.burned_area_mha_kalimantan);
  const idn = seasons.map((s) => s.burned_area_mha_indonesia);
  const oni = seasons.map((s) => s.oni_peak);
  const colors = seasons.map((s) => COLOR.enso(s.enso_phase));

  const opts = baseOptions();
  opts.onClick = (_e, elements) => {
    if (!elements.length) return;
    const year = seasons[elements[0].index].year;
    document.dispatchEvent(new CustomEvent('wwhe:select-year', { detail: { year } }));
  };
  opts.onHover = (e, elements) => {
    e.native.target.style.cursor = elements.length ? 'pointer' : 'default';
  };
  opts.plugins.tooltip.callbacks = {
    title: (items) => {
      const s = seasons[items[0].dataIndex];
      return `${s.year} · ${ensoLabel(s.enso_phase)}`;
    },
    label: (item) => {
      const s = seasons[item.dataIndex];
      if (item.dataset.label === 'ONI peak') return `Peak ONI: ${fmtSig(s.oni_peak)}`;
      if (item.dataset.label === 'Indonesia') {
        return `Indonesia: ${fmtNum(s.burned_area_mha_indonesia, 2)} Mha`;
      }
      return `Kalimantan: ${fmtNum(s.burned_area_mha_kalimantan, 2)} Mha`;
    },
    afterBody: (items) => {
      const s = seasons[items[0].dataIndex];
      const out = [];
      if (s.duration_days != null) out.push(`Season length: ${fmtInt(s.duration_days)} days`);
      if (s.hotspot_count != null) out.push(`Hotspots: ${fmtInt(s.hotspot_count)}`);
      if (s.excess_deaths_est != null) {
        out.push(`Excess deaths (est.): ${fmtInt(s.excess_deaths_est)}`);
      }
      out.push('Click to load this year on the map');
      return out;
    },
  };
  opts.scales = {
    x: {
      grid: { display: false },
      ticks: { color: COLOR.ink3, maxRotation: 0, autoSkipPadding: 8 },
      border: { color: gridColor() },
    },
    y: {
      title: { display: true, text: 'Burned area (Mha)', color: COLOR.ink3 },
      grid: { color: gridColor(), drawTicks: false },
      ticks: { color: COLOR.ink3, padding: 6 },
      border: { display: false },
      beginAtZero: true,
    },
    y1: {
      position: 'right',
      title: { display: true, text: 'Peak ONI (°C)', color: COLOR.cool },
      grid: { display: false },
      ticks: { color: COLOR.cool, padding: 6 },
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
          type: 'bar',
          label: 'Indonesia',
          data: idn,
          backgroundColor: colors.map((c) => c + '26'),
          borderColor: colors.map((c) => c + '55'),
          borderWidth: 1,
          borderRadius: 3,
          yAxisID: 'y',
          order: 3,
          barPercentage: 0.95,
          categoryPercentage: 0.82,
        },
        {
          type: 'bar',
          label: 'Kalimantan',
          data: kal,
          backgroundColor: colors,
          borderRadius: 3,
          yAxisID: 'y',
          order: 2,
          barPercentage: 0.58,
          categoryPercentage: 0.82,
        },
        {
          type: 'line',
          label: 'ONI peak',
          data: oni,
          yAxisID: 'y1',
          borderColor: COLOR.cool,
          backgroundColor: COLOR.cool,
          borderWidth: 1.8,
          pointRadius: 2.4,
          pointHoverRadius: 5,
          tension: 0.28,
          spanGaps: true,
          order: 1,
        },
      ],
    },
    options: opts,
  });
}

function buildEnsoLegend(seasons) {
  const box = $('#enso-legend');
  if (!box) return;
  box.textContent = '';
  const phases = [...new Set(seasons.map((s) => s.enso_phase).filter(Boolean))];
  const order = ['el_nino', 'neutral', 'la_nina'];
  phases.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  for (const ph of phases) {
    box.append(el('span', { class: 'legend-row' }, [
      el('span', { class: 'legend-swatch', style: `background:${COLOR.enso(ph)}` }),
      el('span', { text: ensoLabel(ph) }),
    ]));
  }
  box.append(el('span', { class: 'legend-row' }, [
    el('span', {
      class: 'legend-swatch',
      style: `background:transparent;border:1px solid ${COLOR.ink3};`,
    }),
    el('span', { text: 'faint bar = all Indonesia' }),
  ]));
  box.append(el('span', { class: 'legend-row' }, [
    el('span', { class: 'legend-swatch', style: `background:${COLOR.cool}` }),
    el('span', { text: 'Peak ONI (right axis)' }),
  ]));
}

/* ----------------------------------------------------------- 2. the Gantt */

function buildGanttChart(seasons) {
  const canvas = $('#chart-gantt');
  if (!canvas) return;
  if (ganttChart) ganttChart.destroy();

  const rows = seasons
    .filter((s) => s.start_date && s.end_date)
    .slice()
    .sort((a, b) => b.year - a.year);      // most recent at the top

  const spans = rows.map((s) => {
    const a = dayOfYear(s.start_date);
    let b = dayOfYear(s.end_date);
    // a season that runs past new year is clamped to the calendar edge
    if (b != null && a != null && b < a) b = 366;
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
    document.dispatchEvent(new CustomEvent('wwhe:select-year', {
      detail: { year: rows[elements[0].index].year },
    }));
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
      return `${fmtDate(s.start_date, { withYear: false })} → ${fmtDate(s.end_date, { withYear: false })} · ${fmtInt(s.duration_days)} days`;
    },
    afterBody: (items) => {
      const s = rows[items[0].dataIndex];
      const out = [];
      if (s.peak_date) out.push(`Peak: ${fmtDate(s.peak_date, { withYear: false })}`);
      if (s.burned_area_mha_kalimantan != null) {
        out.push(`Burned, Kalimantan: ${fmtNum(s.burned_area_mha_kalimantan, 2)} Mha`);
      }
      out.push('Click to load this year on the map');
      return out;
    },
  };
  opts.scales = {
    x: {
      min: lo,
      max: hi,
      grid: { color: gridColor() },
      border: { display: false },
      afterBuildTicks: (axis) => {
        axis.ticks = MONTH_START
          .filter((d) => d >= axis.min && d <= axis.max)
          .map((value) => ({ value }));
      },
      ticks: {
        color: COLOR.ink3,
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
      ticks: { color: COLOR.ink3, autoSkip: false, font: { size: 10 } },
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
        borderRadius: 3,
        borderSkipped: false,
        barPercentage: 0.72,
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
    label: (item) => `${item.dataset.label}: ${fmtNum(item.parsed.y, 2)} Mha`,
  };
  opts.scales = {
    x: {
      grid: { display: false },
      border: { color: gridColor() },
      ticks: { color: COLOR.ink3, maxTicksLimit: 6, maxRotation: 0 },
    },
    y: {
      beginAtZero: true,
      grid: { color: gridColor(), drawTicks: false },
      border: { display: false },
      ticks: { color: COLOR.ink3, maxTicksLimit: 5 },
      title: { display: true, text: 'Burnable stock (Mha)', color: COLOR.ink3 },
    },
  };
  opts.elements = { point: { radius: 0, hitRadius: 12 } };

  trajChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: traj.years.map(String),
      datasets: [
        {
          label: 'p90 (slowest)', data: traj.p90,
          borderColor: 'transparent', backgroundColor: COLOR.ember + '1f',
          fill: '+1', tension: 0.2,
        },
        {
          label: 'p10 (fastest)', data: traj.p10,
          borderColor: 'transparent', backgroundColor: 'transparent', tension: 0.2,
        },
        {
          label: 'p50 (median)', data: traj.p50,
          borderColor: COLOR.ember, borderWidth: 2, tension: 0.2, fill: false,
        },
      ],
    },
    options: opts,
  });
}
