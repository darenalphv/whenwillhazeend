"""Fuel-exhaustion model, v2: when does Kalimantan run out of peat, and of forest?

MODEL_SPEC section 4.4 (F1-F4), with burned area from section 3.4 (B1-B3) and
emissions from section 1.4 (E1). Monte-Carlo over ``n_sims`` futures driven by a
three-state ENSO Markov chain with a conditional Indian Ocean Dipole draw.

What changed from v1, and why it moves the answer
-------------------------------------------------
v1 carried peat as "burn cycles" (0.5-1.0 m of burnable depth / 0.33 m per fire) and
had no non-fire peat loss, so a hectare could only ever "run out" by burning 1.5-3
times. That made "never within 200 years" an artefact of the criterion (spec 4.3).
v2 carries peat as a **thickness column** that loses depth both to fire and to
**drainage subsidence**, which on drained peat removes more peat than fire does.

F1: peat column
---------------
Kalimantan peat, MoA/BBSDLP 2011 (Ritung et al.; via Warren et al. 2017): 4.78 Mha,
depth classes 0.5-1 / 1-2 / 2-3 / >3 m = 22 / 29 / 22 / 27 %. Within a class the
thickness is taken as uniform over the class range; the >3 m class is 3-7 m, mean
5.0 m [J, UNVERIFIED; Hooijer & Vernimmen 2013 give a 5.4 m Kalimantan mean]. State
per simulation, per depth class k and status s (intact / drained):

    A[k,s]  nominal area (Mha)          L[k,s]  mean surface lowering so far (m)
    alive area = A * clip((z_max - L) / (z_max - z_min), 0, 1)

Each year, on drained peat::

    L += s_d                                  s_d = 0.043 m/yr (Evans 2019 Acacia;
                                              range 0.034 forest - 0.05 Hooijer 2012)
    L += p_burn * d1 * E[r_n]                 the fire part, averaged over the class
                                              d1 = 0.26 m (0.17-0.37), r_n Konecny 2016

Intact peat does not subside (s = 0) and burns less (susceptibility 0.35, a model
parameter). Newly drained peat (canals following deforestation) gets the Hooijer 2012
first-five-years consolidation, 1.42 m of surface lowering, applied on drainage [J:
not spread over the five years], of which only 25% is counted as mass loss.
Oxidation is 92% of long-term subsidence (Hooijer 2012), and emits CO2, not PM2.5.
Only the fire part emits PM2.5. The drainage base is NOT modelled: real drained peat
may stop subsiding when it reaches the drainage limit and floods.

F2: burn probability
--------------------
p_burn = p0 * m(ENSO, IOD) * s_status * (1 - burn_reduction). p0 is the long-run
mean Kalimantan peat burned fraction, drawn per simulation log-uniformly between
3.6%/yr (Vetrita & Cochrane 2020, MODIS: a lower bound) and the GFED5-consistent
value (build_burned_area.py; about 6.5%/yr) [D]. m is fitted to the B2 series
(log-linear in El Nino, La Nina, positive IOD), normalised to mean 1. Burned intact
peat joins the drained pool [J]: fire kills the swamp forest and the next fire finds
a degraded surface. The fire-count distribution per status is carried so that
repeat fires take 0.56 / 0.33 / 0.11 of the first-fire depth.

F3: deforestation 0.21 Mha/yr (Gaveau et al. 2022, 2001-2019 mean) by default;
0.10 is the low end ("recent years", UNVERIFIED).

Non-peat burning (forest, scrub) keeps the v1 stock-and-flow, but its base rate is
now the B2 satellite-consistent Kalimantan non-peat burned area (drawn per simulation
between the MCD64A1-equivalent and the GFED5-equivalent level) instead of
KLHK x 0.29.

F4: headlines
-------------
``headlines.peat``  : drained (burnable) peat with thickness left. Exhaustion = below
                      5% of today's drained area; also the 50% year; cumulative peat
                      carbon lost to fire vs to drainage.
``headlines.forest``: primary + secondary forest, below 5% of baseline.
The v1 aggregate ``exhaustion_year`` (peat + forest below 5%) is kept for
compatibility, and is now a secondary metric.
"""

from __future__ import annotations

import functools
import math

__all__ = ["simulate_exhaustion", "enso_transition_matrix", "fit_multipliers",
           "peat_years_to_zero", "CAVEATS", "THETA_DEPLETION"]

THETA_DEPLETION = 0.6          # v1 fuel-depletion exponent (non-peat pools), UNVERIFIED
PHI_CONVERSION = 0.5           # v1 density dependence of deforestation, UNVERIFIED
PEAT_DRAINAGE_COUPLING = 0.25  # v1: intact peat drained per unit deforestation, UNVERIFIED
INTACT_SUSCEPTIBILITY = 0.35   # v1 model parameter (relative to drained = 1)
BURNED_INTACT_BECOMES_DRAINED = True     # [J]
CONSOLIDATION_M = 1.42         # Hooijer 2012 first 5 years
CONSOLIDATION_MASS_SHARE = 0.25          # MODEL_SPEC F1 reading [J]
OXIDATION_SHARE = 0.92         # Hooijer 2012, after 18 years
EXHAUSTION_FRACTION = 0.05
PHASES = ("la_nina", "neutral", "el_nino")
NOISE_SIGMA = 0.45             # v1 interannual lognormal noise on burned area [J]


