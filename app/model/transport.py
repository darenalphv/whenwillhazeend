"""LPM-3L: a reduced-form three-layer Lagrangian particle model for fire smoke.

Implements MODEL_SPEC.md section 5.4 (transport) and section 6.4 (boundary layer,
H1-H4). Everything here is plain numpy so the same code runs offline (to build the
source-receptor matrix in ``scripts/model/earth/``) and inside a request (the 3D
view in :mod:`app.model.haze3d`).

Layers
------
* **L1, mixed layer**: 0 .. H(x, t). Particles in L1 are redistributed uniformly in
  [0, H] every step (well mixed) and are the only ones that count at the surface.
* **L2, residual layer**: H(x, t) .. H_max, the day's afternoon maximum.
* **L3, free troposphere**: above H_max.

When H rises in the morning, L2 particles below the new H rejoin L1 (fumigation of
yesterday's smoke at downwind cities). When H falls in the evening, L1 particles
left above the new H become L2 and keep their height (nocturnal decoupling).

Advection (dt = 15 min offline, 30 min in a request [J])::

    L1 by day   : mean of the near-surface (1000 hPa) and 925 hPa winds
    L1 by night : near-surface (1000 hPa) wind
    L2          : 925 hPa wind
    L3          : 850 hPa wind
    + horizontal random walk, sigma = sqrt(2 K_h dt), K_h = 1e4 m2/s (UNVERIFIED)

The spec asks for the 100 m wind for the near-surface layer. The open ERA5 copy used
here (WeatherBench 2, 1.5 deg, 6-hourly) has no 100 m wind, so the 1000 hPa wind
(roughly 100 m above sea level over the lowlands) stands in for it [J].

Boundary layer (H1-H4)
----------------------
H(x, t) = H_day(x, day) * P_type(x)(local hour), where H_day is the ERA5 daily mean
at the particle and P is one of five ERA5-derived station profiles (Palangka Raya,
Pontianak, Kuching, Singapore, Pekanbaru; Aug-Oct 2010-2023, research/data/
era5_blh_summary.json), assigned by region type (spec H1). Over open sea the profile
is flat [J]: the marine boundary layer has little diurnal cycle. Floor 100 m
(UNVERIFIED, range 50-150), ceiling 2,500 m [D].

Removal (first order)
---------------------
* dry, L1 only: lambda_d = v_d / H,  v_d = 0.15 cm/s (UNVERIFIED, range 0.05-0.3)
* wet, all layers below 5 km where P > 0.1 mm/h:
  lambda_w = Lambda0 * (P / 1 mm/h)^0.7, Lambda0 CALIBRATED offline so the Jul-Oct
  domain-mean e-folding lifetime of fire PM is in 3-6 days (Lee 2017; AeroCom).
  The calibrated value lives in app/data/earth/lpm_calibration.json.

SOA (toggle, default on)
------------------------
Asymptotic SOA/POA = Y * EF_CO / EF_PM = 0.04 * 255 / 22.3 = 0.46 [D], formed with a
1-day time constant (UNVERIFIED placeholder). Applied as a multiplier on each
particle's primary mass, ``1 + R (1 - exp(-age / tau))``, which is exact for
first-order removal because the SOA travels with its parent. Peat ratios are used
for all smoke [J]: there is no verified forest EF_CO in the parameter set.

Surface concentration::

    C_sfc(cell) = sum_{p in L1, in cell} m_p / (A_cell * H(t)) + C_bg
"""

from __future__ import annotations

import functools
import math
from datetime import datetime, timedelta, timezone

__all__ = [
    "BLH_PROFILES", "BLH_STATIONS", "BLH_CLIMATOLOGY_M", "BLH_FLOOR_M", "BLH_CEIL_M",
    "profile_factor", "diurnal_mixing_height", "profile_type_at",
    "Met", "load_met", "met_years", "uniform_met", "climatology_met",
    "LPM", "param", "calibration",
]

# --------------------------------------------------------------------------- #
# parameters
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=1)
def _params() -> dict:
    try:
        from app.data.loader import load_earth_parameters
        return load_earth_parameters().get("parameters", {})
    except Exception:
        return {}


