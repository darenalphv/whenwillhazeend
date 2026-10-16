"""Health impact of haze: published estimates as ranges (M1), and a gated per-scenario
GEMM / short-term estimate (M2). MODEL_SPEC section 7.

M1 (always on). Published estimates of deaths attributable to the 2015 haze run from
about 12,000 to 100,000. They differ mainly in how they convert smoke exposure into
risk (Kiely et al. 2020 show this directly: their exposure with Koplitz's function
gives 106,000 instead of 44,040). The site shows the range and the metric of each,
never a single figure.

M2 (OFF). A per-scenario estimate needs three inputs this site does not have in a
verified form:

1. baseline NCD+LRI mortality rates for adults 25+ by country (GBD 2019/2021) -- listed
   UNVERIFIED in research/PARAMETERS.json (``baseline_mortality_ncd_lri`` has no value),
   and the IHME licence for redistributing them has not been checked;
2. gridded population aged 25+ (not bundled; e.g. WorldPop or GPW age structure);
3. daily all-cause deaths per cell for the short-term (Atkinson) estimate -- source
   UNVERIFIED (WHO/GBD).

The functions are implemented and unit-tested on synthetic inputs, but
:func:`scenario_estimate` refuses to produce a number while ``SCENARIO_ENABLED`` is
False, and says which input is missing. Do not switch it on until (1)-(3) are sourced.

GEMM (Burnett et al. 2018, PNAS 115:9592), NCD+LRI, age 25+, GEMM Calculator workbook::

    z = max(0, PM2.5 - 2.4)
    HR(z) = exp( theta * log(1 + z/alpha) / (1 + exp(-(z - mu)/nu)) )
    with China:    theta 0.1430 (SE 0.01807), alpha 1.6, mu 15.5, nu 36.8
    without China: theta 0.1231 (SE 0.01797), alpha 1.5, mu 10.4, nu 25.9

    AF = 1 - HR(C_bg) / HR(C_bg + C_fire_annual)       (C_bg 10-15 ug/m3, Koplitz)

Short-term (Atkinson et al. 2014, Thorax 69:660): all-cause +1.04% (0.52-1.56%) per
10 ug/m3 of daily PM2.5::

    deaths_st = sum_days D_daily * (1 - exp(-beta * C_fire(day))),  beta = 0.00104

Both are always shown labelled and never summed.
"""

from __future__ import annotations

import math

__all__ = ["SCENARIO_ENABLED", "GEMM_PARAMS", "gemm_hr", "gemm_attributable_fraction",
           "shortterm_deaths", "published_estimates", "scenario_estimate", "MISSING_INPUTS"]

#: Keep False until every input in MISSING_INPUTS is sourced and verified.
SCENARIO_ENABLED = False

MISSING_INPUTS = [
    {"input": "Baseline NCD+LRI mortality rate, adults 25+, by country",
     "needed_for": "GEMM long-term estimate",
     "status": "UNVERIFIED: GBD 2019/2021 values not downloaded or checked; IHME redistribution licence not checked",
     "parameter_id": "baseline_mortality_ncd_lri"},
    {"input": "Gridded population aged 25+ over Indonesia, Malaysia and Singapore",
     "needed_for": "GEMM long-term estimate",
     "status": "not bundled (e.g. WorldPop / GPW age structure; licence to check)"},
    {"input": "Daily all-cause deaths per cell",
     "needed_for": "Atkinson short-term estimate",
     "status": "UNVERIFIED source (WHO / GBD)"},
]

GEMM_PARAMS = {
    "with_china": {"theta": 0.1430, "se": 0.01807, "alpha": 1.6, "mu": 15.5, "nu": 36.8},
    "without_china": {"theta": 0.1231, "se": 0.01797, "alpha": 1.5, "mu": 10.4, "nu": 25.9},
    "cf_ugm3": 2.4,
    "source": "Burnett et al. 2018, PNAS 115:9592; GEMM Calculator workbook, NCD+LRI, age 25+ [V]",
}
ATKINSON_BETA = (0.00104, 0.00052, 0.00156)      # central, low, high per ug/m3 [V]


def gemm_hr(pm25: float, variant: str = "with_china", theta_sd: float = 0.0) -> float:
    """GEMM hazard ratio at an annual-mean PM2.5 (ug/m3). theta_sd shifts theta by k SE."""
    p = GEMM_PARAMS[variant]
    theta = p["theta"] + theta_sd * p["se"]
    z = max(0.0, float(pm25) - GEMM_PARAMS["cf_ugm3"])
    return math.exp(theta * math.log1p(z / p["alpha"]) / (1.0 + math.exp(-(z - p["mu"]) / p["nu"])))


def gemm_attributable_fraction(c_bg: float, c_fire_annual: float, variant: str = "with_china",
                               theta_sd: float = 0.0) -> float:
    """Fraction of NCD+LRI deaths attributable to the fire increment (spec M2)."""
    return 1.0 - gemm_hr(c_bg, variant, theta_sd) / gemm_hr(c_bg + c_fire_annual, variant, theta_sd)


def shortterm_deaths(daily_deaths: float, fire_pm25_daily, beta: float = ATKINSON_BETA[0]) -> float:
    """Atkinson 2014 short-term deaths for one cell: sum_d D * (1 - exp(-beta C_d))."""
    return float(sum(daily_deaths * (1.0 - math.exp(-beta * max(0.0, c))) for c in fire_pm25_daily))


