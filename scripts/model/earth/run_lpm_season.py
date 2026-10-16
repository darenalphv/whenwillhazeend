"""Offline LPM-3L season runs: Lambda0 calibration, the source-receptor matrix, validation.

For each bundled year with observed ERA5 met, releases particles every 3 h from every
active GFED4.1s cell (peat and non-peat separately, each with its own injection
profile), runs Jun 25 - Nov 30 at dt = 15 min, and records hourly surface fire PM2.5
at every receptor, split by source region (tag) and by days since release (lag 0-7).

Products
  app/data/earth/srm.npz            M[year, r, s, month, lag]  ug/m3 per (kg/day)
                                    (emission-weighted sensitivity: sum of the
                                    concentration from source s released `lag` days
                                    earlier, divided by the emission of s on those days)
                                    plus a multi-year mean, and daily receptor series
  app/data/earth/lpm_validation.json  spec section 9 row 6 targets, per year
  app/data/earth/lpm_calibration.json Lambda0 and the lifetime it gives

Usage:
  .venv/bin/python scripts/model/earth/run_lpm_season.py calibrate 2015 2019
  .venv/bin/python scripts/model/earth/run_lpm_season.py run 2015 2019 ...
"""
from __future__ import annotations

import json
import math
import pathlib
import sys
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from app.model import emissions as E  # noqa: E402
from app.model import transport as T  # noqa: E402
from app.data.loader import load_receptors  # noqa: E402

OUT = ROOT / "app" / "data" / "earth"
GRID = (95.0, 120.0, -8.0, 8.0, 0.25)       # receptor accumulation grid
RELEASE_EVERY_H = 3
DT_S = 900.0
N_LAG = 8
MONTHS = (7, 8, 9, 10, 11)


def _grid():
    lo0, lo1, la0, la1, d = GRID
    nx = int(round((lo1 - lo0) / d))
    ny = int(round((la1 - la0) / d))
    lat_c = la0 + (np.arange(ny) + 0.5) * d
    area = (d * 111.32e3) ** 2 * np.cos(np.radians(lat_c))     # m2 per cell, by row
    return nx, ny, area


def _receptor_weights(recs, nx, ny):
    """(ncell, nrec) weights: mean over the 3x3 block of 0.25-deg cells (~80 km)."""
    lo0, lo1, la0, la1, d = GRID
    W = np.zeros((nx * ny, len(recs)))
    for k, r in enumerate(recs):
        ci = int((r["lon"] - lo0) / d)
        cj = int((r["lat"] - la0) / d)
        cells = [(cj + dj, ci + di) for dj in (-1, 0, 1) for di in (-1, 0, 1)
                 if 0 <= cj + dj < ny and 0 <= ci + di < nx]
        for (j, i) in cells:
            W[j * nx + i, k] = 1.0 / len(cells)
    return W


