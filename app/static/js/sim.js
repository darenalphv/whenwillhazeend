/* sim.js — the model playground.
 *
 * Sliders for ONI, burn reduction, restoration and deforestation plus a scenario
 * select; POSTs to /api/simulate/season and /api/simulate/exhaustion and animates
 * the results. Deltas are measured against the baseline run shown in the hero.
 */

import {
  api, REDUCED_MOTION, describeError,
  $, el, panel, setPanelState,
  fmtDate, fmtInt, fmtNum, fmtSig, animateNumber, debounce, titleCase,
  horizonOf, exhaustionYearText, neverText,
} from './util.js';
import { renderTrajectory } from './charts.js';

let baseline = { season: null, exhaustion: null };
let defaults = null;
let today = new Date().toISOString().slice(0, 10);
let runToken = 0;

const FIELDS = {
  oni: '#sim-oni',
  burnred: '#sim-burnred',
  restore: '#sim-restore',
  defor: '#sim-defor',
  horizon: '#sim-horizon',
  scenario: '#sim-scenario',
};

/* --------------------------------------------------------------- defaults */

function oniFromSeason(season) {
  // the season response echoes the resolved ONI at the top level
  if (season && Number.isFinite(Number(season.oni))) return Number(season.oni);
  const d = ((season && season.drivers) || []).find((x) => x.id === 'oni');
  return d && Number.isFinite(Number(d.value)) ? Number(d.value) : 0;
}

export function initSim(ctx) {
  baseline.season = ctx.season || null;
  baseline.exhaustion = ctx.exhaustion || null;
  today = ctx.today || today;

  const fuel = ctx.fuel || {};
  const baseAssume = (baseline.exhaustion && baseline.exhaustion.assumptions) || {};
  defaults = {
    oni: oniFromSeason(baseline.season),
    burnred: 0,
    restore: baseAssume.restoration_mha_per_yr != null ? Number(baseAssume.restoration_mha_per_yr)
      : (fuel.restoration_mha_per_yr != null ? Number(fuel.restoration_mha_per_yr) : 0.05),
    defor: baseAssume.deforestation_mha_per_yr != null ? Number(baseAssume.deforestation_mha_per_yr)
      : (fuel.deforestation_mha_per_yr != null ? Number(fuel.deforestation_mha_per_yr) : 0.13),
    horizon: baseAssume.horizon_years != null ? Number(baseAssume.horizon_years) : 300,
    scenario: (baseline.season && baseline.season.scenario) || 'baseline',
  };

  applyDefaults();
  syncLabels();

  const form = $('#sim-form');
  const auto = debounce(() => run(), 650);

  for (const sel of Object.values(FIELDS)) {
    const node = $(sel);
    if (!node) continue;
    node.addEventListener('input', () => { syncLabels(); auto(); });
    node.addEventListener('change', () => { syncLabels(); auto(); });
  }

  form.addEventListener('submit', (e) => { e.preventDefault(); run(); });
  $('#sim-reset').addEventListener('click', () => {
    applyDefaults();
    syncLabels();
    run();
  });

  // seed the panels with the baseline run rather than an empty box
  if (baseline.season) renderSeason(baseline.season);
  else setPanelState(panel('sim-season'), 'error',
    'The baseline season run failed, so there is nothing to compare against yet. Press “Run the model”.');
  if (baseline.exhaustion) renderExhaustion(baseline.exhaustion);
  else setPanelState(panel('sim-exhaustion'), 'error',
    'The baseline exhaustion run failed. Press “Run the model” to try again.');
}

function applyDefaults() {
  $(FIELDS.oni).value = String(defaults.oni);
  $(FIELDS.burnred).value = String(defaults.burnred);
  $(FIELDS.restore).value = String(defaults.restore);
  $(FIELDS.defor).value = String(defaults.defor);
  $(FIELDS.horizon).value = String(defaults.horizon);
  const sel = $(FIELDS.scenario);
  if ([...sel.options].some((o) => o.value === defaults.scenario)) sel.value = defaults.scenario;
}

function readInputs() {
  return {
    oni: Number($(FIELDS.oni).value),
    burn_reduction_pct: Number($(FIELDS.burnred).value),
    restoration_mha_per_yr: Number($(FIELDS.restore).value),
    deforestation_mha_per_yr: Number($(FIELDS.defor).value),
    horizon_years: Number($(FIELDS.horizon).value),
    scenario: $(FIELDS.scenario).value,
  };
}

function syncLabels() {
  const v = readInputs();
  $('#sim-oni-out').textContent = `${v.oni > 0 ? '+' : ''}${fmtNum(v.oni, 1)}`;
  $('#sim-burnred-out').textContent = `${fmtInt(v.burn_reduction_pct)}%`;
  $('#sim-restore-out').textContent = `${fmtNum(v.restoration_mha_per_yr, 2)} Mha/yr`;
  $('#sim-defor-out').textContent = `${fmtNum(v.deforestation_mha_per_yr, 2)} Mha/yr`;
  $('#sim-horizon-out').textContent = `${fmtInt(v.horizon_years)} yrs`;
}

/* -------------------------------------------------------------------- run */

