"""Fuel-exhaustion model: in what year is there nothing left in Kalimantan to burn?

A stock-and-flow model over five land pools (intact peat, drained/degraded
peat, primary forest, secondary forest, scrub/grass), driven by a three-state
ENSO Markov chain, Monte-Carlo'd over ``n_sims`` futures.

Equations
---------
Per pool *p* and year *t*::

    S_{p,t+1} = S_{p,t} - burned_{p,t} - converted_{p,t} + restored_{p,t}

    burned_{p,t}    = A_base * m(state_t) * s_p * (S_{p,t}/S_{p,0})**theta
                      * (1 - burn_reduction_pct/100)
    converted_{p,t} = C_rate * (S_{forest,t}/S_{forest,0})**phi * w_{p,t}
                                                                 [forest pools]
    restored_{p,t}  = R_rate * (S_drained,t / S_drained,0)       [drained peat]

``A_base`` is the mean annual Kalimantan burned area in ENSO-neutral years,
taken from `load_seasons()`.  ``m(state)`` is the ENSO burned-area multiplier,
also estimated from `load_seasons()` as the ratio of mean burned area in El
Nino / La Nina years to neutral years.  ``s_p`` is the pool's relative
susceptibility from `load_fuel_stock()`, area-weighted and normalised so that
``sum_p burned_{p,0} = A_base * m``.  ``theta`` (default 0.6) is the
fuel-depletion exponent: burning falls off markedly sub-linearly as a pool
shrinks, because the last fragments are exactly the drained, roadside,
edge-exposed ones. Fragmented tropical forest burns far more readily per
hectare than continuous forest -- edge desiccation and adjacent agricultural
burning both scale with perimeter, not area (Cochrane 2003, *Nature* 421;
Alencar et al. 2015) -- so the remaining hectares get hit harder as the stock
falls.  Conversion is density-dependent with exponent ``phi = 0.5`` and is
allocated between the forest pools in proportion to what is left of each:
clearing slows as accessible forest runs out, but not proportionally, because
the frontier follows the roads.  Restoration is proportional to the remaining
drained-peat stock.

**Peat is irreversible.**  This is the point of the model and it is handled
explicitly.  A peat fire consumes a measured 0.33 +/- 0.18 m of the peat
column (Ballhorn et al. 2009, PNAS); the peat below took millennia to
accumulate at 0.5-2 mm/yr and does not come back on any timescale a person
cares about.  So each peat pool is carried not as hectares but as
**burn-cycles-equivalent hectares**::

    S_peat,0 [cycle-ha] = area_ha * (mean_peat_depth_m / burn_depth_per_fire_m)

A fire removes one cycle's worth per hectare burned, permanently.  Crucially
the depth that counts is not the depth of the dome but the depth **above the
dry-season water table** -- peat below the water table does not ignite no
matter how thick it is -- which is about 0.5 m under intact peat and about
1.0 m under drained peat, so a hectare sustains roughly 1.5 and 3 burns
respectively before it has subsided to the water table or to mineral soil and
stops carrying fire.  Restoration
(canal blocking, rewetting) can move *drained* peat back to *intact* -- it
raises the water table and lowers susceptibility -- but it can never put back
carbon that has already been emitted.  Burned forest, by contrast, is not
destroyed: it moves into the scrub/grass pool, which remains burnable, which
is why forest disappears from the "burnable forest" accounting long before the
landscape stops producing smoke.

"Exhausted" follows the contract: the **peat + forest** stock falls below 5% of
its baseline value.  Scrub and grass are excluded, because grass regrows and
would never exhaust.

Emissions::

    PM2.5_kg = burned_ha * fuel_load_t_ha * EF_g_kg

(``t/ha * 1000 kg/t * g/kg / 1000 g/kg`` collapses to exactly this product.)
Peat PM2.5 emission factor 17.3 +/- 6.0 g/kg (Stockwell et al. 2016, measured
in Central Kalimantan during the 2015 El Nino); above-ground biomass values
from Akagi et al. (2011).

ENSO Markov chain
-----------------
Years are classified by ONI from `load_enso()` into la_nina (ONI <= -0.5),
neutral, el_nino (ONI >= +0.5).  The 3x3 year-to-year transition matrix is
counted from that record with Laplace (add-0.5) smoothing, and each simulation
starts from the most recent observed phase.  The chain is stationary by
construction: it contains no ENSO response to greenhouse forcing, which is a
significant omission (see CAVEATS).

Sources
-------
* Page, S.E. et al. (2002). The amount of carbon released from peat and forest
  fires in Indonesia during 1997. *Nature* 420, 61-65.
* Ballhorn, U. et al. (2009). Derivation of burn scar depths and estimation of
  carbon emissions with LIDAR in Indonesian peatlands. *PNAS* 106(50),
  21213-21218 -- mean burn scar depth 0.33 +/- 0.18 m.
* Stockwell, C.E. et al. (2016). Field measurements of trace gases and aerosols
  emitted by peat fires in Central Kalimantan, Indonesia, during the 2015 El
  Nino. *Atmos. Chem. Phys.* 16, 11711-11732 -- PM2.5 EF 17.3 +/- 6.0 g/kg.
* Akagi, S.K. et al. (2011). Emission factors for open and domestic biomass
  burning for use in atmospheric models. *ACP* 11, 4039-4072.
* Page, S.E. et al. (2011). Global and regional importance of the tropical
  peatland carbon pool. *Global Change Biology* 17, 798-818 -- peat depths.
* Turetsky, M.R. et al. (2015). Global vulnerability of peatlands to fire and
  carbon loss. *Nature Geoscience* 8, 11-14.
* Miettinen, J., Shi, C. & Liew, S.C. (2016). Land cover distribution in the
  peatlands of Peninsular Malaysia, Sumatra and Borneo in 2015.
  *Global Ecology and Conservation* 6, 67-78.
"""