def run_year(year, lambda0=None, soa=False, record=True, end=None, seed=1, verbose=True):
    met = T.load_met(year)
    if met is None:
        raise SystemExit(f"no met for {year}")
    regions = E.region_table()
    n_src = len(regions)
    ntag = n_src * 2                     # (region, peat|nonpeat)
    recs = [r for r in load_receptors()]
    nrec = len(recs)
    nx, ny, area_row = _grid()
    W = _receptor_weights(recs, nx, ny)
    lo0, lo1, la0, la1, d = GRID

    start = datetime(year, 6, 25, tzinfo=timezone.utc)
    stop = end or datetime(year, 11, 30, 23, tzinfo=timezone.utc)
    lpm = T.LPM(met, dt_s=DT_S, seed=seed, lambda0=lambda0, soa=soa,
                t_start_h=met.hours_since_t0(start), max_age_h=24.0 * 10)
    n_days = (stop.date() - date(year, 7, 1)).days + 1
    C = np.zeros((n_days, nrec, ntag, N_LAG))            # daily-mean ug/m3
    Edaily = np.zeros((n_days + N_LAG, ntag))            # kg/day by source tag
    grid_sum = np.zeros(ny * nx)
    n_hours_rec = np.zeros(n_days)
    burden_acc = 0.0
    removed0 = (0.0, 0.0)
    n_steps = 0
    t_wall = time.time()
    t = start
    cur_day = None
    em = None
    while t <= stop:
        if t.date() != cur_day:
            cur_day = t.date()
            em = E.daily_emissions(cur_day)
            if em is not None:
                di = (cur_day - date(year, 7, 1)).days
                tg = em["region"] * 2
                if 0 <= di < n_days:
                    Edaily[di] += np.bincount(tg, weights=em["peat_kg"], minlength=ntag)
                    Edaily[di] += np.bincount(tg + 1, weights=em["nonpeat_kg"], minlength=ntag)
        # release every 3 h
        if em is not None and t.hour % RELEASE_EVERY_H == 0 and t.minute == 0:
            q = t.hour // 3
            for kind, mass, tagoff in ((T.KIND_PEAT, em["peat_kg"] / 8.0, 0),
                                       (T.KIND_FLAMING, em["nonpeat_kg"] * em["diurnal"][q], 1)):
                sel = mass > 0
                if sel.any():
                    n = int(sel.sum())
                    jit = (np.random.default_rng(n_steps).random((2, n)) - 0.5) * 0.25
                    lpm.release(em["lon"][sel] + jit[0], em["lat"][sel] + jit[1], mass[sel],
                                kind, em["region"][sel] * 2 + tagoff)
        lpm.step()
        n_steps += 1
        t = t + timedelta(seconds=DT_S)
        burden_acc += lpm.burden()
        # hourly receptor sampling
        if record and t.minute == 0 and t.date() >= date(year, 7, 1):
            di = (t.date() - date(year, 7, 1)).days
            if di < n_days and len(lpm.x):
                in1 = lpm.z <= lpm.H
                ci = ((lpm.x - lo0) / d).astype(int)
                cj = ((lpm.y - la0) / d).astype(int)
                ok = in1 & (ci >= 0) & (ci < nx) & (cj >= 0) & (cj < ny)
                if ok.any():
                    cell = cj[ok] * nx + ci[ok]
                    mass = lpm.effective_mass()[ok]
                    val = mass * 1e9 / (area_row[cj[ok]] * lpm.H[ok])        # ug/m3
                    grid_sum[:] += np.bincount(cell, weights=val, minlength=nx * ny)
                    lag = np.minimum((lpm.age[ok] / 24.0).astype(int), N_LAG - 1)
                    tl = lpm.tag[ok] * N_LAG + lag
                    # only cells that feed a receptor
                    rc = W[cell].sum(axis=1) > 0
                    if rc.any():
                        G = np.bincount(tl[rc] * (nx * ny) + cell[rc], weights=val[rc],
                                        minlength=ntag * N_LAG * nx * ny
                                        ).reshape(ntag * N_LAG, nx * ny)
                        R = G @ W                                            # (ntag*lag, nrec)
                        C[di] += R.T.reshape(nrec, ntag, N_LAG)
                n_hours_rec[di] += 1
        if verbose and n_steps % (96 * 10) == 0:
            print(f"  {t.date()} particles {len(lpm.x)} burden {lpm.burden() / 1e6:.1f} kt "
                  f"wall {time.time() - t_wall:.0f}s", flush=True)
    C /= np.maximum(n_hours_rec, 1)[:, None, None, None]
    removed = lpm.removed_dry + lpm.removed_wet
    mean_burden = burden_acc / max(n_steps, 1)
    run_hours = n_steps * DT_S / 3600.0
    lifetime_d = mean_burden / (removed / run_hours) / 24.0 if removed > 0 else None
    return {
        "year": year, "C": C, "E": Edaily, "recs": recs, "regions": regions,
        "grid_mean": (grid_sum / max(n_hours_rec.sum(), 1)).reshape(ny, nx),
        "lifetime_days": lifetime_d, "lambda0": lpm.lambda0,
        "removed_dry_kt": lpm.removed_dry / 1e6, "removed_wet_kt": lpm.removed_wet / 1e6,
        "exported_kt": lpm.exported / 1e6, "released_kt": lpm.released / 1e6,
        "soa": soa, "wall_s": time.time() - t_wall,
    }


def calibrate(years, target=4.5):
    """Find Lambda0 giving a Jul-Oct domain-mean e-folding lifetime of `target` days."""
    lo, hi = 1e-6, 3e-4
    res = []
    for it in range(7):
        lam = math.sqrt(lo * hi)
        taus = []
        for y in years:
            r = run_year(y, lambda0=lam, record=False,
                         end=datetime(y, 10, 31, 23, tzinfo=timezone.utc), verbose=False)
            taus.append(r["lifetime_days"])
        tau = float(np.mean(taus))
        res.append({"lambda0_per_s": lam, "lifetime_days": tau, "per_year": taus})
        print(f"lambda0 {lam:.3e} lifetime {tau:.2f} d {taus}", flush=True)
        if tau > target:
            lo = lam
        else:
            hi = lam
    best = min(res, key=lambda r: abs(r["lifetime_days"] - target))
    out = {"lambda0_per_s": best["lambda0_per_s"], "lifetime_days": best["lifetime_days"],
           "target_days": target, "target_range_days": [3, 6], "years": years,
           "iterations": res,
           "method": ("Bisection on Lambda0 so the Jul-Oct mass-weighted e-folding lifetime "
                      "(mean burden / mean removal rate, export excluded) is 4.5 d, the centre "
                      "of 3-6 d (Lee et al. 2017 <3 d wet / >5 d dry; AeroCom POM 6 d). "
                      "Lambda0 is calibrated, not a literature value (Wang et al. 2010: "
                      "1-2 orders of magnitude uncertainty)."),
           "v_d_m_s": T.param("dry_dep_velocity", 0.0015),
           "k_h_m2_s": T.param("horizontal_diffusivity", 1e4)}
    (OUT / "lpm_calibration.json").write_text(json.dumps(out, indent=1) + "\n")
    print("wrote", OUT / "lpm_calibration.json", out["lambda0_per_s"], out["lifetime_days"])