def _P(pid, default, field="value"):
    try:
        from app.data.loader import load_earth_parameters
        v = (load_earth_parameters().get("parameters", {}).get(pid) or {}).get(field)
    except Exception:
        v = None
    return float(v) if v is not None else float(default)


def peat_years_to_zero(z_m: float, subsidence_m_yr: float | None = None) -> float:
    """Years for a drained column of thickness z to reach zero with no fire (spec F4).

    Spec test: 0.75 m / 0.043 m/yr = 17.4 yr.
    """
    s = subsidence_m_yr if subsidence_m_yr is not None else _P("subsidence_rate_drained", 0.043)
    return float(z_m) / float(s)


# --------------------------------------------------------------------------- #
# climate drivers
# --------------------------------------------------------------------------- #

def _phase(row) -> str:
    oni = row.get("oni_burn_window")
    if oni is None:
        oni = row.get("oni_jas")
    if oni is None:
        ph = row.get("phase")
        return ph if ph in PHASES else "neutral"
    oni = float(oni)
    return "el_nino" if oni >= 0.5 else ("la_nina" if oni <= -0.5 else "neutral")


@functools.lru_cache(maxsize=1)
def _drivers():
    """{year: (enso_phase, iod_positive)} from the bundled ONI and DMI records."""
    from app.data.loader import load_enso
    try:
        from app.data.loader import _read
        iod = (_read("iod.json") or {}).get("seasons", {})
    except Exception:
        iod = {}
    out = {}
    for r in sorted(load_enso() or [], key=lambda r: r.get("year", 0)):
        y = int(r["year"])
        ip = (iod.get(str(y)) or {}).get("iod_phase")
        out[y] = (_phase(r), ip == "positive" if ip else None)
    return out


def enso_transition_matrix(enso=None):
    """3x3 year-to-year ENSO transition matrix (add-0.5 smoothing), burn-window ONI."""
    import numpy as np
    if enso is None:
        drv = _drivers()
        phases = [drv[y][0] for y in sorted(drv)]
    else:
        phases = [_phase(r) for r in sorted(enso, key=lambda r: r.get("year", 0))]
    counts = np.full((3, 3), 0.5)
    for a, b in zip(phases, phases[1:]):
        counts[PHASES.index(a), PHASES.index(b)] += 1.0
    P = counts / counts.sum(axis=1, keepdims=True)
    return P, (phases[-1] if phases else "neutral"), max(0, len(phases) - 1)


@functools.lru_cache(maxsize=1)
def fit_multipliers():
    """Burned-area multipliers m(ENSO, IOD), fitted to the B2 series.

    log BA_y = c0 + c_EN [El Nino] + c_LN [La Nina] + c_IOD [positive IOD], 1997-2023,
    separately for peat-cell and non-peat burned area. Returned unnormalised, with
    P(positive IOD | ENSO phase) from the overlapping DMI/ONI record.
    """
    import numpy as np
    from app.data.loader import load_burned_area
    ba = load_burned_area()
    ser = (ba.get("series") or {}).get("kalimantan") or {}
    drv = _drivers()
    rows = []
    for y, v in ser.items():
        y = int(y)
        if y not in drv or drv[y][1] is None:
            continue
        ph, iod = drv[y]
        peat = max(float(v["peat_cell_mha_gfed4s"]), 1e-3)
        non = max(float(v["gfed4s_mha"]) - float(v["peat_cell_mha_gfed4s"]), 1e-3)
        rows.append((ph == "el_nino", ph == "la_nina", bool(iod), math.log(peat), math.log(non)))
    out = {}
    if len(rows) >= 8:
        X = np.array([[1.0, a, b, c] for a, b, c, _, _ in rows])
        for name, col in (("peat", 3), ("nonpeat", 4)):
            yv = np.array([r[col] for r in rows])
            coef, *_ = np.linalg.lstsq(X, yv, rcond=None)
            resid = yv - X @ coef
            out[name] = {"c_el_nino": float(coef[1]), "c_la_nina": float(coef[2]),
                         "c_iod_pos": float(coef[3]),
                         "resid_sd": float(resid.std(ddof=4)), "n_years": len(rows)}
    else:   # no series: v1 fallback multipliers (el nino x4, la nina x0.3)
        for name in ("peat", "nonpeat"):
            out[name] = {"c_el_nino": math.log(4.0), "c_la_nina": math.log(0.3),
                         "c_iod_pos": 0.0, "resid_sd": NOISE_SIGMA, "n_years": 0,
                         "fallback": True}
    # P(positive IOD | phase), add-0.5 smoothing
    cnt = {p: [0.5, 1.0] for p in PHASES}
    for y, (ph, iod) in drv.items():
        if iod is None:
            continue
        cnt[ph][0] += float(iod)
        cnt[ph][1] += 1.0
    out["p_iod_given_phase"] = {p: cnt[p][0] / cnt[p][1] for p in PHASES}
    return out