from __future__ import annotations

import math

__all__ = ["simulate_exhaustion", "enso_transition_matrix", "enso_multipliers",
           "CAVEATS", "THETA_DEPLETION", "BURN_DEPTH_M", "BURNABLE_DEPTH_M"]

# --- model parameters (estimated, not measured) ----------------------------
THETA_DEPLETION = 0.6       # fuel-depletion exponent on burned area
PHI_CONVERSION = 0.5        # density-dependence exponent on deforestation
BURN_DEPTH_M = 0.33         # Ballhorn et al. 2009 mean burn scar depth
#: Depth of peat that is actually *available* to burn: the column above the
#: dry-season water table. Peat below the water table does not ignite, however
#: deep the dome is. Drained peat has a deeper drawdown than intact peat, so it
#: holds more burn cycles. Estimated, not measured.
BURNABLE_DEPTH_M = {"peat_intact": 0.5, "peat_drained": 1.0}
DEFAULT_BURNABLE_DEPTH_M = 0.7
#: Hard cap on repeat burns of the same hectare. Observed repeat-burn counts in
#: the ex-Mega Rice Project blocks of Central Kalimantan over 1997-2019 are of
#: order 2-4; beyond that the surface has subsided to the regional water table
#: or to mineral substrate and stops carrying fire.
MAX_REBURN_CYCLES = 4.0
PEAT_DRAINAGE_COUPLING = 0.25   # intact peat drained per unit deforestation
EXHAUSTION_FRACTION = 0.05      # contract: below 5% of baseline = exhausted
PHASES = ("la_nina", "neutral", "el_nino")
FALLBACK_MULTIPLIERS = {"la_nina": 0.30, "neutral": 1.0, "el_nino": 4.0}


def _fuel_stock():
    from app.data.loader import load_fuel_stock
    fs = load_fuel_stock()
    if not fs or not fs.get("pools"):
        raise RuntimeError("load_fuel_stock() returned no pools")
    return fs


def _classify(oni: float) -> str:
    if oni <= -0.5:
        return "la_nina"
    if oni >= 0.5:
        return "el_nino"
    return "neutral"


def _row_phase(row: dict) -> str:
    from app.model.meteo import enso_phase_of
    return enso_phase_of(row)


def enso_transition_matrix(enso=None):
    """3x3 year-to-year ENSO transition matrix with add-0.5 smoothing."""
    import numpy as np
    if enso is None:
        try:
            from app.data.loader import load_enso
            enso = load_enso()
        except Exception:
            enso = []
    rows = sorted([r for r in (enso or []) if isinstance(r, dict)],
                  key=lambda r: r.get("year", 0))
    counts = np.full((3, 3), 0.5)
    prev = None
    n_trans = 0
    for r in rows:
        ph = _row_phase(r)
        if prev is not None:
            counts[PHASES.index(prev), PHASES.index(ph)] += 1.0
            n_trans += 1
        prev = ph
    P = counts / counts.sum(axis=1, keepdims=True)
    last = prev or "neutral"
    return P, last, n_trans