def param(pid: str, default: float, field: str = "value") -> float:
    """A parameter from app/data/earth/parameters.json (copied from research/)."""
    p = _params().get(pid) or {}
    v = p.get(field)
    return float(v) if v is not None else float(default)


@functools.lru_cache(maxsize=1)
def calibration() -> dict:
    """Offline calibration (Lambda0, lifetime achieved). Empty if not built yet."""
    try:
        from app.data.loader import load_earth_json
        return load_earth_json("lpm_calibration.json") or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# H1-H4: boundary-layer profiles
# --------------------------------------------------------------------------- #

#: Normalised hourly ERA5 BLH / daily mean, 00..23 local clock time.
#: MODEL_SPEC 6.2, from research/data/era5_blh_summary.json (ERA5 via Open-Meteo,
#: Aug-Oct 2010-2023) [D].
BLH_PROFILES = {
    "palangkaraya": [.36, .35, .33, .31, .29, .23, .27, .59, .99, 1.51, 2.03, 2.41,
                     2.65, 2.70, 2.57, 2.14, 1.44, .51, .40, .39, .39, .39, .39, .38],
    "pontianak":    [.45, .46, .46, .47, .48, .44, .45, .63, .93, 1.33, 1.78, 2.16,
                     2.39, 2.40, 2.19, 1.78, 1.33, .79, .62, .55, .51, .49, .47, .46],
    "kuching":      [.16, .16, .16, .17, .17, .18, .16, .18, .36, .87, 1.60, 2.32,
                     2.91, 3.29, 3.35, 2.97, 2.09, 1.16, .50, .38, .28, .22, .19, .17],
    "singapore":    [.46, .45, .45, .46, .47, .48, .44, .45, .61, 1.09, 1.45, 1.76,
                     1.99, 2.08, 2.04, 1.93, 1.79, 1.63, 1.19, .74, .57, .52, .49, .47],
    "pekanbaru":    [.24, .24, .25, .26, .26, .24, .23, .30, .61, 1.18, 1.86, 2.46,
                     2.89, 3.10, 3.07, 2.70, 1.88, .60, .34, .29, .27, .26, .25, .24],
    "flat":         [1.0] * 24,
}
#: Station ERA5 cell (lat, lon) and the UTC offset of the local clock the profile
#: was tabulated in. Used to shift a profile to a particle's solar time.
BLH_STATIONS = {
    "palangkaraya": (-2.25, 114.0, 7.0),
    "pontianak": (0.0, 109.25, 7.0),
    "kuching": (1.5, 110.25, 8.0),
    "singapore": (1.5, 103.75, 8.0),
    "pekanbaru": (0.5, 101.5, 7.0),
}
#: ERA5 Aug-Oct 2010-2023 daily-mean BLH, m (spec H2 defaults).
BLH_CLIMATOLOGY_M = {"palangkaraya": 488.0, "pontianak": 358.0, "kuching": 278.0,
                     "singapore": 386.0, "pekanbaru": 323.0}
FIRE_YEAR_BLH_MULTIPLIER = 1.4          # spec H2 [D]: PR 2015/2019/2023 vs clim
BLH_FLOOR_M = 100.0                     # spec H3 [J], UNVERIFIED (range 50-150)
BLH_CEIL_M = 2500.0                     # spec H3 [D]
PROFILE_ORDER = ("palangkaraya", "pontianak", "kuching", "singapore", "pekanbaru", "flat")


def _norm(vals):
    m = sum(vals) / len(vals)
    return [v / m for v in vals]


_PROF_N = {k: _norm(v) for k, v in BLH_PROFILES.items()}


def profile_factor(site: str, clock_hour: float) -> float:
    """BLH / daily mean at a local clock hour, linearly interpolated, mean 1.00."""
    p = _PROF_N.get(site) or _PROF_N["palangkaraya"]
    h = float(clock_hour) % 24.0
    i = int(math.floor(h))
    f = h - i
    return p[i] * (1.0 - f) + p[(i + 1) % 24] * f


def diurnal_mixing_height(mean_m: float, hour_of_day: float,
                          site: str = "palangkaraya") -> float:
    """Mixing-layer depth at a local hour, for a given daily mean (spec H1/H3)."""
    h = float(mean_m) * profile_factor(site, hour_of_day)
    return float(min(BLH_CEIL_M, max(BLH_FLOOR_M, h)))