def _stationary(P):
    import numpy as np
    w, v = np.linalg.eig(P.T)
    i = int(np.argmin(np.abs(w - 1.0)))
    pi = np.real(v[:, i])
    return pi / pi.sum()


# --------------------------------------------------------------------------- #
# the simulation
# --------------------------------------------------------------------------- #

def _fuel_stock():
    from app.data.loader import load_fuel_stock
    fs = load_fuel_stock()
    if not fs or not fs.get("pools"):
        raise RuntimeError("load_fuel_stock() returned no pools")
    return fs


def _quantile_years(ex):
    import numpy as np
    ever = ex > 0
    never_pct = round(100.0 * float((~ever).mean()), 2)
    if ever.any() and ever.mean() >= 0.5:
        # percentiles over ALL simulations, with "never" treated as +infinity, so a
        # p90 is not reported when more than 10% never get there
        vals = np.where(ever, ex, np.iinfo(np.int32).max)
        q = np.percentile(vals, [10, 50, 90], method="lower")
        out = [int(x) if x < np.iinfo(np.int32).max else None for x in q]
    elif ever.any():
        vals = np.where(ever, ex, np.iinfo(np.int32).max)
        q = np.percentile(vals, [10, 50, 90], method="lower")
        out = [int(x) if x < np.iinfo(np.int32).max else None for x in q]
    else:
        out = [None, None, None]
    return {"p10": out[0], "p50": out[1], "p90": out[2], "never_pct": never_pct}