_PUBLISHED = {
    2015: [
        {"value": 11880, "low": 6153, "high": 17270, "region": "ID+MY+SG",
         "source": "Crippa et al. 2016", "doi": "10.1038/srep37074",
         "metric": "Short-term (daily) exposure, Sep-Oct 2015, all-cause (Atkinson 2014 CRF)"},
        {"value": 44040, "low": 34672, "high": 53948, "region": "ID+MY+SG",
         "source": "Kiely et al. 2020", "doi": "10.1088/1748-9326/ab9a6c",
         "metric": "Long-term exposure function (GEMM) applied to the 2015 fire-season PM"},
        {"value": 75600, "low": None, "high": None, "region": "ID+MY+SG",
         "source": "Crippa et al. 2016", "doi": "10.1038/srep37074",
         "metric": "As if the Sep-Oct exposure were sustained long-term (IER)"},
        {"value": 100300, "low": 26300, "high": 174300, "region": "ID+MY+SG",
         "source": "Koplitz et al. 2016", "doi": "10.1088/1748-9326/11/9/094023",
         "metric": "Long-term function, Jul-Oct exposure, adults",
         "interval_note": "Sum of the authors' country 95% intervals [D]; the authors state no total range."},
    ],
    2006: [
        {"value": 37600, "low": None, "high": None, "region": "ID+MY+SG",
         "source": "Koplitz et al. 2016", "doi": "10.1088/1748-9326/11/9/094023",
         "metric": "Long-term function, Jul-Oct exposure"},
        {"value": 22100, "low": None, "high": None, "region": "ID+MY+SG",
         "source": "Kiely et al. 2020", "doi": "10.1088/1748-9326/ab9a6c",
         "metric": "GEMM, annual-mean fire PM2.5 increment"},
    ],
}
_CONTEXT = [
    {"value": 33100, "low": 19900, "high": 64800, "source": "Hein et al. 2022",
     "metric": "Adults, per AVERAGE fire year, Sumatra + Kalimantan exposure"},
    {"value": 10800, "low": 6800, "high": 14300, "source": "Marlier et al. 2013",
     "metric": "Adult cardiovascular deaths only, per strong El Nino year (1997-2006)"},
]


def _koplitz_kiely_note():
    return ("Estimates differ mainly in the concentration-response function, then in the "
            "modelled exposure: Kiely et al. 2020 report 106,000 deaths when Koplitz's function "
            "is applied to their own exposure, against 44,040 with GEMM.")


def published_estimates(year: int = 2015) -> dict | None:
    """M1: published deaths for a haze year as a CONTRACT_V2 range object, or None."""
    est = _PUBLISHED.get(int(year))
    if not est:
        return None
    vals = [e["value"] for e in est]
    out = {
        "low": min(vals), "high": max(vals), "central": None,
        "estimates": [dict(e) for e in est],
        "note": _koplitz_kiely_note(),
        "headline": ("Published estimates of deaths attributable to the {y} haze range from "
                     "about {lo:,} to {hi:,}. They differ mainly in how they convert smoke "
                     "exposure into risk.").format(y=year, lo=_round_sig(min(vals)),
                                                   hi=_round_sig(max(vals))),
        "never_sum": "Short-term and long-term estimates count different things; never add them.",
        "context": _CONTEXT,
    }
    return out


def _round_sig(v):
    mag = 10 ** max(0, int(math.log10(max(v, 1))) - 1)
    return int(round(v / mag) * mag)


def scenario_estimate(c_fire_annual=None, population_25plus=None, baseline_rate=None,
                      c_bg: float = 12.5, daily_deaths=None, fire_pm25_daily=None,
                      force: bool = False) -> dict:
    """M2 per-scenario estimate. Gated: returns the missing inputs while disabled.

    With ``force=True`` (tests only) and caller-supplied inputs, returns GEMM (with and
    without the China cohort, theta +/- 2 SE) and the Atkinson short-term estimate.
    """
    if not (SCENARIO_ENABLED or force):
        return {"enabled": False, "missing_inputs": MISSING_INPUTS,
                "reason": ("A per-scenario mortality estimate needs baseline mortality rates "
                           "and an age-structured population that are not verified in this "
                           "build; the published range is shown instead.")}
    out = {"enabled": True, "long_term_gemm": None, "short_term_atkinson": None,
           "c_bg_ugm3": c_bg}
    if c_fire_annual is not None and population_25plus is not None and baseline_rate is not None:
        res = {}
        for variant in ("with_china", "without_china"):
            for k, sd in (("central", 0.0), ("low", -2.0), ("high", 2.0)):
                tot = 0.0
                for c, pop in zip(c_fire_annual, population_25plus):
                    tot += pop * baseline_rate * gemm_attributable_fraction(c_bg, c, variant, sd)
                res[f"{variant}_{k}"] = round(tot)
        out["long_term_gemm"] = res
    if daily_deaths is not None and fire_pm25_daily is not None:
        out["short_term_atkinson"] = {
            name: round(sum(shortterm_deaths(d, series, b)
                            for d, series in zip(daily_deaths, fire_pm25_daily)))
            for name, b in zip(("central", "low", "high"), ATKINSON_BETA)}
    return out