def profile_type_at(lat: float, lon: float, land: bool = True) -> str:
    """Spec H1 region-type assignment [J: the boxes are this model's own]."""
    if not land:
        return "flat"
    if lon < 104.6:
        return "pekanbaru" if lat < 1.0 else "singapore"   # Sumatra / Peninsula+SG
    if lon < 108.6:
        return "pontianak"                                  # Bangka, Belitung, Riau Is.
    if lat > 1.0:
        return "kuching"                                    # Sarawak, Sabah
    if lon < 110.6 and lat > -2.5:
        return "pontianak"                                  # coastal West Kalimantan
    return "palangkaraya"                                   # inland Borneo


def _profile_table(np):
    """(6, 24) array of normalised profiles in PROFILE_ORDER, plus clock offsets."""
    tab = np.array([_PROF_N[k] for k in PROFILE_ORDER], dtype=np.float64)
    # solar-minus-clock offset of each station: profile is indexed by station clock
    off = []
    for k in PROFILE_ORDER:
        if k in BLH_STATIONS:
            la, lo, tz = BLH_STATIONS[k]
            off.append(lo / 15.0 - tz)
        else:
            off.append(0.0)
    return tab, np.array(off)


# --------------------------------------------------------------------------- #
# meteorology
# --------------------------------------------------------------------------- #

LEVELS = ("sfc", "925", "850")      # near-surface (1000 hPa), 925 hPa, 850 hPa
LEVEL_HEIGHTS_M = (100, 750, 1500)  # nominal heights AGL for display [J]


class Met:
    """Gridded winds, BLH and rain on a regular lat/lon grid, regularly in time.

    Arrays are float32, shape (n_t, ny, nx), rows south->north. ``hday`` is the
    ERA5 daily-mean BLH assigned to each time step; the diurnal shape comes from
    the station profiles via ``ptype``.
    """

    def __init__(self, np, lat, lon, t0, dt_h, fields, ptype, label, source):
        self.np = np
        self.lat = np.asarray(lat, dtype=np.float64)
        self.lon = np.asarray(lon, dtype=np.float64)
        self.t0 = t0                    # datetime (UTC) of index 0
        self.dt_h = float(dt_h)
        self.f = fields                 # dict name -> (n_t, ny, nx) float32
        self.n_t = next(iter(fields.values())).shape[0]
        self.ptype = ptype              # (ny, nx) int8 index into PROFILE_ORDER
        self.label = label              # "observed" | "climatology" | "uniform"
        self.source = source
        self.dlat = float(self.lat[1] - self.lat[0]) if len(self.lat) > 1 else 1.0
        self.dlon = float(self.lon[1] - self.lon[0]) if len(self.lon) > 1 else 1.0
        self.prof, self.prof_off = _profile_table(np)

    # time ----------------------------------------------------------------
    def hours_since_t0(self, dt: datetime) -> float:
        return (dt - self.t0).total_seconds() / 3600.0

    def covers(self, start: datetime, end: datetime) -> bool:
        a = self.hours_since_t0(start)
        b = self.hours_since_t0(end)
        return a >= 0 and b <= (self.n_t - 1) * self.dt_h

    def slice_at(self, t_h: float) -> dict:
        """Fields linearly interpolated to hour t_h since t0 (clamped)."""
        np = self.np
        x = min(max(t_h / self.dt_h, 0.0), self.n_t - 1.0)
        i = min(int(math.floor(x)), self.n_t - 2) if self.n_t > 1 else 0
        w = x - i if self.n_t > 1 else 0.0
        out = {}
        for k, a in self.f.items():
            if self.n_t == 1:
                out[k] = a[0].astype(np.float64)
            else:
                out[k] = a[i].astype(np.float64) * (1.0 - w) + a[i + 1].astype(np.float64) * w
        return out

    # space ---------------------------------------------------------------
    def index(self, lon, lat):
        """Fractional grid indices and bilinear weights for particle positions."""
        np = self.np
        fx = np.clip((lon - self.lon[0]) / self.dlon, 0.0, len(self.lon) - 1.000001)
        fy = np.clip((lat - self.lat[0]) / self.dlat, 0.0, len(self.lat) - 1.000001)
        ix = fx.astype(np.int64)
        iy = fy.astype(np.int64)
        return ix, iy, fx - ix, fy - iy

    @staticmethod
    def bilinear(F, idx):
        ix, iy, wx, wy = idx
        return ((F[iy, ix] * (1 - wx) + F[iy, ix + 1] * wx) * (1 - wy)
                + (F[iy + 1, ix] * (1 - wx) + F[iy + 1, ix + 1] * wx) * wy)

    def blh(self, hday, idx, ptype_idx, utc_hour):
        """H at particles: daily mean x station profile at the particle's solar time."""
        np = self.np
        ix, iy, wx, wy = idx
        hd = self.bilinear(hday, idx)
        solar = (utc_hour + self.lon_of(idx) / 15.0) % 24.0
        clock = (solar - self.prof_off[ptype_idx]) % 24.0
        i0 = clock.astype(np.int64) % 24
        f = clock - np.floor(clock)
        p = self.prof[ptype_idx, i0] * (1 - f) + self.prof[ptype_idx, (i0 + 1) % 24] * f
        h = np.clip(hd * p, BLH_FLOOR_M, BLH_CEIL_M)
        hmax = np.clip(hd * self.prof[ptype_idx].max(axis=-1), BLH_FLOOR_M, BLH_CEIL_M)
        return h, hmax

    def lon_of(self, idx):
        ix, iy, wx, wy = idx
        return self.lon[0] + (ix + wx) * self.dlon

    def ptype_at(self, idx):
        ix, iy, wx, wy = idx
        return self.ptype[iy + (wy > 0.5), ix + (wx > 0.5)]


