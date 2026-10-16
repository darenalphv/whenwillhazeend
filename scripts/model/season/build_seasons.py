"""Apply the MODEL_SPEC section 0.4 / 2 / 3 / 7 corrections to app/data/seasons.json.

Idempotent: re-running it re-derives every corrected field from the inputs.

Inputs
  app/data/seasons.json      (v1 rows; v1 keys are kept for compatibility)
  app/data/enso.json         (per-row `oni_seasons`, from fetch_indices.py)
  app/data/iod.json          (DMI seasons, from fetch_indices.py)
  scripts/model/season/data/firms_daily_modis.json  (fire-observed labels)

What it changes (MODEL_SPEC 0.4 item numbers in brackets)
  * start/peak/end/duration for 2001-2025 -> fire-observed labels (S1); the v1
    rainfall-rule dates move to `rainfall_rule_dates`; `target_source` added.
    1997-2000 keep rainfall-rule dates, flagged.                       [8]
  * `enso_phase` from the burn-window ONI (mean of JJA/JAS/ASO), not the
    post-season Aug-Nov peak; `oni_burn_window` added.                 [4]
  * `iod_phase`, `dmi_son`, `dmi_peak` added.                          [4]
  * `singapore_peaks` object; `psi_peak_singapore` keeps only 24-h PSI. [1]
  * Palangka Raya PM2.5 attributed to Santoso et al. 2022.             [3]
  * `excess_deaths_range` (2015, 2006) with all published estimates.   [6]
  * `burned_area_mha_{indonesia,kalimantan}_range` where >1 estimate.  (S3 B1)
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SEASONS = ROOT / "app/data/seasons.json"
ENSO = ROOT / "app/data/enso.json"
IOD = ROOT / "app/data/iod.json"

ONI_THRESHOLD = 0.5

FIRE_DATES_SOURCE = (
    "Season dates: FIRE-OBSERVED, NASA FIRMS MODIS C6.1 active-fire counts in the "
    "4S-0, 110-117E peat-belt box (Field et al. 2016), labelled by this site's rule "
    "(see notes_method)")
FIRE_NOTES_METHOD = (
    "Season dates are fire-observed: daily NASA FIRMS MODIS (Terra+Aqua, C6.1, type 0) "
    "detections inside 4S-0, 110-117E, from the FIRMS yearly Indonesia archive. "
    "start = first day after 1 May on which the centred 7-day count exceeds 10% of the "
    "season's maximum 7-day count and stays above it for 7 days; peak = day of the "
    "maximum 7-day count; end = first day after the peak from which the 7-day count stays "
    "below 10% of the maximum for 14 consecutive days. Years whose maximum is below the "
    "10th percentile of 2001-2025 maxima have no season. This is the site's own "
    "definition (MODEL_SPEC S1, engineering judgement); no published definition exists. "
    "The previous rainfall-rule dates are kept in rainfall_rule_dates.")
RAIN_NOTES_METHOD_SUFFIX = (
    " These are DERIVED climatological burn-window dates, not observed fire dates; MODIS "
    "fire data start in Nov 2000, so 1997-2000 cannot be fire-labelled. Excluded from the "
    "season model's skill metrics (target_source = rainfall_rule).")

SG_PEAKS = {
    2015: {"psi_24h_max": 322, "pm25_1h_max_ugm3": 471, "psi_3h_max": None,
           "psi_24h_max_when": "2015-09-25 08:00 SGT, South region",
           "pm25_1h_max_when": "2015-10-19 23:00 SGT, West region",
           "source": "NEA Singapore historical 24-hr PSI and 1-hr PM2.5 datasets, data.gov.sg "
                     "(Singapore Open Data Licence v1.0). The 471 is a 1-hour PM2.5 "
                     "concentration in ug/m3, not a PSI.",
           "verified": True},
    2013: {"psi_24h_max": None, "pm25_1h_max_ugm3": None, "psi_3h_max": 401,
           "source": "3-hour PSI of 401 (21 Jun 2013) as reported by the press; NEA's pre-2014 "
                     "data are not on data.gov.sg, so this is UNVERIFIED at source.",
           "verified": False},
    1997: {"psi_24h_max": None, "pm25_1h_max_ugm3": None, "psi_3h_max": None,
           "unverified_value": 226,
           "source": "A Singapore peak of 226 is widely quoted for 1997, but its metric and "
                     "averaging period are UNVERIFIED, so it is not assigned to any field.",
           "verified": False},
    2006: {"psi_24h_max": None, "pm25_1h_max_ugm3": None, "psi_3h_max": None,
           "unverified_value": 150,
           "source": "A Singapore peak of 150 is widely quoted for 2006, but its metric and "
                     "averaging period are UNVERIFIED, so it is not assigned to any field.",
           "verified": False},
}

DEATHS_2015 = {
    "low": 11880, "high": 100300, "central": None, "unit": "deaths",
    "estimates": [
        {"value": 11880, "interval": [6153, 17270], "region": "ID+MY+SG",
         "metric": "short-term (daily) exposure, Sep-Oct, all-cause (Atkinson 2014 CRF)",
         "source": "Crippa et al. 2016, Sci. Rep. 6:37074", "doi": "10.1038/srep37074"},
        {"value": 44040, "interval": [34672, 53948], "region": "ID+MY+SG",
         "metric": "long-term exposure function (GEMM, NCD+LRI, adults) on the 2015 fire-season PM",
         "source": "Kiely et al. 2020, Environ. Res. Lett. 15:094054", "doi": "10.1088/1748-9326/ab9a6c"},
        {"value": 75600, "interval": None, "region": "ID+MY+SG",
         "metric": "as if the Sep-Oct exposure were sustained long-term (IER)",
         "source": "Crippa et al. 2016, Sci. Rep. 6:37074", "doi": "10.1038/srep37074"},
        {"value": 100300, "interval": [26300, 174300], "region": "ID+MY+SG",
         "metric": "long-term exposure function, Jul-Oct exposure, adults",
         "interval_note": "sum of the authors' country 95% intervals (ID 24,000-159,200; "
                          "MY 1,700-11,300; SG 600-3,800); the authors state no total interval",
         "source": "Koplitz et al. 2016, Environ. Res. Lett. 11:094023",
         "doi": "10.1088/1748-9326/11/9/094023"},
    ],
    "note": ("Published estimates of deaths attributable to the 2015 haze range from about "
             "12,000 to 100,000. They differ mainly in how they convert smoke exposure into "
             "risk (the exposure-response function): applying Koplitz's function to Kiely's "
             "smoke gives 106,000 (Kiely et al. 2020). Short-term and long-term estimates "
             "count different things and must never be added. Koplitz's exposure window is "
             "July-October despite the paper's September-October title."),
}
DEATHS_2006 = {
    "low": 22100, "high": 37600, "central": None, "unit": "deaths",
    "estimates": [
        {"value": 37600, "region": "ID+MY+SG", "metric": "long-term exposure function",
         "source": "Koplitz et al. 2016, Environ. Res. Lett. 11:094023",
         "doi": "10.1088/1748-9326/11/9/094023"},
        {"value": 22100, "region": "ID+MY+SG", "metric": "long-term exposure function (GEMM)",
         "source": "Kiely et al. 2020, Environ. Res. Lett. 15:094054",
         "doi": "10.1088/1748-9326/ab9a6c"},
    ],
    "note": "Estimates differ mainly in the exposure-response function and the modelled exposure.",
}

BA_IDN = {
    2015: {"low": 2.61, "high": 4.60, "central": None, "unit": "Mha", "estimates": [
        {"value": 2.61, "source": "KLHK SiPongi (via GoodStats compilation)", "method": "Landsat, official"},
        {"value": 4.60, "source": "Lohberger et al. 2018, Glob. Change Biol. 24:644",
         "doi": "10.1111/gcb.13841", "method": "Sentinel-1 SAR (Sumatra, Kalimantan, West Papua)"}],
        "note": "central is null: no GFED5 value has been extracted for 2015 (MODEL_SPEC B1)."},
    2019: {"low": 1.65, "high": 3.8, "central": 3.8, "unit": "Mha", "estimates": [
        {"value": 1.65, "source": "KLHK SiPongi (via GoodStats compilation)", "method": "Landsat, official"},
        {"value": 2.04, "source": "Gaveau et al. 2021, ESSD 13:5353 (also 2.09 Gaveau 2026; 2.2 Chen 2023)",
         "doi": "10.5194/essd-13-5353-2021", "method": "MODIS MCD64A1"},
        {"value": 2.64, "source": "Gaveau et al. 2026, PLoS ONE 21:e0331831",
         "doi": "10.1371/journal.pone.0331831", "method": "Sentinel-2 monthly"},
        {"value": 3.11, "source": "Gaveau et al. 2021, ESSD 13:5353 (stated as >3.11)",
         "doi": "10.5194/essd-13-5353-2021", "method": "Sentinel-2 annual"},
        {"value": 3.8, "source": "Chen et al. 2023, ESSD 15:5227 (GFED5)",
         "doi": "10.5194/essd-15-5227-2023", "method": "GFED5 (MODIS-based, small-fire corrected)"}],
        "note": "central = GFED5 for consistency across years (MODEL_SPEC B1). The official map's "
                "producer's accuracy was 49.5% in 2019 (Gaveau et al. 2021)."},
    2023: {"low": 1.11, "high": 1.70, "central": None, "unit": "Mha", "estimates": [
        {"value": 1.16, "source": "KLHK SiPongi (via GoodStats compilation)", "method": "Landsat, official"},
        {"value": 1.11, "source": "Gaveau et al. 2026, PLoS ONE 21:e0331831",
         "doi": "10.1371/journal.pone.0331831", "method": "MODIS MCD64A1"},
        {"value": 1.70, "source": "Gaveau et al. 2026, PLoS ONE 21:e0331831",
         "doi": "10.1371/journal.pone.0331831", "method": "Sentinel-2 monthly"}],
        "note": "central is null: no GFED5 value has been extracted for 2023 (MODEL_SPEC B1)."},
}
BA_KAL = {
    2015: {"low": 0.78, "high": None, "central": None, "unit": "Mha", "estimates": [
        {"value": 0.78, "source": "KLHK provincial recaps, Kalimantan Tengah + Kalimantan Selatan only",
         "method": "Landsat, official; LOWER BOUND (Kalbar/Kaltim/Kaltara not located)"}],
        "note": "No verified Kalimantan-only satellite estimate was located. For Indonesia as a "
                "whole the satellite estimate is 1.8x the official one (4.60 vs 2.61 Mha), so the "
                "true Kalimantan figure is very probably higher; high is null rather than guessed."},
    2019: {"low": 0.627, "high": None, "central": None, "unit": "Mha", "estimates": [
        {"value": 0.627, "source": "Sum of KLHK per-province recaps (Kalbar, Kalsel, Kalteng, Kaltim)",
         "method": "Landsat, official"}],
        "note": "No verified Kalimantan-only satellite estimate was located. For Indonesia the "
                "satellite estimates run 1.2-2.3x the official figure in 2019; high is null "
                "rather than guessed."},
}


def _phase(v):
    if v is None:
        return None
    return "el_nino" if v >= ONI_THRESHOLD else ("la_nina" if v <= -ONI_THRESHOLD else "neutral")


def _replace_source_part(src: str, startswith: str, new: str | None) -> str:
    parts = [p.strip() for p in src.split("; ") if p.strip()]
    out, done = [], False
    for p in parts:
        if p.startswith(startswith):
            if new and not done:
                out.append(new)
            done = True
        else:
            out.append(p)
    if new and not done:
        out.append(new)
    return "; ".join(out)


def main():
    from app.model import calibrate as cal
    rows = json.loads(SEASONS.read_text())
    enso = {r["year"]: r for r in json.loads(ENSO.read_text())}
    iod = json.loads(IOD.read_text())["seasons"]
    labels = {l["year"]: l for l in cal.fire_labels()}

    for s in rows:
        y = s["year"]
        # ---- season dates ------------------------------------------------
        if "rainfall_rule_dates" not in s:
            s["rainfall_rule_dates"] = {k: s.get(k) for k in
                                        ("start_date", "peak_date", "end_date", "duration_days")}
            s["rainfall_rule_dates"]["method"] = s.get("notes_method")
        lab = labels.get(y)
        if lab is not None:
            s["target_source"] = "firms_modis"
            s["fire_counts"] = {"modis_box_max_7d": int(lab["max_7d"]),
                                "modis_box_year_total": lab["season_total"],
                                "no_season_floor_7d": round(lab["floor_7d"], 1),
                                "box": "4S-0, 110-117E", "sensor": "MODIS Terra+Aqua C6.1"}
            if lab["status"] == "season":
                s["start_date"], s["peak_date"], s["end_date"] = lab["start"], lab["peak"], lab["end"]
                s["duration_days"] = lab["duration"]
            else:
                s["start_date"] = s["peak_date"] = s["end_date"] = None
                s["duration_days"] = 0
            s["notes_method"] = FIRE_NOTES_METHOD
            s["source"] = _replace_source_part(s["source"], "Season dates:", FIRE_DATES_SOURCE)
        else:
            s["target_source"] = "rainfall_rule"
            nm = s["rainfall_rule_dates"].get("method") or ""
            s["notes_method"] = nm.split(" These are DERIVED")[0] + RAIN_NOTES_METHOD_SUFFIX

        # ---- ENSO / IOD --------------------------------------------------
        e = enso.get(y, {})
        bw = e.get("oni_burn_window")
        s["oni_burn_window"] = bw
        s["enso_phase"] = _phase(bw) or s.get("enso_phase")
        s["enso_phase_basis"] = ("burn-window ONI: mean of the JJA, JAS and ASO seasons, "
                                 "+/-0.5 C (NOAA CPC ERSSTv5)")
        s["oni_peak_note"] = ("oni_peak is the largest-magnitude ONI over JAS-OND and includes "
                              "post-season months; it is not the burn-window phase.")
        io = iod.get(str(y)) or {}
        s["dmi_son"] = io.get("dmi_son")
        s["dmi_peak"] = io.get("dmi_peak")
        s["iod_phase"] = io.get("iod_phase")
        s["source"] = _replace_source_part(
            s["source"], "ONI:", "ONI: NOAA CPC ERSSTv5; enso_phase from the burn-window mean "
            "of JJA/JAS/ASO; oni_peak = max|ONI| over JAS/ASO/SON/OND")
        s["source"] = _replace_source_part(
            s["source"], "DMI:", "DMI: NOAA OSMC weekly OISST Dipole Mode Index (1991-2020 base); "
            "iod_phase from the SON mean at +/-0.4 C (BoM convention, UNVERIFIED)")

        # ---- Singapore peaks [0.4 item 1] -----------------------------------
        sp = SG_PEAKS.get(y)
        if sp is not None:
            s["singapore_peaks"] = sp
        else:
            s["singapore_peaks"] = None
        s["psi_peak_singapore"] = (sp or {}).get("psi_24h_max")
        s["psi_peak_singapore_metric"] = "24-hour PSI (NEA); null where no verified 24-h value"
        s["source"] = _replace_source_part(s["source"], "Singapore 3-hour PSI peak",
                                           None)
        if sp is not None:
            s["source"] = _replace_source_part(
                s["source"], "Singapore peaks:", "Singapore peaks: see singapore_peaks.source")

        # ---- Palangka Raya PM2.5 [0.4 item 3] -------------------------------
        if s.get("pm25_peak_ugm3"):
            s["pm25_peak_metric"] = "24-hour filter sample (Palangka Raya)"
            s["source"] = _replace_source_part(
                s["source"], "Palangka Raya PM2.5:",
                "Palangka Raya PM2.5: Santoso, Hopke et al. 2022, J. Air Waste Manage. Assoc. "
                "72(11):1191-1200, doi:10.1080/10962247.2022.2077474 ('nearly 400' ug/m3, 24-h "
                "filter sample; previously misattributed to Hayasaka et al.)")

        # ---- deaths, burned-area ranges ----------------------------------
        if y == 2015:
            s["excess_deaths_range"] = DEATHS_2015
            s["source"] = _replace_source_part(
                s["source"], "Excess deaths:",
                "Excess deaths: excess_deaths_est = Koplitz et al. 2016 central estimate "
                "(ERL 11:094023), the highest of four published estimates; see excess_deaths_range")
        elif y == 2006:
            s["excess_deaths_range"] = DEATHS_2006
            s["source"] = _replace_source_part(
                s["source"], "Excess deaths:",
                "Excess deaths: excess_deaths_est = Koplitz et al. 2016 (ERL 11:094023); "
                "Kiely et al. 2020 give 22,100; see excess_deaths_range")
        if y in BA_IDN:
            s["burned_area_mha_indonesia_range"] = BA_IDN[y]
        if y in BA_KAL:
            s["burned_area_mha_kalimantan_range"] = BA_KAL[y]

        # ---- notes text that repeated the errors ---------------------------
        n = s.get("notes") or ""
        n = n.replace("Singapore 3-hour PSI peaked at 471 on 20 Oct 2015.",
                      "Singapore's 24-hour PSI peaked at 322 (25 Sep 2015) and 1-hour PM2.5 at "
                      "471 ug/m3 (19 Oct 2015); 471 is not a PSI.")
        n = n.replace("Koplitz et al. (2016): ~100,300 excess deaths region-wide (Indonesia 91,600; "
                      "Malaysia 6,500; Singapore 2,200).",
                      "Published estimates of attributable deaths range from about 12,000 to "
                      "100,000 depending mainly on the exposure-response function (see "
                      "excess_deaths_range); Koplitz et al. (2016): ~100,300 (Indonesia 91,600; "
                      "Malaysia 6,500; Singapore 2,200).")
        n = n.replace("Singapore's 3-hour PSI peaked at 226.",
                      "A Singapore peak of 226 is often quoted; its metric is unverified.")
        n = n.replace("Weak El Nino but a strong positive Indian Ocean Dipole, which the ONI alone "
                      "does not capture.",
                      "ENSO-neutral during the burn window (JJA-ASO ONI 0.30; the OND value of 0.72 "
                      "came after the season) with an extreme positive Indian Ocean Dipole "
                      "(Sep-Nov DMI 4 standard deviations above normal, Ratna et al. 2021).")
        n = n.replace("Weak-to-moderate El Nino; the last major",
                      "ENSO-neutral in the burn window (JJA-ASO ONI 0.33; El Nino developed from "
                      "October) with a positive IOD; the last major")
        s["notes"] = n

    SEASONS.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print("updated", len(rows), "seasons")
    for s in rows:
        print(s["year"], s["target_source"], s["enso_phase"], s["iod_phase"], s["start_date"],
              s["end_date"], s["duration_days"], s["psi_peak_singapore"])


if __name__ == "__main__":
    main()