def enso_multipliers(seasons=None):
    """Burned-area multiplier by ENSO phase, estimated from observed seasons."""
    import numpy as np
    if seasons is None:
        try:
            from app.data.loader import load_seasons
            seasons = load_seasons()
        except Exception:
            seasons = []
    by = {p: [] for p in PHASES}
    for s in seasons or []:
        a = s.get("burned_area_mha_kalimantan")
        if a is None:
            continue
        ph = _row_phase(s)
        if ph not in by:
            ph = "neutral"
        by[ph].append(float(a))
    neutral = float(np.mean(by["neutral"])) if by["neutral"] else None
    if not neutral or neutral <= 0:
        return dict(FALLBACK_MULTIPLIERS), None, True
    mult, estimated = {}, False
    for p in PHASES:
        if by[p]:
            mult[p] = float(np.mean(by[p])) / neutral
        else:
            mult[p] = FALLBACK_MULTIPLIERS[p]
            estimated = True
    return mult, neutral, estimated


def _pool_geometry(fs):
    """Per-pool baseline stock, susceptibility weights, depths and EFs."""
    pools = []
    forest_total = 0.0
    for p in fs["pools"]:
        pid = str(p.get("id", ""))
        area = float(p.get("area_mha") or 0.0)
        is_peat = "peat" in pid
        is_forest = "forest" in pid
        cycles = 1.0
        depth = None
        if is_peat:
            depth = (p.get("burnable_depth_m") or p.get("water_table_depth_m")
                     or BURNABLE_DEPTH_M.get(pid, DEFAULT_BURNABLE_DEPTH_M))
            cycles = min(max(1.0, float(depth) / BURN_DEPTH_M), MAX_REBURN_CYCLES)
        if is_forest:
            forest_total += area
        pools.append({
            "id": pid,
            "label": p.get("label", pid),
            "area_mha": area,
            "susceptibility": float(p.get("burn_susceptibility") or 0.0),
            "fuel_load_t_ha": float(p.get("fuel_load_t_ha") or 0.0),
            "pm25_ef_g_kg": float(p.get("pm25_ef_g_kg") or 0.0),
            "is_peat": is_peat,
            "is_forest": is_forest,
            "is_scrub": not (is_peat or is_forest),
            "burnable_depth_m": (float(depth) if depth is not None else None),
            "burn_cycles": cycles,
            "stock0": area * cycles,     # cycle-ha equivalents for peat
        })
    return pools, forest_total