def _ptype_grid(np, lat, lon, land):
    pt = np.zeros((len(lat), len(lon)), dtype=np.int8)
    for j, la in enumerate(lat):
        for i, lo in enumerate(lon):
            pt[j, i] = PROFILE_ORDER.index(profile_type_at(float(la), float(lo),
                                                           bool(land[j, i])))
    return pt


@functools.lru_cache(maxsize=1)
def met_years() -> tuple:
    """Years for which observed ERA5 meteorology is bundled."""
    try:
        from app.data.loader import earth_files
        return tuple(sorted(int(p.stem.split("_")[1]) for p in earth_files("met_*.npz")
                            if p.stem.split("_")[1].isdigit()))
    except Exception:
        return ()


def _decode_met(np, z, label, source):
    sc = float(z["scale"])
    fields = {}
    for k in ("u_sfc", "v_sfc", "u_925", "v_925", "u_850", "v_850", "u10", "v10"):
        if k in z:
            fields[k] = z[k].astype(np.float32) * sc
    fields["hday"] = z["hday"].astype(np.float32)
    fields["rain"] = z["rain"].astype(np.float32) * float(z["rain_scale"])   # mm/h
    t0 = datetime.fromisoformat(str(z["t0"])).replace(tzinfo=timezone.utc)
    return Met(np, z["lat"], z["lon"], t0, float(z["dt_h"]), fields,
               z["ptype"].astype(np.int64), label, source)


@functools.lru_cache(maxsize=2)
def load_met(year: int):
    """Observed ERA5 met for a fire season (Jun 20 - Dec 5), or None.

    Cached two-deep: one year is ~2.5 MB on disk and ~25 MB as float32 in memory.
    """
    import numpy as np
    from app.data.loader import earth_path
    p = earth_path(f"met_{int(year)}.npz")
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=False) as z:
        return _decode_met(np, z, "observed",
                           "ERA5 (Hersbach et al. 2020) via WeatherBench 2, 1.5 deg, 6-hourly")