def simulate_exhaustion(n_sims: int = 2000, horizon_years: int = 200,
                        restoration_mha_per_yr: float | None = None,
                        burn_reduction_pct: float = 0.0,
                        deforestation_mha_per_yr: float | None = None,
                        seed: int | None = None) -> dict:
    """Monte-Carlo the years Kalimantan's drained peat and its forest run out.

    See the module docstring. Deterministic for a given ``seed``.
    """
    import numpy as np
    from app.data.loader import load_burned_area

    fs = _fuel_stock()
    n_sims = int(max(1, min(int(n_sims), 50000)))
    horizon_years = int(max(1, min(int(horizon_years), 400)))
    burn_reduction_pct = float(min(max(burn_reduction_pct, 0.0), 100.0))
    burn_scale = 1.0 - burn_reduction_pct / 100.0
    rng = np.random.default_rng(seed)
    base_year = int(str(fs.get("as_of", "2025-12-31"))[:4])
    years = [base_year + 1 + t for t in range(horizon_years)]

    col = fs.get("peat_column") or {}
    if deforestation_mha_per_yr is None:
        deforestation_mha_per_yr = float(fs.get("deforestation_mha_per_yr") or 0.0)
    if restoration_mha_per_yr is None:
        restoration_mha_per_yr = float(fs.get("restoration_mha_per_yr") or 0.0)
    defo = float(max(0.0, deforestation_mha_per_yr))
    resto = float(max(0.0, restoration_mha_per_yr))

    # ---- peat column (F1) -------------------------------------------------
    peat_area = float(col.get("area_mha") or _P("kalimantan_peat_area_moa2011", 4.78))
    drained_frac = float(col.get("drained_fraction") or _P("drained_peat_fraction_current", 0.6))
    classes = col.get("depth_classes") or [
        {"share": 0.22, "z_min": 0.5, "z_max": 1.0},
        {"share": 0.29, "z_min": 1.0, "z_max": 2.0},
        {"share": 0.22, "z_min": 2.0, "z_max": 3.0},
        {"share": 0.27, "z_min": 3.0, "z_max": 7.0},
    ]
    zmin = np.array([c["z_min"] for c in classes], float)
    zmax = np.array([c["z_max"] for c in classes], float)
    share = np.array([c["share"] for c in classes], float)
    share = share / share.sum()
    K = len(classes)
    A = np.zeros((n_sims, K, 2))
    A[:, :, 0] = peat_area * (1 - drained_frac) * share
    A[:, :, 1] = peat_area * drained_frac * share
    L = np.zeros((n_sims, K, 2))
    drained0 = float(A[0, :, 1].sum())

    # fire-count distribution per status: F[:, s, n], n = fires so far (0,1,2,>=3)
    r_n = np.array([1.0, 0.56, 0.33, 0.11])
    hist = col.get("fire_history") or {"affected_fraction_of_peat": 0.286,
                                       "once": 0.135, "twice": 0.128, "more": 0.36}
    F = np.zeros((n_sims, 2, 4))
    F[:, 0, 0] = 1.0
    aff = min(1.0, hist["affected_fraction_of_peat"] * peat_area / max(drained0, 1e-9))
    w = np.array([hist["once"], hist["twice"], hist["more"]])
    w = w / w.sum()
    F[:, 1, 0] = 1.0 - aff
    F[:, 1, 1:] = aff * w

    def alive(Aa, La):
        g = np.clip((zmax[None, :, None] - La) / (zmax - zmin)[None, :, None], 0.0, 1.0)
        return Aa * g

    # ---- per-simulation parameter draws (parameter uncertainty in the MC) ----
    ba = load_burned_area() or {}
    br = ba.get("base_rates") or {}
    pb = br.get("peat_burned_fraction_per_yr") or {"low": 0.036, "high": 0.036}
    npb = br.get("nonpeat_mha_per_yr") or {"low": 0.198, "high": 0.198, "central": 0.198}
    p0 = np.exp(rng.uniform(math.log(pb["low"]), math.log(max(pb["high"], pb["low"])), n_sims))
    a_non = np.exp(rng.uniform(math.log(npb["low"]), math.log(max(npb["high"], npb["low"])),
                               n_sims))
    s_d = rng.uniform(_P("subsidence_rate_drained", 0.034, "low"),
                      _P("subsidence_rate_drained", 0.05, "high"), n_sims)
    d1 = rng.uniform(_P("peat_burn_depth_first_fire_central", 0.17, "low"),
                     _P("peat_burn_depth_first_fire_central", 0.37, "high"), n_sims)
    rho_t_per_ha_m = _P("peat_bulk_density", 0.11) * 1.0e4          # t DM per ha per m
    ef_peat = _P("ef_pm25_peat_central", 22.3)
    c_frac = _P("peat_carbon_fraction", 0.513)

    # susceptibility normalised so the area-weighted mean over today's peat = 1
    s_norm = 1.0 / (drained_frac + (1 - drained_frac) * INTACT_SUSCEPTIBILITY)
    s_status = np.array([INTACT_SUSCEPTIBILITY, 1.0]) * s_norm

    # ---- climate chain (F2) -----------------------------------------------
    fit = fit_multipliers()
    P, last_phase, n_trans = enso_transition_matrix()
    cum = P.cumsum(axis=1)
    pi = _stationary(P)
    p_iod = np.array([fit["p_iod_given_phase"][p] for p in PHASES])

    def mult_table(f):
        c = np.array([f["c_la_nina"], 0.0, f["c_el_nino"]])
        tab = np.stack([np.exp(c), np.exp(c + f["c_iod_pos"])], axis=1)   # (3, 2)
        mean = float((pi[:, None] * np.stack([1 - p_iod, p_iod], axis=1) * tab).sum())
        return tab / mean
    m_peat_tab = mult_table(fit["peat"])
    m_non_tab = mult_table(fit["nonpeat"])

    # ---- non-peat pools (v1 stock-and-flow with B2 base rate) ----------------
    pools = [p for p in fs["pools"] if not str(p["id"]).startswith("peat")]
    nid = [str(p["id"]) for p in pools]
    area0 = np.array([float(p.get("area_mha") or 0.0) for p in pools])
    is_forest = np.array(["forest" in i for i in nid])
    scrub_i = nid.index("scrub_grass") if "scrub_grass" in nid else None
    from app.model.emissions import NONPEAT_FUEL_T_HA, pool_emission_factors
    efs = pool_emission_factors()
    fuel = np.array([NONPEAT_FUEL_T_HA.get(i, float(p.get("fuel_load_t_ha") or 0.0))
                     for i, p in zip(nid, pools)])
    ef = np.array([efs.get(i, float(p.get("pm25_ef_g_kg") or 0.0)) for i, p in zip(nid, pools)])
    sus = np.array([float(p.get("burn_susceptibility") or 0.0) for p in pools]) * area0
    s_eff = sus / sus.sum() if sus.sum() > 0 else np.full(len(pools), 1.0 / len(pools))
    S = np.tile(area0, (n_sims, 1))
    forest0 = float(area0[is_forest].sum())
    S0safe = np.where(area0 > 0, area0, 1.0)

    # ---- outputs ----------------------------------------------------------
    state = np.full(n_sims, PHASES.index(last_phase), dtype=np.int64)
    ex_peat = np.full(n_sims, -1, np.int32)
    half_peat = np.full(n_sims, -1, np.int32)
    ex_forest = np.full(n_sims, -1, np.int32)
    ex_agg = np.full(n_sims, -1, np.int32)
    cum_pm = np.zeros(n_sims)
    burned_2050 = np.zeros(n_sims)
    fire_c = np.zeros(n_sims)
    drain_c = np.zeros(n_sims)
    fire_c_2050 = np.zeros(n_sims)
    drain_c_2050 = np.zeros(n_sims)
    # float32 and only the series that need percentiles: the n_sims=20000 x 300 yr
    # ceiling would otherwise hold ~430 MB of trajectories
    agg_traj = np.zeros((horizon_years, n_sims), np.float32)
    drained_traj = np.zeros((horizon_years, n_sims), np.float32)
    forest_traj = np.zeros((horizon_years, n_sims), np.float32)
    peat_all_2050 = np.zeros(n_sims)
    pool_ids = ("peat_intact", "peat_drained", *nid)
    pool_ex = {i: np.full(n_sims, -1, np.int32) for i in pool_ids}
    pool_2050 = {i: np.zeros(n_sims) for i in pool_ids}
    agg0 = peat_area + forest0
    sims = np.arange(n_sims)
    base_pool = {"peat_intact": peat_area * (1 - drained_frac), "peat_drained": drained0}
    base_pool.update({i: float(a) for i, a in zip(nid, area0)})

    for t, yr in enumerate(years):
        # climate
        u = rng.random(n_sims)
        state = np.clip((u[:, None] > cum[state]).sum(axis=1), 0, 2)
        iod = (rng.random(n_sims) < p_iod[state]).astype(np.int64)
        noise_p = rng.lognormal(-0.5 * fit["peat"]["resid_sd"] ** 2 * 0 - 0.5 * NOISE_SIGMA ** 2,
                                NOISE_SIGMA, n_sims)
        noise_n = rng.lognormal(-0.5 * NOISE_SIGMA ** 2, NOISE_SIGMA, n_sims)
        m_p = m_peat_tab[state, iod] * noise_p
        m_n = m_non_tab[state, iod] * noise_n

        # --- peat fire (F2) -----------------------------------------------
        Al = alive(A, L)                                          # (n, K, 2)
        p_burn = np.clip(p0[:, None] * m_p[:, None] * s_status[None, :] * burn_scale,
                         0.0, 0.95)                               # (n, 2)
        burned = Al * p_burn[:, None, :]                          # Mha by class/status
        er = (F * r_n[None, None, :]).sum(axis=2)                 # (n, 2) E[r] per status
        depth = d1[:, None] * er                                  # m per burned ha
        # don't burn deeper than what is left: mean remaining thickness of alive area
        zrem = np.maximum(0.5 * (np.maximum(zmin[None, :, None] - L, 0.0)
                                 + np.maximum(zmax[None, :, None] - L, 0.0)), 0.0)
        dburn = np.minimum(depth[:, None, :], zrem)
        burned_mha = burned.sum(axis=(1, 2))
        mass_t = (burned * dburn).sum(axis=(1, 2)) * 1e6 * rho_t_per_ha_m   # t DM
        cum_pm += mass_t * ef_peat                                # kg PM2.5
        fire_c += mass_t * c_frac / 1e6                            # Mt C
        if yr <= 2050:
            fire_c_2050 += mass_t * c_frac / 1e6
        # lowering from fire, averaged over each class's nominal area
        L += np.where(A > 0, burned * dburn / np.maximum(A, 1e-12), 0.0)
        # update fire counts
        for s in (0, 1):
            pb_s = p_burn[:, s][:, None]
            moved = F[:, s, :] * pb_s
            F[:, s, :] -= moved
            F[:, s, 1:] += moved[:, :-1]
            F[:, s, 3] += moved[:, 3]
        # burned intact peat joins the drained pool [J]
        if BURNED_INTACT_BECOMES_DRAINED:
            frac = np.where(A[:, :, 0] > 0, burned[:, :, 0] / np.maximum(Al[:, :, 0], 1e-12), 0.0)
            frac = np.clip(frac * np.clip(Al[:, :, 0] / np.maximum(A[:, :, 0], 1e-12), 0, 1), 0, 1)
            _move_peat(np, A, L, F, frac, 0, 1, extra_L=0.0)

        # --- subsidence on drained peat (F1) ---------------------------------
        before = alive(A, L)[:, :, 1].sum(axis=1)
        sub_mass = (alive(A, L)[:, :, 1] * np.minimum(s_d[:, None], zrem[:, :, 1])).sum(axis=1)
        L[:, :, 1] += s_d[:, None]
        oxid = sub_mass * 1e6 * rho_t_per_ha_m * OXIDATION_SHARE * c_frac / 1e6   # Mt C
        drain_c += oxid
        if yr <= 2050:
            drain_c_2050 += oxid
        del before

        # --- non-peat burning and deforestation (v1 flow, B2 base rate) -----
        frac_s = np.clip(S / S0safe, 0.0, 1.0)
        burned_n = (a_non[:, None] * m_n[:, None] * s_eff[None, :]
                    * np.power(frac_s, THETA_DEPLETION) * burn_scale)
        burned_n = np.minimum(burned_n, S)
        cum_pm += (burned_n * 1e6 * fuel * ef).sum(axis=1)
        S = S - burned_n
        if scrub_i is not None:
            S[:, scrub_i] += burned_n[:, is_forest].sum(axis=1)
        b_tot = burned_n.sum(axis=1) + burned_mha
        if yr <= 2050:
            burned_2050 += b_tot
        if defo > 0 and forest0 > 0:
            f_now = S[:, is_forest]
            f_tot = f_now.sum(axis=1, keepdims=True)
            rate = defo * np.power(np.clip(f_tot / forest0, 0.0, 1.0), PHI_CONVERSION)
            wgt = f_now / np.maximum(f_tot, 1e-12)
            conv = np.minimum(rate * wgt, f_now)
            S[:, is_forest] = f_now - conv
            if scrub_i is not None:
                S[:, scrub_i] += conv.sum(axis=1)
            # canals follow the loggers: some intact peat becomes drained peat,
            # with the Hooijer 2012 first-5-yr consolidation applied on drainage [J]
            Al = alive(A, L)
            ia = Al[:, :, 0].sum(axis=1)
            drain_mha = np.minimum(conv.sum(axis=1) * PEAT_DRAINAGE_COUPLING, ia)
            frac = np.where(ia[:, None] > 0, (drain_mha / np.maximum(ia, 1e-12))[:, None], 0.0)
            frac = np.broadcast_to(frac, (n_sims, K)).copy()
            cons_mass = (Al[:, :, 0] * frac).sum(axis=1) * CONSOLIDATION_M * CONSOLIDATION_MASS_SHARE
            drain_c += cons_mass * 1e6 * rho_t_per_ha_m * c_frac / 1e6
            if yr <= 2050:
                drain_c_2050 += cons_mass * 1e6 * rho_t_per_ha_m * c_frac / 1e6
            _move_peat(np, A, L, F, frac, 0, 1, extra_L=CONSOLIDATION_M)

        # --- restoration: rewetting moves drained peat back to intact -----------
        if resto > 0:
            Al = alive(A, L)
            da = Al[:, :, 1].sum(axis=1)
            share_left = da / max(drained0, 1e-9)
            rest = np.minimum(resto * share_left, da)
            frac = np.where(da[:, None] > 0, (rest / np.maximum(da, 1e-12))[:, None], 0.0)
            frac = np.broadcast_to(frac, (n_sims, K)).copy()
            _move_peat(np, A, L, F, frac, 1, 0, extra_L=0.0)

        S = np.maximum(S, 0.0)
        Al = alive(A, L)
        d_alive = Al[:, :, 1].sum(axis=1)
        p_alive = Al.sum(axis=(1, 2))
        f_alive = S[:, is_forest].sum(axis=1)
        drained_traj[t] = d_alive
        forest_traj[t] = f_alive
        agg_traj[t] = p_alive + f_alive
        now = {"peat_intact": Al[:, :, 0].sum(axis=1), "peat_drained": d_alive}
        for j, i in enumerate(nid):
            now[i] = S[:, j]
        for i in pool_ids:
            if i != "scrub_grass":
                hitp = (pool_ex[i] < 0) & (now[i] < EXHAUSTION_FRACTION * base_pool[i])
                pool_ex[i][hitp] = yr
            if yr == 2050:
                pool_2050[i] = now[i]
        if yr == 2050:
            peat_all_2050 = p_alive
        for arr, val, base in ((ex_peat, d_alive, drained0), (ex_forest, f_alive, forest0),
                               (ex_agg, p_alive + f_alive, agg0)):
            hit = (arr < 0) & (val < EXHAUSTION_FRACTION * base)
            arr[hit] = yr
        hit = (half_peat < 0) & (d_alive < 0.5 * drained0)
        half_peat[hit] = yr

    del sims
    yr_2050 = 2050 - (base_year + 1)
    in_h = 0 <= yr_2050 < horizon_years

    def pct(a, q):
        return float(np.percentile(a, q))

    def at2050(traj):
        if not in_h:
            return None
        return round(pct(traj[yr_2050], 50), 3)

    def band2050(traj):
        if not in_h:
            return None
        return [round(pct(traj[yr_2050], 10), 3), round(pct(traj[yr_2050], 90), 3)]

    tp10, tp50, tp90 = (np.percentile(agg_traj, q, axis=1) for q in (10, 50, 90))
    tp10, tp90 = np.minimum(tp10, tp50), np.maximum(tp90, tp50)

    labels = {str(p["id"]): p.get("label", p["id"]) for p in fs["pools"]}
    per_pool = []
    for pid in pool_ids:
        ex = pool_ex[pid]
        ever = ex > 0
        b = base_pool[pid]
        per_pool.append({
            "id": pid, "label": labels.get(pid, pid),
            "baseline_mha": round(b, 4),
            "exhaustion_year_p50": (int(np.percentile(ex[ever], 50)) if ever.mean() >= 0.5
                                    else None),
            "never_pct": round(100.0 * float((~ever).mean()), 2),
            "remaining_mha_2050": round(pct(pool_2050[pid], 50), 3) if in_h else None,
            "estimated": True,
        })

    peat_head = {
        "label": "Drained peat (the peat that burns)",
        "definition": ("Area of drained or fire-degraded Kalimantan peat with any thickness "
                       "left; 'exhausted' = below 5% of today's drained area. Rewetted peat "
                       "leaves this pool too (it stops subsiding and rarely burns)."),
        "baseline_mha": round(drained0, 3),
        "exhaustion_year": _quantile_years(ex_peat),
        "half_gone_year": _quantile_years(half_peat),
        "remaining_mha_2050": at2050(drained_traj),
        "remaining_mha_2050_p10_p90": band2050(drained_traj),
        "all_peat_remaining_mha_2050": round(pct(peat_all_2050, 50), 3) if in_h else None,
        "all_peat_baseline_mha": round(peat_area, 3),
        "carbon_lost_mt_c": {
            "fire_by_2050": round(pct(fire_c_2050, 50), 1) if in_h else None,
            "drainage_by_2050": round(pct(drain_c_2050, 50), 1) if in_h else None,
            "fire_by_horizon": round(pct(fire_c, 50), 1),
            "drainage_by_horizon": round(pct(drain_c, 50), 1),
            "note": "Medians. Drainage loss is oxidation (CO2 only); only fire emits PM2.5.",
        },
        "estimated": True,
    }
    forest_head = {
        "label": "Forest (primary + secondary, non-peat)",
        "definition": "Burnable forest below 5% of its baseline area.",
        "baseline_mha": round(forest0, 3),
        "exhaustion_year": _quantile_years(ex_forest),
        "remaining_mha_2050": at2050(forest_traj),
        "remaining_mha_2050_p10_p90": band2050(forest_traj),
        "estimated": True,
    }
    agg = _quantile_years(ex_agg)

    return {
        "headlines": {"peat": peat_head, "forest": forest_head},
        "exhaustion_year": agg,
        "exhaustion_year_note": ("Secondary metric (v1 compatibility): peat + forest area "
                                 "together below 5% of baseline. Use headlines.peat for haze."),
        "trajectories": {
            "years": years,
            "p10": [round(float(x), 4) for x in tp10],
            "p50": [round(float(x), 4) for x in tp50],
            "p90": [round(float(x), 4) for x in tp90],
            "unit": "Mha of peat (with thickness left) + forest",
            "baseline_mha": round(agg0, 4),
            "drained_peat_p50": [round(float(x), 4) for x in np.percentile(drained_traj, 50, axis=1)],
            "forest_p50": [round(float(x), 4) for x in np.percentile(forest_traj, 50, axis=1)],
        },
        "per_pool": per_pool,
        "cumulative_burned_mha_by_2050": round(pct(burned_2050, 50), 3),
        "cumulative_pm25_kt": round(pct(cum_pm, 50) / 1e6, 1),
        "assumptions": {
            "n_sims": n_sims, "horizon_years": horizon_years, "base_year": base_year,
            "restoration_mha_per_yr": resto, "burn_reduction_pct": burn_reduction_pct,
            "deforestation_mha_per_yr": defo, "seed": seed,
            "peat_area_mha": peat_area, "peat_area_source": col.get("area_source"),
            "drained_fraction": drained_frac, "drained_fraction_verified": False,
            "depth_classes": classes,
            "subsidence_m_per_yr": {"low": float(s_d.min()) if n_sims else None,
                                    "high": float(s_d.max()) if n_sims else None,
                                    "drawn": "uniform 0.034-0.05 per simulation",
                                    "source": "Evans et al. 2019; Hooijer et al. 2012"},
            "peat_burned_fraction_per_yr": {"low": pb["low"], "high": pb["high"],
                                            "drawn": "log-uniform per simulation"},
            "first_fire_depth_m": {"low": float(d1.min()), "high": float(d1.max()),
                                   "drawn": "uniform 0.17-0.37 per simulation"},
            "repeat_fire_ratios": r_n.tolist(),
            "nonpeat_burned_mha_per_yr": {"low": npb["low"], "high": npb["high"],
                                          "central": npb.get("central"),
                                          "drawn": "log-uniform per simulation"},
            "A_base_mha_per_yr": round(float(np.exp(np.mean(np.log(a_non)))), 4),
            "A_base_source": "B2 satellite-consistent series (GFED4.1s rescaled to MCD64A1-GFED5 level)",
            "v1_A_base_mha_per_yr": 0.198,
            "multipliers": {
                "peat": {f"{PHASES[i]}{'_pIOD' if j else ''}": round(float(m_peat_tab[i, j]), 3)
                         for i in range(3) for j in range(2)},
                "nonpeat": {f"{PHASES[i]}{'_pIOD' if j else ''}": round(float(m_non_tab[i, j]), 3)
                            for i in range(3) for j in range(2)},
                "fit": {k: v for k, v in fit.items() if k != "p_iod_given_phase"},
                "p_positive_iod_given_phase": fit["p_iod_given_phase"],
            },
            "enso_multipliers": {p: round(float(m_non_tab[i, 0]), 3) for i, p in enumerate(PHASES)},
            "enso_multipliers_estimated": True,
            "enso_transition_matrix": {PHASES[i]: {PHASES[j]: round(float(P[i, j]), 4)
                                                   for j in range(3)} for i in range(3)},
            "enso_transitions_observed": int(n_trans),
            "enso_initial_phase": last_phase,
            "theta_depletion": THETA_DEPLETION, "phi_conversion": PHI_CONVERSION,
            "peat_drainage_coupling": PEAT_DRAINAGE_COUPLING,
            "intact_susceptibility": INTACT_SUSCEPTIBILITY,
            "burned_intact_becomes_drained": BURNED_INTACT_BECOMES_DRAINED,
            "consolidation_m": CONSOLIDATION_M,
            "consolidation_mass_share": CONSOLIDATION_MASS_SHARE,
            "oxidation_share": OXIDATION_SHARE,
            "exhaustion_threshold_fraction": EXHAUSTION_FRACTION,
            "drainage_base_modelled": False,
            "peat_is_irreversible": True,
            "estimated": True,
        },
        "caveats": list(CAVEATS),
    }