def _validation(r_off, r_on):
    """Spec section 9 row 6 targets, plus the Hansen 2019 split and KL."""
    y = r_off["year"]
    recs = [x["id"] for x in r_off["recs"]]
    regions = r_off["regions"]
    isl = np.array([regions[t // 2]["island"] for t in range(len(regions) * 2)])
    rid = np.array([regions[t // 2]["id"] for t in range(len(regions) * 2)])
    d0 = date(y, 7, 1)

    def window(a, b):
        return slice((a - d0).days, (b - d0).days + 1)

    def mean_at(r, rid_, win, which=r_off):
        C = which["C"]
        return float(C[win, recs.index(rid_)].sum(axis=(1, 2)).mean())

    jul_oct = window(date(y, 7, 1), date(y, 10, 31))
    aug_oct = window(date(y, 8, 1), date(y, 10, 31))
    sep_nov = window(date(y, 9, 1), date(y, 11, 30))
    out = {"year": y}
    sg_regions = [x for x in recs if x.startswith("sg-")]
    for name, ids in (("singapore", ["singapore"]), ("singapore_nea_regions_mean", sg_regions),
                      ("kuala_lumpur", ["my-kuala-lumpur"]), ("kuching", ["kuching"]),
                      ("pekanbaru", ["pekanbaru"]), ("palangkaraya", ["palangkaraya"]),
                      ("pontianak", ["pontianak"]), ("jambi", ["jambi"])):
        v_off = float(np.mean([mean_at(None, i, jul_oct) for i in ids]))
        v_on = float(np.mean([mean_at(None, i, jul_oct, r_on) for i in ids])) if r_on else None
        so = float(np.mean([mean_at(None, i, sep_nov) for i in ids]))
        out[name] = {"jul_oct_mean_fire_pm25_primary": round(v_off, 1),
                     "jul_oct_mean_fire_pm25_with_soa": round(v_on, 1) if v_on is not None else None,
                     "sep_nov_mean_fire_pm25_primary": round(so, 1)}
    # Singapore 2015 target (Koplitz 2016): 27 modelled / 30 observed, within x2
    sg = out["singapore"]["jul_oct_mean_fire_pm25_primary"]
    out["target_singapore_jul_oct"] = {
        "target_ugm3": [27, 30], "pass_band_ugm3": [13.5, 60],
        "modelled_primary": sg, "modelled_with_soa": out["singapore"]["jul_oct_mean_fire_pm25_with_soa"],
        "pass": bool(13.5 <= sg <= 60) if y == 2015 else None,
        "source": "Koplitz et al. 2016 (ERL 11:094023): Jul-Oct 2015 smoke 27 modelled / 30 observed"}
    # source shares
    C = r_off["C"]

    def shares(rec_id, win):
        tot = C[win, recs.index(rec_id)].sum(axis=(0, 2))       # by tag
        s = tot.sum()
        by_isl = {k: round(float(tot[isl == k].sum() / s), 3) for k in sorted(set(isl))} if s > 0 else {}
        by_reg = {}
        for k in sorted(set(rid)):
            by_reg[k] = round(float(tot[rid == k].sum() / s), 3) if s > 0 else None
        return by_isl, by_reg

    ki, kr = shares("kuching", jul_oct)
    borneo = ki.get("kalimantan", 0) + ki.get("malaysian_borneo", 0)
    out["target_kuching_borneo_share"] = {"modelled": round(borneo, 3), "target": "> 0.70",
                                          "reference": 0.846, "pass": bool(borneo > 0.7),
                                          "by_island": ki,
                                          "source": "Lee et al. 2017 (ACP 17:965): 84.6% annual 2003-2014"}
    si, sr = shares("singapore", aug_oct)
    out["singapore_aug_oct_source_split"] = {
        "by_island": si, "by_region": sr,
        "hansen_2019_west_east": {"sumsel": [0.382, 0.218], "kalteng": [0.312, 0.415],
                                  "babel": [0.113, 0.136], "kalbar": [0.066, 0.094],
                                  "jambi": [0.065, 0.060]},
        "source": "Hansen et al. 2019 (ACP 19:5363), NAME III with 2015 winds"}
    kli, klr = shares("my-kuala-lumpur", aug_oct)
    out["kuala_lumpur_aug_oct_source_split"] = {"by_island": kli, "by_region": klr}
    return out


def run(years):
    cal = T.calibration()
    allv = {}
    srm = {}
    series = {}
    for y in years:
        print("year", y, "lambda0", cal.get("lambda0_per_s"), flush=True)
        r_off = run_year(y, soa=False)
        print(f"  primary run {r_off['wall_s']:.0f}s, lifetime {r_off['lifetime_days']:.2f} d", flush=True)
        r_on = run_year(y, soa=True, record=True, verbose=False)
        v = _validation(r_off, r_on)
        v["lifetime_days"] = round(r_off["lifetime_days"], 2)
        v["mass_budget_kt"] = {k: round(r_off[k], 1) for k in
                               ("released_kt", "removed_dry_kt", "removed_wet_kt", "exported_kt")}
        tot = E.season_totals(y, date(y, 9, 1), date(y, 10, 31))
        v["emissions_sep_oct_tg"] = {k: round(val, 2) for k, val in tot.items()}
        v["target_sep_oct_primary_tg"] = {"modelled": round(tot.get("total_tg", 0), 2),
                                          "target": [4, 11], "pass": (4 <= tot.get("total_tg", 0) <= 11)
                                          if y == 2015 else None,
                                          "source": "Wooster 2018 9.1 Tg; Kiely 2019 7.33 Tg; GFED4s 4.2; GFAS 3.99"}
        allv[str(y)] = v
        print(json.dumps({k: v[k] for k in ("singapore", "kuala_lumpur", "kuching",
                                            "target_kuching_borneo_share",
                                            "singapore_aug_oct_source_split")}, indent=0)[:3000],
              flush=True)
        # SRM: M[r, tag, month, lag]
        C, Ed = r_off["C"], r_off["E"]
        nrec, ntag = C.shape[1], C.shape[2]
        M = np.zeros((nrec, ntag, len(MONTHS), N_LAG), np.float32)
        d0 = date(y, 7, 1)
        for mi, m in enumerate(MONTHS):
            days = [k for k in range(C.shape[0]) if (d0 + timedelta(days=k)).month == m]
            for lag in range(N_LAG):
                num = C[days, :, :, lag].sum(axis=0)                 # (nrec, ntag)
                src_days = [k - lag for k in days if k - lag >= 0]
                den = Ed[src_days].sum(axis=0) if src_days else np.zeros(ntag)
                M[:, :, mi, lag] = np.where(den > 0, num / np.maximum(den, 1e-9), 0.0)
        srm[y] = M
        series[y] = C.sum(axis=(2, 3)).astype(np.float32)           # (days, nrec) primary
        series[f"{y}_soa"] = r_on["C"].sum(axis=(2, 3)).astype(np.float32)
        series[f"{y}_bytag"] = C.sum(axis=3).astype(np.float32)     # (days, nrec, ntag)
        np.save(HERE / "_raw" / f"grid_mean_{y}.npy", r_off["grid_mean"])
    prev = json.loads((OUT / "lpm_validation.json").read_text()) if (OUT / "lpm_validation.json").exists() else {}
    prev.update(allv)
    (OUT / "lpm_validation.json").write_text(json.dumps(prev, indent=1) + "\n")
    # merge with existing srm.npz
    arrays = {}
    p = OUT / "srm.npz"
    if p.exists():
        with np.load(p) as z:
            arrays = {k: z[k] for k in z.files}
    for y, M in srm.items():
        arrays[f"M_{y}"] = M
        arrays[f"series_{y}"] = series[y]
        arrays[f"series_soa_{y}"] = series[f"{y}_soa"]
        arrays[f"series_bytag_{y}"] = series[f"{y}_bytag"]
    ys = sorted(int(k[2:]) for k in arrays if k.startswith("M_") and k[2:].isdigit())
    arrays["years"] = np.array(ys)
    arrays["M_mean"] = np.mean([arrays[f"M_{y}"] for y in ys], axis=0).astype(np.float32)
    arrays["receptor_ids"] = np.array([r["id"] for r in load_receptors()])
    arrays["tag_region"] = np.array([r["id"] for r in E.region_table() for _ in (0, 1)])
    arrays["tag_kind"] = np.array(["peat", "nonpeat"] * len(E.region_table()))
    arrays["months"] = np.array(MONTHS)
    arrays["n_lag"] = np.array(N_LAG)
    arrays["units"] = np.array("ug/m3 per (kg/day); series in ug/m3, from Jul 1")
    np.savez_compressed(p, **arrays)
    print("wrote", p, round(p.stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    mode = sys.argv[1]
    yrs = [int(a) for a in sys.argv[2:]]
    if mode == "calibrate":
        calibrate(yrs)
    else:
        run(yrs)