@functools.lru_cache(maxsize=1)
def _clim_npz():
    import numpy as np
    from app.data.loader import earth_path
    p = earth_path("met_clim.npz")
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def climatology_met(start: datetime, hours: float):
    """A synthetic met series built from the monthly ERA5 composite (4 per day).

    Used when the requested season has no bundled observed meteorology. It has
    the right prevailing flow and diurnal cycle for the month, and none of the
    day-to-day weather.
    """
    import numpy as np
    z = _clim_npz()
    if z is None:
        return None
    t0 = datetime(start.year, start.month, start.day, tzinfo=timezone.utc) - timedelta(days=1)
    n_t = int(math.ceil((hours + 48.0) / 6.0)) + 1
    months = z["months"].tolist()
    sc = float(z["scale"])
    fields = {k: [] for k in ("u_sfc", "v_sfc", "u_925", "v_925", "u_850", "v_850",
                               "u10", "v10", "hday", "rain")}
    for k in range(n_t):
        t = t0 + timedelta(hours=6 * k)
        m = t.month if t.month in months else min(months, key=lambda mm: abs(mm - t.month))
        mi = months.index(m)
        si = (t.hour // 6) % 4
        for name in fields:
            arr = z[name][mi, si]
            if name == "hday":
                fields[name].append(arr.astype(np.float32))
            elif name == "rain":
                fields[name].append(arr.astype(np.float32) * float(z["rain_scale"]))
            else:
                fields[name].append(arr.astype(np.float32) * sc)
    fields = {k: np.stack(v) for k, v in fields.items()}
    return Met(np, z["lat"], z["lon"], t0, 6.0, fields, z["ptype"].astype(np.int64),
               "climatology",
               f"ERA5 monthly composite of the bundled fire seasons "
               f"({', '.join(str(int(y)) for y in z['years'])}), 1.5 deg")


def uniform_met(wind_speed_ms: float, wind_dir_deg: float, mean_blh_m: float,
                rain_mm_day: float = 0.0, veer_deg_per_h: float = 0.0,
                start: datetime | None = None, hours: float = 24.0,
                bbox=(95.0, 120.0, -8.0, 8.0)):
    """A single wind vector everywhere (v1 semantics), on the LPM machinery.

    Used only when the request has no date. The wind may veer at a constant rate.
    The same speed and direction are applied at every level [J].
    """
    import numpy as np
    lon = np.arange(bbox[0] - 3.0, bbox[1] + 3.01, 1.5)
    lat = np.arange(bbox[2] - 3.0, bbox[3] + 3.01, 1.5)
    t0 = (start or datetime(2015, 9, 1, tzinfo=timezone.utc)) - timedelta(days=5)
    n_t = int(math.ceil((hours + 24 * 6) / 6.0)) + 1
    land = np.ones((len(lat), len(lon)), dtype=bool)
    ptype = _ptype_grid(np, lat, lon, land).astype(np.int64)
    fields = {k: np.zeros((n_t, len(lat), len(lon)), np.float32)
              for k in ("u_sfc", "v_sfc", "u_925", "v_925", "u_850", "v_850", "u10", "v10")}
    start_h = 24.0 * 5
    for k in range(n_t):
        t_h = 6.0 * k
        d = math.radians((wind_dir_deg + veer_deg_per_h * max(0.0, t_h - start_h)) % 360.0)
        u = -wind_speed_ms * math.sin(d)
        v = -wind_speed_ms * math.cos(d)
        for lev in ("sfc", "925", "850"):
            fields[f"u_{lev}"][k] = u
            fields[f"v_{lev}"][k] = v
        fields["u10"][k] = u * 0.7
        fields["v10"][k] = v * 0.7
    fields["hday"] = np.full((n_t, len(lat), len(lon)), float(mean_blh_m), np.float32)
    fields["rain"] = np.full((n_t, len(lat), len(lon)), max(0.0, rain_mm_day) / 24.0,
                             np.float32)
    return Met(np, lat, lon, t0, 6.0, fields, ptype, "uniform",
               "user-supplied single wind vector (no meteorology for this request)")


# --------------------------------------------------------------------------- #
# the particle model
# --------------------------------------------------------------------------- #

R_EARTH = 6.371e6
KIND_PEAT, KIND_FLAMING = 0, 1


ENV_KEYS = ("u_sfc", "v_sfc", "u_925", "v_925", "u_850", "v_850", "hday", "rain")
_FIELDS = ("x", "y", "z", "m", "age", "tag", "env", "H", "Hmax")


def _buf_prop(name):
    """Particle field stored in a preallocated buffer; reads/writes the live part.

    Growing arrays with np.concatenate every step left the process high-water mark
    ~4x the live data (allocator fragmentation); fixed buffers keep it flat.
    """
    def get(self):
        return self._b[name][:self.n]

    def put(self, value):
        self._b[name][:self.n] = value
    return property(get, put)


class LPM:
    """Particle state and the step function. Linear in emissions.

    Usage::

        lpm = LPM(met, dt_s=900, seed=1, t_start_h=...)
        each step:  lpm.release(lon, lat, mass_kg, kind, tag); lpm.step()
        outputs:    lpm.x, lpm.y, lpm.z, lpm.H (mixing height at each particle),
                    lpm.effective_mass(), lpm.age, lpm.tag

    The met fields at the current time are stacked once per step and gathered at
    the particles in one bilinear pass (``self.env``, columns in ENV_KEYS order).
    """

    x, y, z, m, age, tag, env, H, Hmax = (_buf_prop(f) for f in _FIELDS)

    def __init__(self, met: Met, dt_s: float = 900.0, seed: int | None = 1,
                 k_h: float | None = None, v_d: float | None = None,
                 lambda0: float | None = None, soa: bool = True,
                 t_start_h: float = 0.0, max_age_h: float = 240.0):
        import numpy as np
        self.np = np
        self.met = met
        self.dt = float(dt_s)
        self.t_h = float(t_start_h)                 # hours since met.t0
        self.rng = np.random.default_rng(seed)
        self.k_h = float(k_h if k_h is not None else param("horizontal_diffusivity", 1.0e4))
        self.v_d = float(v_d if v_d is not None else param("dry_dep_velocity", 0.0015))
        cal = calibration()
        self.lambda0 = float(lambda0 if lambda0 is not None
                             else cal.get("lambda0_per_s", 3.0e-5))
        self.wet_exp = param("wet_scav_exponent", 0.7)
        self.soa = bool(soa)
        ef_co = param("ef_co_peat", 255.0)
        ef_pm = param("ef_pm25_peat_central", 22.3)
        self.soa_ratio = param("soa_yield_per_co", 0.04) * ef_co / ef_pm      # 0.46 [D]
        self.soa_tau_h = 24.0 * param("soa_time_constant", 1.0)
        self.max_age_h = float(max_age_h)
        self.inj_cap_peat = param("injection_cap_peat", 700.0)
        self.inj_surface_frac = param("injection_surface_fraction", 0.5)
        self.veg_above_bl = param("veg_injection_above_bl", 500.0)
        self.veg_cap = param("veg_injection_cap", 2000.0)
        self.lon0, self.lon1 = float(met.lon[0]), float(met.lon[-1])
        self.lat0, self.lat1 = float(met.lat[0]), float(met.lat[-1])
        self.nx, self.ny = len(met.lon), len(met.lat)
        self.pmax = met.prof.max(axis=1)
        self.n = 0
        self._b = {}
        self._alloc(4096)
        self.removed_dry = 0.0
        self.removed_wet = 0.0
        self.exported = 0.0
        self.released = 0.0
        self._stack_t = None
        self._stack()

    def _alloc(self, cap):
        np = self.np
        old = self._b
        new = {}
        for f in _FIELDS:
            shape = (cap, len(ENV_KEYS)) if f == "env" else (cap,)
            dtype = np.int32 if f == "tag" else np.float64
            new[f] = np.zeros(shape, dtype)
            if old:
                new[f][:self.n] = old[f][:self.n]
        self._b = new
        self.cap = cap

    # -- environment -----------------------------------------------------
    def _stack(self):
        if self._stack_t == self.t_h:
            return
        np = self.np
        F = self.met.slice_at(self.t_h)
        self._S = np.stack([F[k] for k in ENV_KEYS], axis=-1).reshape(-1, len(ENV_KEYS))
        self._utc = (self.met.t0.hour + self.t_h) % 24.0
        self._stack_t = self.t_h

    def _env_at(self, lon, lat):
        """(env, H, Hmax) at positions: one stacked bilinear gather."""
        np = self.np
        met = self.met
        fx = np.clip((lon - self.lon0) / met.dlon, 0.0, self.nx - 1.000001)
        fy = np.clip((lat - self.lat0) / met.dlat, 0.0, self.ny - 1.000001)
        ix = fx.astype(np.int64)
        iy = fy.astype(np.int64)
        wx = (fx - ix)[:, None]
        wy = (fy - iy)[:, None]
        i00 = iy * self.nx + ix
        S = self._S
        env = ((S[i00] * (1 - wx) + S[i00 + 1] * wx) * (1 - wy)
               + (S[i00 + self.nx] * (1 - wx) + S[i00 + self.nx + 1] * wx) * wy)
        pt = met.ptype[iy + (wy[:, 0] > 0.5), ix + (wx[:, 0] > 0.5)]
        solar = (self._utc + lon / 15.0) % 24.0
        clock = (solar - met.prof_off[pt]) % 24.0
        i0 = clock.astype(np.int64) % 24
        f = clock - np.floor(clock)
        prof = met.prof
        pf = prof[pt, i0] * (1 - f) + prof[pt, (i0 + 1) % 24] * f
        hd = env[:, 6]
        H = np.clip(hd * pf, BLH_FLOOR_M, BLH_CEIL_M)
        Hmax = np.clip(hd * self.pmax[pt], BLH_FLOOR_M, BLH_CEIL_M)
        return env, H, Hmax

    def _refresh(self):
        self._stack()
        if self.n:
            env, H, Hmax = self._env_at(self.x, self.y)
            self.env = env
            self.H = H
            self.Hmax = Hmax

    def blh_at(self, lon, lat):
        self._stack()
        _, H, Hmax = self._env_at(self.np.asarray(lon, float), self.np.asarray(lat, float))
        return H, Hmax

    # -- sources --------------------------------------------------------
    def release(self, lon, lat, mass_kg, kind, tag=None):
        """Add particles at the current time with the spec's injection profiles."""
        np = self.np
        lon = np.asarray(lon, float)
        lat = np.asarray(lat, float)
        mass = np.asarray(mass_kg, float)
        n = len(lon)
        if n == 0:
            return
        self._stack()
        kind = np.broadcast_to(np.asarray(kind, int), (n,))
        env, H, Hmax = self._env_at(lon, lat)
        u = self.rng.random(n)
        # peat: 50% at 0-70 m, 50% uniform over 0-min(H, 700)   (Kiely; Lee 2017)
        top_peat = np.minimum(H, self.inj_cap_peat)
        surf = self.rng.random(n) < self.inj_surface_frac
        z_peat = np.where(surf, u * 70.0, u * top_peat)
        # flaming vegetation: uniform over 0-(H + 500), capped at 2,000 m  (Tosca 2011)
        z_veg = u * np.minimum(H + self.veg_above_bl, self.veg_cap)
        z = np.where(kind == KIND_PEAT, z_peat, z_veg)
        t = np.zeros(n, dtype=np.int32) if tag is None else \
            np.broadcast_to(np.asarray(tag, np.int32), (n,))
        if self.n + n > self.cap:
            self._alloc(max(2 * self.cap, self.n + n))
        a, b = self.n, self.n + n
        B = self._b
        B["x"][a:b], B["y"][a:b], B["z"][a:b], B["m"][a:b] = lon, lat, z, mass
        B["age"][a:b], B["tag"][a:b] = 0.0, t
        B["env"][a:b], B["H"][a:b], B["Hmax"][a:b] = env, H, Hmax
        self.n = b
        self.released += float(mass.sum())

    # -- dynamics --------------------------------------------------------
    def step(self):
        np = self.np
        dt = self.dt
        if len(self.x):
            env, H, Hmax = self.env, self.H, self.Hmax
            in1 = self.z <= H
            in3 = self.z > Hmax
            solar = (self._utc + self.x / 15.0) % 24.0
            day = (solar >= 7.0) & (solar < 18.0)
            us, vs, u9, v9, u8, v8 = (env[:, k] for k in range(6))
            u = np.where(in1, np.where(day, 0.5 * (us + u9), us), np.where(in3, u8, u9))
            v = np.where(in1, np.where(day, 0.5 * (vs + v9), vs), np.where(in3, v8, v9))
            sig = math.sqrt(2.0 * self.k_h * dt)
            n = len(self.x)
            rnd = self.rng.standard_normal((2, n))
            dx = u * dt + sig * rnd[0]
            dy = v * dt + sig * rnd[1]
            self.x = self.x + np.degrees(dx / (R_EARTH * np.cos(np.radians(self.y))))
            self.y = self.y + np.degrees(dy / R_EARTH)
            # removal (first order over the step)
            rain = env[:, 7]                                  # mm/h
            lam_w = np.where((rain > 0.1) & (self.z < 5000.0),
                             self.lambda0 * np.power(np.maximum(rain, 1e-9), self.wet_exp), 0.0)
            lam_d = np.where(in1, self.v_d / np.maximum(H, 1.0), 0.0)
            lam = lam_w + lam_d
            loss = self.m * (1.0 - np.exp(-lam * dt))
            wet_part = np.where(lam > 0, lam_w / np.where(lam > 0, lam, 1.0), 0.0)
            lw = float((loss * wet_part).sum())
            self.removed_wet += lw
            self.removed_dry += float(loss.sum()) - lw
            self.m = self.m - loss
            self.age = self.age + dt / 3600.0

        self.t_h += dt / 3600.0
        if len(self.x):
            out = ((self.x < self.lon0) | (self.x > self.lon1)
                   | (self.y < self.lat0) | (self.y > self.lat1)
                   | (self.age > self.max_age_h))
            if out.any():
                self.exported += float(self.m[out].sum())
                keep = ~out
                k = int(keep.sum())
                for f in ("x", "y", "z", "m", "age", "tag"):
                    self._b[f][:k] = self._b[f][:self.n][keep]
                self.n = k
        self._refresh()
        # vertical exchange: L1 is well mixed in [0, H]; particles left above a
        # falling H stay where they are (they are L2 now); L2 particles that a
        # rising H has caught are L1 and get mixed.
        if len(self.x):
            in1 = self.z <= self.H
            self.z = np.where(in1, self.rng.random(len(self.z)) * self.H, self.z)

    # -- outputs ---------------------------------------------------------
    def effective_mass(self):
        """Primary mass, plus SOA formed so far when the SOA toggle is on."""
        np = self.np
        if not self.soa:
            return self.m
        return self.m * (1.0 + self.soa_ratio * (1.0 - np.exp(-self.age / self.soa_tau_h)))

    def burden(self):
        return float(self.m.sum())


# --------------------------------------------------------------------------- #
# source-receptor matrix (browser / scenario path: matrix arithmetic only)
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=1)
def _srm():
    import numpy as np
    from app.data.loader import earth_path
    p = earth_path("srm.npz")
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def srm_info() -> dict | None:
    """Receptor ids, source tags (region x peat/non-peat), months and years of the SRM."""
    z = _srm()
    if z is None:
        return None
    return {"receptor_ids": [str(x) for x in z["receptor_ids"]],
            "tag_region": [str(x) for x in z["tag_region"]],
            "tag_kind": [str(x) for x in z["tag_kind"]],
            "months": [int(m) for m in z["months"]], "n_lag": int(z["n_lag"]),
            "years": [int(y) for y in z["years"]], "units": str(z["units"])}