def simulate_exhaustion(n_sims: int = 2000, horizon_years: int = 200,
                        restoration_mha_per_yr: float | None = None,
                        burn_reduction_pct: float = 0.0,
                        deforestation_mha_per_yr: float | None = None,
                        seed: int | None = None) -> dict:
    """Monte-Carlo the year Kalimantan runs out of peat and forest to burn.

    See the module docstring for the stock-and-flow equations, the ENSO Markov
    chain and the treatment of peat as an irreversible stock.  Deterministic
    for a given ``seed``.
    """
    import numpy as np

    fs = _fuel_stock()
    n_sims = int(max(1, min(int(n_sims), 50000)))
    horizon_years = int(max(1, min(int(horizon_years), 400)))
    burn_reduction_pct = float(min(max(burn_reduction_pct, 0.0), 100.0))
    rng = np.random.default_rng(seed)

    base_year = int(str(fs.get("as_of", "2025-12-31"))[:4])
    years = [base_year + 1 + t for t in range(horizon_years)]

    if deforestation_mha_per_yr is None:
        deforestation_mha_per_yr = float(fs.get("deforestation_mha_per_yr") or 0.0)
    if restoration_mha_per_yr is None:
        restoration_mha_per_yr = float(fs.get("restoration_mha_per_yr") or 0.0)
    deforestation_mha_per_yr = float(max(0.0, deforestation_mha_per_yr))
    restoration_mha_per_yr = float(max(0.0, restoration_mha_per_yr))

    pools, forest_total0 = _pool_geometry(fs)
    npool = len(pools)
    idx = {p["id"]: i for i, p in enumerate(pools)}

    stock0 = np.array([p["stock0"] for p in pools])
    cycles = np.array([p["burn_cycles"] for p in pools])
    is_peat = np.array([p["is_peat"] for p in pools])
    is_forest = np.array([p["is_forest"] for p in pools])
    is_scrub = np.array([p["is_scrub"] for p in pools])
    fuel = np.array([p["fuel_load_t_ha"] for p in pools])
    ef = np.array([p["pm25_ef_g_kg"] for p in pools])

    # area-weighted, normalised susceptibility so sum_p burned_0 = A_base*m
    raw = np.array([p["susceptibility"] for p in pools]) * np.array([p["area_mha"] for p in pools])
    s_eff = raw / raw.sum() if raw.sum() > 0 else np.full(npool, 1.0 / npool)

    mult_map, a_base_obs, mult_estimated = enso_multipliers()
    A_base = float(a_base_obs) if a_base_obs else 0.20
    mult_vec = np.array([mult_map[p] for p in PHASES])
    P, last_phase, n_trans = enso_transition_matrix()
    cum = P.cumsum(axis=1)

    scrub_i = int(np.argmax(is_scrub)) if is_scrub.any() else None
    intact_i = idx.get("peat_intact")
    drained_i = idx.get("peat_drained")

    burnable_mask = is_peat | is_forest
    baseline_burnable = float((stock0[burnable_mask] / cycles[burnable_mask]).sum())

    S = np.tile(stock0, (n_sims, 1)).astype(float)
    state = np.full(n_sims, PHASES.index(last_phase), dtype=np.int8)
    exhaust_year = np.full(n_sims, -1, dtype=np.int32)
    cum_burned = np.zeros(n_sims)
    cum_pm25_kg = np.zeros(n_sims)
    burned_by_2050 = np.zeros(n_sims)
    traj = np.zeros((horizon_years, n_sims))
    per_pool_traj = np.zeros((horizon_years, n_sims, npool))

    burn_scale = 1.0 - burn_reduction_pct / 100.0
    stock0_safe = np.where(stock0 > 0, stock0, 1.0)

    for t, yr in enumerate(years):
        # --- ENSO transition ---------------------------------------------
        u = rng.random(n_sims)
        nxt = np.zeros(n_sims, dtype=np.int8)
        for k in range(3):
            sel = state == k
            if sel.any():
                nxt[sel] = np.searchsorted(cum[k], u[sel])
        state = np.clip(nxt, 0, 2)
        m = mult_vec[state]
        # interannual noise on the base burned area (lognormal, sigma 0.45)
        m = m * rng.lognormal(-0.5 * 0.45 ** 2, 0.45, n_sims)

        frac = np.clip(S / stock0_safe, 0.0, 1.0)
        burned = (A_base * m[:, None] * s_eff[None, :]
                  * np.power(frac, THETA_DEPLETION) * burn_scale)
        burned = np.minimum(burned, S)

        # emissions: cycle-ha == ha burned for peat (one cycle per fire)
        cum_pm25_kg += (burned * 1e6 * fuel * ef).sum(axis=1)
        burned_area = burned.sum(axis=1)
        cum_burned += burned_area
        if yr <= 2050:
            burned_by_2050 += burned_area

        S = S - burned
        # burned forest degrades into the scrub pool; burned peat is gone.
        if scrub_i is not None:
            S[:, scrub_i] += burned[:, is_forest].sum(axis=1)

        # --- deforestation: density-dependent conversion of forest -> scrub
        if deforestation_mha_per_yr > 0 and is_forest.any() and forest_total0 > 0:
            f_now = S[:, is_forest]
            f_tot = f_now.sum(axis=1, keepdims=True)
            rate = deforestation_mha_per_yr * np.power(
                np.clip(f_tot / max(forest_total0, 1e-9), 0.0, 1.0), PHI_CONVERSION)
            w = f_now / np.maximum(f_tot, 1e-12)
            conv = np.minimum(rate * w, f_now)
            S[:, is_forest] = f_now - conv
            if scrub_i is not None:
                S[:, scrub_i] += conv.sum(axis=1)
            # canals follow the loggers: some intact peat becomes drained peat
            if intact_i is not None and drained_i is not None:
                drain = np.minimum(conv.sum(axis=1) * PEAT_DRAINAGE_COUPLING,
                                   S[:, intact_i])
                S[:, intact_i] -= drain
                S[:, drained_i] += drain * (cycles[drained_i] / max(cycles[intact_i], 1e-9))

        # --- restoration: rewetting moves drained peat back to intact ------
        if restoration_mha_per_yr > 0 and intact_i is not None and drained_i is not None:
            d_now = S[:, drained_i]
            share = d_now / max(stock0[drained_i], 1e-9)
            rest = np.minimum(restoration_mha_per_yr * cycles[drained_i] * share, d_now)
            S[:, drained_i] = d_now - rest
            S[:, intact_i] += rest * (cycles[intact_i] / max(cycles[drained_i], 1e-9))

        S = np.maximum(S, 0.0)
        per_pool_traj[t] = S / cycles
        burnable = (S[:, burnable_mask] / cycles[burnable_mask]).sum(axis=1)
        traj[t] = burnable
        hit = (exhaust_year < 0) & (burnable < EXHAUSTION_FRACTION * baseline_burnable)
        exhaust_year[hit] = yr

    ever = exhaust_year > 0
    never_pct = round(100.0 * float((~ever).mean()), 2)
    if ever.any():
        q = np.percentile(exhaust_year[ever], [10, 50, 90])
        p10, p50, p90 = (int(round(float(x))) for x in q)
        p10, p50, p90 = sorted((p10, p50, p90))
    else:
        p10 = p50 = p90 = None

    tp10, tp50, tp90 = (np.percentile(traj, pct, axis=1) for pct in (10, 50, 90))
    tp10, tp90 = np.minimum(tp10, tp50), np.maximum(tp90, tp50)

    per_pool = []
    yr_2050 = 2050 - (base_year + 1)
    for i, p in enumerate(pools):
        base_i = p["area_mha"]
        ex = np.full(n_sims, -1, dtype=np.int32)
        if base_i > 0 and not p["is_scrub"]:
            below = per_pool_traj[:, :, i] < EXHAUSTION_FRACTION * base_i
            for t in range(horizon_years):
                hit = (ex < 0) & below[t]
                ex[hit] = years[t]
        ever_i = ex > 0
        rem_2050 = (float(np.percentile(per_pool_traj[yr_2050, :, i], 50))
                    if 0 <= yr_2050 < horizon_years else None)
        per_pool.append({
            "id": p["id"],
            "label": p["label"],
            "baseline_mha": round(base_i, 4),
            "exhaustion_year_p50": (int(round(float(np.percentile(ex[ever_i], 50))))
                                    if ever_i.any() else None),
            "never_pct": round(100.0 * float((~ever_i).mean()), 2),
            "remaining_mha_2050": (round(rem_2050, 4) if rem_2050 is not None else None),
            "burn_cycles": round(float(p["burn_cycles"]), 2),
            "burnable_depth_m": p["burnable_depth_m"],
            "estimated": True,
        })

    return {
        "exhaustion_year": {"p10": p10, "p50": p50, "p90": p90,
                            "never_pct": never_pct},
        "trajectories": {
            "years": years,
            "p10": [round(float(x), 4) for x in tp10],
            "p50": [round(float(x), 4) for x in tp50],
            "p90": [round(float(x), 4) for x in tp90],
            "unit": "Mha of burnable peat + forest",
            "baseline_mha": round(baseline_burnable, 4),
        },
        "per_pool": per_pool,
        "cumulative_burned_mha_by_2050": round(float(np.percentile(burned_by_2050, 50)), 3),
        "cumulative_pm25_kt": round(float(np.percentile(cum_pm25_kg, 50)) / 1e6, 1),
        "assumptions": {
            "n_sims": n_sims,
            "horizon_years": horizon_years,
            "base_year": base_year,
            "restoration_mha_per_yr": restoration_mha_per_yr,
            "burn_reduction_pct": burn_reduction_pct,
            "deforestation_mha_per_yr": deforestation_mha_per_yr,
            "seed": seed,
            "A_base_mha_per_yr": round(A_base, 4),
            "A_base_source": ("mean observed Kalimantan burned area in "
                              "ENSO-neutral seasons" if a_base_obs
                              else "fallback default"),
            "enso_multipliers": {k: round(float(v), 3) for k, v in mult_map.items()},
            "enso_multipliers_estimated": bool(mult_estimated),
            "enso_transition_matrix": {
                PHASES[i]: {PHASES[j]: round(float(P[i, j]), 4) for j in range(3)}
                for i in range(3)},
            "enso_transitions_observed": int(n_trans),
            "enso_initial_phase": last_phase,
            "theta_depletion": THETA_DEPLETION,
            "phi_conversion": PHI_CONVERSION,
            "burn_depth_m_per_fire": BURN_DEPTH_M,
            "burnable_depth_m": dict(BURNABLE_DEPTH_M),
            "max_reburn_cycles": MAX_REBURN_CYCLES,
            "peat_drainage_coupling": PEAT_DRAINAGE_COUPLING,
            "exhaustion_threshold_fraction": EXHAUSTION_FRACTION,
            "peat_is_irreversible": True,
            "estimated": True,
        },
        "caveats": list(CAVEATS),
    }