async function run() {
  const token = ++runToken;
  const v = readInputs();
  const btn = $('#sim-run');
  btn.disabled = true;
  btn.textContent = 'Running…';
  for (const name of ['sim-season', 'sim-exhaustion']) {
    const p = panel(name);
    if (p && p.dataset.panelState === 'ok') p.dataset.busy = 'true';
    else setPanelState(p, 'loading');
  }

  const seasonReq = api('/api/simulate/season', {
    method: 'POST',
    body: {
      as_of: today, region: 'kalimantan', oni: v.oni,
      n_sims: 3000, scenario: v.scenario, seed: 7,
    },
  });
  const exReq = api('/api/simulate/exhaustion', {
    method: 'POST',
    body: {
      n_sims: 1500,
      horizon_years: v.horizon_years,
      burn_reduction_pct: v.burn_reduction_pct,
      restoration_mha_per_yr: v.restoration_mha_per_yr,
      deforestation_mha_per_yr: v.deforestation_mha_per_yr,
      seed: 7,
    },
  });

  const [seasonRes, exRes] = await Promise.allSettled([seasonReq, exReq]);
  if (token !== runToken) return;   // a newer run superseded this one

  const sp = panel('sim-season');
  sp.dataset.busy = 'false';
  if (seasonRes.status === 'fulfilled') {
    renderSeason(seasonRes.value);
    document.dispatchEvent(new CustomEvent('wwhe:sim-season', {
      detail: { season: seasonRes.value },
    }));
    document.dispatchEvent(new CustomEvent('wwhe:caveats', {
      detail: {
        label: `Season model, “${titleCase(seasonRes.value.scenario || v.scenario)}” run`,
        caveats: seasonRes.value.caveats,
      },
    }));
  } else {
    setPanelState(sp, 'error', describeError(seasonRes.reason, 'season model'));
  }

  const ep = panel('sim-exhaustion');
  ep.dataset.busy = 'false';
  if (exRes.status === 'fulfilled') {
    renderExhaustion(exRes.value);
    document.dispatchEvent(new CustomEvent('wwhe:caveats', {
      detail: {
        label: 'Fuel-exhaustion model, playground run',
        caveats: exRes.value.caveats,
      },
    }));
  } else {
    setPanelState(ep, 'error', describeError(exRes.reason, 'fuel-exhaustion model'));
  }

  btn.disabled = false;
  btn.textContent = 'Run the model';
}

/* ----------------------------------------------------------------- render */

function renderSeason(res) {
  const p = panel('sim-season');
  setPanelState(p, 'ok');
  const d = res.days_remaining || {};
  const e = res.end_date || {};

  animateNumber($('#sim-days'), d.p50, { format: fmtInt });
  $('#sim-interval').textContent =
    d.p10 != null && d.p90 != null ? `${fmtInt(d.p10)}–${fmtInt(d.p90)} days` : '—';
  $('#sim-end').textContent = fmtDate(e.p50);
  $('#sim-confidence').textContent = res.confidence || 'unstated';

  const delta = $('#sim-delta');
  delta.textContent = '';
  const base = baseline.season && baseline.season.days_remaining;
  if (base && base.p50 != null && d.p50 != null) {
    const diff = d.p50 - base.p50;
    if (diff === 0) {
      delta.append(document.createTextNode('Same as the baseline run.'));
    } else {
      delta.append(
        el('span', {
          class: diff > 0 ? 'up' : 'down',
          text: `${diff > 0 ? '+' : ''}${fmtInt(diff)} days`,
        }),
        document.createTextNode(` against the baseline ${fmtInt(base.p50)}.`),
      );
    }
  }
}

function renderExhaustion(res) {
  const p = panel('sim-exhaustion');
  setPanelState(p, 'ok');     // reveal before the canvas is measured
  const y = res.exhaustion_year || {};

  const { years, lastYear } = horizonOf(res);
  const answer = exhaustionYearText(res);

  const node = $('#sim-year');
  node.classList.toggle('result-card__big--text', !answer.reached);
  if (answer.reached) {
    node.textContent = '';
    animateNumber(node, answer.value, { format: (v) => String(Math.round(v)) });
  } else {
    // horizon-relative: the fuel did not run out before the run stopped
    node.textContent = answer.text;
  }
  $('#sim-year-interval').textContent =
    y.p10 != null && y.p90 != null ? `${y.p10}–${y.p90}` : 'not reached on this horizon';
  $('#sim-never').textContent = neverText(res);

  renderTrajectory('#chart-traj', res.trajectories);

  const tbody = $('#pool-table tbody');
  tbody.textContent = '';
  for (const pool of res.per_pool || []) {
    tbody.append(el('tr', {}, [
      el('td', { text: pool.label || titleCase(pool.id || '—') }),
      el('td', {
        class: pool.exhaustion_year_p50 == null ? 'pool-table__unreached' : null,
        text: pool.exhaustion_year_p50 != null
          ? String(pool.exhaustion_year_p50)
          : (lastYear ? `not by ${lastYear}` : `not within ${fmtInt(years)} yrs`),
      }),
      el('td', { text: pool.remaining_mha_2050 != null ? `${fmtNum(pool.remaining_mha_2050, 2)} Mha` : '—' }),
    ]));
  }

  const cum = $('#sim-cum');
  cum.textContent = '';
  const parts = [];
  if (res.cumulative_burned_mha_by_2050 != null) {
    parts.push(`${fmtNum(res.cumulative_burned_mha_by_2050, 2)} Mha burned by 2050`);
  }
  if (res.cumulative_pm25_kt != null) {
    parts.push(`${fmtInt(res.cumulative_pm25_kt)} kt of PM2.5 released`);
  }
  cum.textContent = parts.length
    ? `Over a ${fmtInt(years)}-year run: ${parts.join(', ')}.`
    : 'The model did not report cumulative totals for this run.';
}