def srm_apply(emissions_kg_day, first_day, year: int | None = None):
    """C_r(day) = sum_s sum_lag M[r, s, month(day), lag] * E_s(day - lag)   (spec 5.4).

    ``emissions_kg_day``: (n_days, n_tags) kg/day by source tag, starting ``first_day``.
    ``year``: use that season's matrix (its own winds); None = the multi-year mean.
    Returns (n_days, n_receptors) fire PM2.5 in ug/m3 (primary, no background).
    Linear in emissions, so it answers "what if region X burned half as much" with
    matrix arithmetic. Days outside Jul-Nov use the nearest month's matrix.
    """
    import numpy as np
    z = _srm()
    if z is None:
        return None
    M = z[f"M_{year}"] if year is not None and f"M_{year}" in z else z["M_mean"]
    E = np.asarray(emissions_kg_day, float)
    months = [int(m) for m in z["months"]]
    n_days, n_lag = E.shape[0], int(z["n_lag"])
    out = np.zeros((n_days, M.shape[0]))
    for d in range(n_days):
        day = first_day + timedelta(days=d)
        mi = months.index(day.month) if day.month in months else (0 if day.month < months[0] else len(months) - 1)
        for lag in range(n_lag):
            if d - lag < 0:
                break
            out[d] += M[:, :, mi, lag] @ E[d - lag]
    return out