def _move_peat(np, A, L, F, frac, s_from, s_to, extra_L=0.0):
    """Move a fraction of each class's nominal area between statuses, merging L by area.

    ``frac`` is (n_sims, K). The fire-count distribution of the receiving status is
    mixed in proportion to the moved area.
    """
    mv = A[:, :, s_from] * frac
    if not np.any(mv > 0):
        return
    tgt = A[:, :, s_to]
    newA = tgt + mv
    L[:, :, s_to] = np.where(newA > 0, (tgt * L[:, :, s_to] + mv * (L[:, :, s_from] + extra_L))
                             / np.maximum(newA, 1e-12), L[:, :, s_to])
    A[:, :, s_to] = newA
    A[:, :, s_from] = A[:, :, s_from] - mv
    tot_mv = mv.sum(axis=1)
    tot_to = tgt.sum(axis=1)
    wsum = np.maximum(tot_mv + tot_to, 1e-12)
    F[:, s_to, :] = (F[:, s_to, :] * tot_to[:, None] + F[:, s_from, :] * tot_mv[:, None]) / wsum[:, None]


CAVEATS = [
    "Peat that burns or oxidises does not come back on a human timescale. Tropical peat "
    "accumulates at roughly 0.15-0.4 mm a year (Page et al. 2004); drained peat loses "
    "about 43 mm a year to oxidation and compaction (Evans et al. 2019) and a first fire "
    "takes about 260 mm (Huijnen et al. 2016).",
    "The peat answer is now driven mostly by drainage, not fire. Drained peat subsides "
    "about 3.4-5 cm a year whether or not it burns (Hooijer et al. 2012; Evans et al. "
    "2019), so the shallowest drained peat (0.5-1 m, 22% of Kalimantan's peat) reaches "
    "zero thickness in roughly 12-23 years from oxidation alone. Eliminating fire does "
    "not stop that; rewetting does.",
    "The drainage base is not modelled. Real drained peat may stop subsiding before it "
    "is gone, when its surface reaches the level canals can drain to and it floods. No "
    "regional drainage-base data was verified, so this model can overstate how much "
    "peat disappears, particularly for the deep domes.",
    "How much of Kalimantan's peat is drained today is not well sourced. This model "
    "uses 60% (the v1 2.7 of 4.5 Mha, applied to the MoA 2011 extent of 4.78 Mha); it "
    "should be replaced by the Miettinen et al. 2016 managed/degraded fraction for "
    "Borneo once extracted. Wetlands International maps 5.77 Mha of Kalimantan peat.",
    "Peat thickness within each MoA depth class is taken as uniform over the class "
    "range, and the deepest class (>3 m) as 3-7 m with a 5 m mean. That last figure is "
    "a judgement informed by a 5.4 m Kalimantan mean in grey literature (Hooijer & "
    "Vernimmen 2013), not a measurement.",
    "Burn rates are calibrated on satellite burned area that is known to miss small and "
    "smouldering fires. Each simulation draws the peat burn rate between 3.6% a year "
    "(MODIS, Vetrita & Cochrane 2020, a lower bound) and about 6.5% (GFED5-consistent), "
    "and the non-peat rate between the MCD64A1 and GFED5 levels; the spread shown "
    "includes that uncertainty.",
    "The ENSO Markov chain is stationary, and the Indian Ocean Dipole enters only as a "
    "fitted multiplier on burned area. Neither responds to greenhouse warming here.",
    "Fire is averaged over each depth class: the model knows how much of a class has "
    "burned and how often, not which hectare. Repeat fires on the same patch take less "
    "each time (Konecny et al. 2016), which the fire-count distribution captures only "
    "on average.",
    "Burned forest moves to the scrub pool and keeps burning, so 'forest exhausted' "
    "means no forest left, not no fire. Forest fuel loads (55/75 t/ha) and all burn "
    "susceptibilities are unverified model parameters.",
    "Restoration is modelled as rewetting that moves drained peat back to the intact "
    "pool and stops its subsidence. Real canal blocks fail, and rewetted peat can still "
    "burn in an extreme drought year.",
]