CAVEATS = [
    "Peat that burns does not grow back on a human timescale. Tropical peat "
    "accumulates at roughly 0.5-2 mm a year; a single fire removes about "
    "330 mm. Every hectare-metre this model burns is gone for the next two to "
    "six centuries at best, and the model treats it that way.",
    "The exhaustion year is not a prediction of the future so much as the "
    "consequence of assuming the last thirty years continue. It assumes "
    "Indonesian land policy, palm oil economics, fire-suppression capacity and "
    "the ENSO cycle all stay roughly as they have been. None of those will.",
    "The ENSO Markov chain is stationary and fitted to about seventy years of "
    "ONI. Climate models disagree about whether El Nino becomes more frequent "
    "or more intense under warming, and this model takes no position, so the "
    "spread shown is almost certainly too narrow on the pessimistic side.",
    "Burned forest is moved into the scrub pool and keeps burning; burned peat "
    "leaves the system. So 'exhaustion' here means the end of peat and forest "
    "fuel, not the end of fire. Grass and scrub fires on drained, subsided land "
    "would continue indefinitely, with far less smoke but not none.",
    "How many times the same hectare of peat can burn before it stops carrying "
    "fire is the single most consequential assumption in this model, and it is "
    "an estimate: about 1.5 burns for intact peat and 3 for drained peat, from "
    "the depth of peat above the dry-season water table. Halve it and the "
    "exhaustion year moves decades earlier; double it and it moves decades "
    "later.",
    "How much peat Kalimantan has is genuinely disputed. This model uses the "
    "KLHK figure of 4.5 Mha; the BBSDLP 2011 mapping gives 6.0 Mha. That is a "
    "33% difference in the stock that dominates the answer, and it moves the "
    "exhaustion year later by roughly the same proportion - on the order of "
    "several decades - if the larger figure is right. The drained fraction is a "
    "policy-sensitive number on top of that.",
    "The base annual burned area is anchored on the observed Kalimantan record, "
    "and that record is a lower bound. The 2015 Kalimantan figure of 0.78 Mha "
    "comes from provincial recaps that exist for only two of the five "
    "provinces; the true figure is higher. A burn rate calibrated on lower "
    "bounds burns the stock too slowly, so the exhaustion year here is "
    "optimistic - probably by more than the Monte-Carlo interval suggests.",
    "The five pools deliberately do not tile Kalimantan. They cover about "
    "29.3 Mha of peat and forest out of 54.4 Mha of land; the rest is "
    "plantation, cropland, settlement and water, which burn on their own "
    "schedule and are outside this model. 'Nothing left to burn' means no peat "
    "or forest left, not a landscape that has stopped producing smoke.",
    "Burn susceptibility per pool is a model parameter, not a measurement. It "
    "is anchored so that drained peat is about 1.0, and everything else is "
    "relative to that on the basis of hotspot density by land cover.",
    "Fire and drainage interact in ways this model only crudely represents: "
    "burning lowers the peat surface, which lowers the water table relative to "
    "the surroundings, which makes the next fire easier. That feedback is "
    "represented only through the fixed drainage coupling term.",
    "Restoration is modelled as rewetting that moves drained peat back to the "
    "intact pool. Real restoration success rates are contested, canal dams "
    "fail, and rewetted peat can still burn in an extreme drought year. "
    "Treating restored hectares as fully intact is optimistic.",
    "Emissions use a single PM2.5 emission factor per pool. Measured peat "
    "PM2.5 emission factors span roughly 6 to 30 g/kg and vary with moisture "
    "and smouldering stage, so cumulative PM2.5 here is good to about a factor "
    "of two, no better.",
]
