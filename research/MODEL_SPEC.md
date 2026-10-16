# whenwillhazeend.com: scientific model specification for the rebuild

Status: research specification, 2026-10-16. Companion files:

- `research/PARAMETERS.json` lists every numeric parameter used in this spec.
- `research/references.bib` holds the BibTeX for every source cited.
- `research/data/era5_blh_summary.{json,txt}` and `research/data/wind_850_925_summary.txt` are derived data computed for this spec.
- `research/scripts/` holds the scripts that produced them.

Who this is for: an engineer rebuilding the model. They should be able to implement it without re-reading the papers. It does not touch `app/` or `tests/`.

## 0. How to read this document

### 0.1 Verification tags

| Tag | Meaning |
|---|---|
| **[V]** | Verified. Read in the primary source (full text, publisher abstract, or the authors' own data release) during this research pass, 2026-10-16. Short verbatim snippets were captured for each one. |
| **[V-sec]** | Verified only in a secondary source, which is named. |
| **[D]** | Derived. Arithmetic done here on [V] numbers. The arithmetic is shown. |
| **[J]** | Engineering judgement made in this spec. It is not a literature value and must be presented as an assumption. |
| **UNVERIFIED** | Could not be confirmed in a primary source. Do not publish without re-checking. |

### 0.2 Conventions

- Emission factors (EF) are in grams of species per kilogram of **dry** matter burned.
- Burned area is in Mha (10⁶ ha).
- PM2.5 is in µg m⁻³.
- Times are local: WIB (UTC+7) for Kalimantan and Sumatra, UTC+8 for Sarawak and Singapore.

### 0.3 The five specified changes that move the headline numbers most

Summary only; details are in the sections that follow.

| # | Change | Headline output affected | Expected effect |
|---|---|---|---|
| 1 | Replace the single-wind-vector Gaussian plume with a Lagrangian particle model on gridded winds (or its precomputed source–receptor matrix), with **Sumatra added as a source region** (§5). | PM2.5 at Singapore, Kuala Lumpur and Kuching | Singapore and KL currently read near zero. The site itself says the model "cannot tell you" there. 2015 seasonal-mean fire PM2.5 at Singapore should come out of order 25–30 µg m⁻³, the modelled/observed values in Koplitz et al. 2016. Largest single change on the site. |
| 2 | Replace the synthesised fire clusters and the constant 14-day release with observed daily emissions: CAMS GFAS PM2.5, corrected for the peat EF bias, or FIRMS fire radiative power (FRP) (§1.4). | Every concentration on the map; the 3D view | Removes the invented geometry. Fixes the order-of-magnitude near-fire over-prediction the 3D view admits to (the release duration is "the single biggest lever"). Makes concentrations day-specific. |
| 3 | Replace the peat "burn-cycle" stock with a **peat-thickness column**. It loses depth to fire (Konecny et al. 2016 repeat-burn depths) **and to drainage-driven subsidence** (≈4–5 cm/yr, Hooijer et al. 2012; Evans et al. 2019). Report the peat-exhaustion year separately from forest (§4). | "Year Kalimantan runs out of burnable peat" | Current model: `never_pct = 100%` within 200 years (seed 1, 2,000 sims; drained-peat pool p50 = 2222). With subsidence, shallow drained peat (MoA class 0.5–1 m, 22% of Kalimantan peat) reaches zero thickness in roughly 12–23 years from oxidation alone [D]. The peat headline moves from "not this century" to "decades for drained peat". |
| 4 | Replace "KLHK national × 0.29" burned area with a satellite-consistent series (GFED5 / MCD64A1 / Sentinel-2), carried as a low–central–high range per year (§3). | Base annual burned area in the exhaustion model; history charts; emissions | KLHK is about half of independent satellite estimates in the years where both exist: 2015 2.61 vs 4.60 Mha; 2019 1.65 vs 2.64–3.11 Mha. GFED5's Equatorial Asia burned area is about 2.6× MCD64A1. A_base rises by a factor of about 1.5–2.6, and every exhaustion date comes earlier. |
| 5 | Replace the single "100,300 excess deaths" with the published range and its metric. Optionally compute a per-scenario estimate with GEMM (§7). | Mortality figure in History and Methods | 2015 published estimates are: 11,880 (short-term CRF, Crippa et al. 2016); 44,040 (GEMM, Kiely et al. 2020); 75,600 (long-term IER, Crippa); 100,300 (Koplitz et al. 2016). The site should say "about 12,000 to 100,000, depending mainly on the exposure–response function". |

Season-end changes have a smaller effect (weeks, mainly in positive-IOD years) but are scientifically important. They are in §2: fire-observed end dates, Drought Code, a 12-week rainfall hinge, and the Indian Ocean Dipole. The boundary-layer change (§6) cuts modelled afternoon concentrations at Palangka Raya by about 25%.

### 0.4 Errors found in the current data/code that should be fixed regardless of the rebuild

1. **`seasons.json` 2015 `psi_peak_singapore: 471` is not a PSI.** 471 µg m⁻³ is NEA's **1-hour PM2.5** in the West region at 23:00 on 19 Oct 2015 [V: NEA CSV via data.gov.sg; NST 20 Oct 2015]. The 2015 maximum **24-h PSI** was 322 (South, 25 Sep 2015 08:00) [V: NEA CSV]. The 2013 value of 401 is a **3-h PSI** [V-sec: press only]. The values 226 (1997) and 150 (2006) are of unverified metric. The column mixes three different quantities.
2. **Peat EF attribution.** 17.3 ± 6.0 g/kg (range 6.0–29.6) is from **Jayarathne et al. 2018** (ACP 18:2585), not Stockwell et al. 2016. Stockwell 2016 gives a preliminary 21.5 ± 4.6 g/kg from 7 plumes [V]. Akagi et al. 2011 has **no** peat PM2.5 EF: 9.1 is tropical forest, 7.17 is savanna [V].
3. **`pm25_peak_ugm3` Palangka Raya ≈ 400** is attributed to "Hayasaka et al. 2022". DOI 10.1080/10962247.2022.2077474 is **Santoso, Hopke et al. 2022**, JAWMA 72(11):1191–1200 [V: Crossref]. The value is a 24-h filter sample [V: abstract].
4. **2019 is labelled `el_nino`** because `oni_peak_aug_nov` takes the OND value (0.72). ONI during the burn window was JJA 0.38, JAS 0.20, ASO 0.31 [V: NOAA CPC]. That is ENSO-neutral. 2019 was an extreme **positive IOD** year: Sep–Nov DMI four standard deviations above the 1981–2010 climatology [V: Ratna et al. 2021]. 2006 has the same issue (JAS 0.33).
5. **Stale caveat in `burn.py`.** It says coefficients "were fitted on weather reconstructed from ENSO-conditioned climatology". `calibrate.py` and `hazard_fit.json` say they were fitted on ERA5 observed weather (`"weather_source": "observed"`). Remove the contradiction.
6. **Koplitz et al. 2016's exposure window is July–October**, despite the "September–October" title [V]. The 26,300–174,300 "range" is **not stated by the authors**. It is the sum of their country 95% intervals [D].
7. **README "a 2025 PLOS One study puts 2019 at 2.64 Mha".** The study exists but is Gaveau, Descals & Salim, **2026**, PLoS ONE 21:e0331831 [V].
8. **Fanin & van der Werf 2017 report that "in 2015 active fires stopped already in late October"** [V]. `seasons.json` has 2015 ending 13 Nov, from the rainfall rule. This is a concrete sign that rainfall-rule end dates are not fire end dates (§2).

---

## 1. Peat and vegetation fire emissions

### 1.1 Current implementation

Each burning cluster emits `PM2.5_kg = area_ha × fuel_load_t_ha × EF_g_kg` (`dispersion.py`, `stock.py`). Pool values come from `fuel_stock.json`:

| Pool | Fuel load | EF |
|---|---|---|
| Peat | 330 t/ha (Ballhorn 0.33 m × an assumed 0.1 g cm⁻³) | 17.3 g/kg |
| Primary forest | 55 t/ha | 9.1 g/kg |
| Secondary forest | 75 t/ha | 9.1 g/kg |
| Scrub | 12 t/ha | 7.2 g/kg |

Mass is released at a constant rate over 14 days (`DEFAULT_EPISODE_DAYS`). Fire geometry comes from `fires/fires_*.json`, which is **synthesised**. The peat EF does not vary with burn stage, moisture, or whether the hectare has burned before.

### 1.2 What the literature says

**Peat PM2.5 emission factors (field and lab)**

| Source | PM2.5 EF (g/kg) | Notes |
|---|---|---|
| Jayarathne et al. 2018, ACP 18:2585, doi:10.5194/acp-18-2585-2018 | **17.3 ± 6.0**, range 6.0–29.6 [V] | 21 filter samples, 18 plumes, Central Kalimantan, 2015. MCE 0.725–0.833. RSD 35%. OC 12.4 ± 5.4, EC 0.24 ± 0.10 g/kg. |
| Stockwell et al. 2016, ACP 16:11711, doi:10.5194/acp-16-11711-2016 | 21.5 ± 4.6 (7 plumes, preliminary) [V] | Gas EFs (35 plumes): CO2 1564 ± 77, CO 291 ± 49, CH4 9.51 ± 4.74 g/kg. MCE 0.693–0.835, mean 0.772. |
| Wooster et al. 2018, *Remote Sensing* 10:495, doi:10.3390/rs10040495 | Pure peat **17.8–22.3**; vegetation burning atop peat **44–61**; landscape-weighted peatland **28 ± 6** [V] | 73% pure peat, 27% vegetation-on-peat weighting. Landscape gas EFs: CO2 1779 ± 55, CO 238 ± 36, CH4 7.8 ± 2.3 g/kg. Peat C content 610 ± 47 g C/kg. |
| Andreae 2019, ACP 19:8523, doi:10.5194/acp-19-8523-2019 | Peat **18.9 ± 2.3** (N = 3) [V] | Tropical forest 8.3 ± 3.3 (N = 9). Savanna/grassland 6.7 ± 3.3 (N = 20). Peat MCE 0.80 ± 0.02. |
| Roulston et al. 2018, JGR 123:5607, doi:10.1029/2017JD027827 | **58** at ignition, decaying **9 %/day**. Recommended average **24** (measured mean 28) [V] | Peninsular Malaysia agricultural peat with bulk density 0.44–0.63 g cm⁻³, several times Kalimantan peat. The decline is attributed to ash build-up. MCE roughly constant at ~0.83. |
| Hu et al. 2018, *Int. J. Wildland Fire* 27:293, doi:10.1071/WF17084 | Literature range **5.9–44.5**; all-peat mean 18.7 [V] | Moisture dependence is described **qualitatively only**. No quantitative EF–moisture function exists in this review (checked). |
| Kiely et al. 2019, ACP 19:11105, doi:10.5194/acp-19-11105-2019 | **22.3** = mean of Wooster 28.0, Stockwell 21.5 and Jayarathne 17.3 [V] | Also OC 11.5, BC 0.07, CO2 1670 g/kg. |
| GFED4s (van der Werf et al. 2017) and GFAS | 9.1 for peat, i.e. the tropical-forest value used as a stand-in [V] | Wooster 2018 shows this halves 2015 PM2.5. |

**Peat burn depth and fuel consumption**

| Source | Depth / consumption | Notes |
|---|---|---|
| Ballhorn et al. 2009, PNAS 106:21213 | LiDAR mean burn-scar depth **0.33 ± 0.18 m**; field 0.30 ± 0.13 m; "never burned considerably deeper than 0.5 m" [V] | Assumed bulk density 0.1 g cm⁻³ and C 58%. 2006 fires, Central Kalimantan. |
| Page et al. 2002, Nature 420:61 | Mean depth 0.51 m, **UNVERIFIED** (paywalled body; 0.51 m appears only in Kiely 2019 Table 1) | 1997 peat carbon 0.19–0.23 Gt C from the study area [V]. |
| Simpson et al. 2016, *Remote Sens.* 8:1000 | **0.23 ± 0.19 m** [V] | First-time fire, Berbak, Jambi (Sumatra), 2015. Bulk density 0.106 g cm⁻³. 134 ± 29 t C/ha. |
| Konecny et al. 2016, GCB 22:1469 | **Area-averaged depth by fire number: 0.17 / 0.10 / 0.06 / 0.02 m; peat consumed 206 / 115 / 69 / 23 t/ha** for the 1st / 2nd / 3rd / ≥4th fire [V] | Kapuas, ex-Mega Rice Project, 1990–2011. Bulk density 0.121 (unburnt) and 0.115 (burnt). C 55.3%. Burn depth increases nearer canals. |
| Huijnen et al. 2016, Sci. Rep. 6:26886 | 2015 equivalent mean depth **26 cm** (1997: 50 cm); 42 kg DM m⁻² [V] | 0.8 Mha of peat burned Sep–Oct 2015. |
| Kiely et al. 2019 (FINNpeat) | 37 cm × 0.11 g cm⁻³ = 40.7 kg DM m⁻². Soil-moisture variant: depth from 5 cm at 0.25 m³ m⁻³ to 37 cm at 0.15 m³ m⁻³ [V] | Monthly 2015 mean depth: Aug 15.0, Sep 23.6, Oct 24.8 cm. |
| IPCC 2014 Wetlands Supplement, Table 2.6 | Tropical wildfire on drained peat **353 t DM/ha (170–536)** [V] | Konecny's values are 58% down to 7% of this. |
| GFED5 (van der Werf et al. 2025, Sci. Data 12:1870) | Equatorial Asia peatland fuel consumption **11,991 g C m⁻²** [V] | At 57% C this is about 210 t DM/ha [D]. |
| Warren et al. 2012, BG 9:4477 | Bulk density **0.127 ± 0.044 g cm⁻³**, C **51.3 ± 2.8%** (597 samples). Sabangau: 0.122 / 54.0% [V] | Kiely 2019 literature mean bulk density 0.11 g cm⁻³ [V]. |

**2015 regional totals (benchmarks for the emission module)**

| Source | Metric | Value |
|---|---|---|
| Wooster 2018 | Sep–Oct dry matter | 358 Tg (±30%), 305 Tg of it peat |
| Wooster 2018 | PM2.5 | **9.1 Tg** (abstract ±3.5; body ±3.2), 95% from peatlands |
| Kiely 2019 | Primary PM2.5 | **7.33 Tg** (71% peat) |
| Kiely 2019 | Fire SOA | +3.12 Tg |
| Jayarathne 2018 | Peat PM2.5 | 6.0 ± 5.5 Tg |
| GFED4s | PM2.5 | 4.2 Tg (Wooster) / 4.14 Tg (Kiely) |
| GFASv1.2 | PM2.5 | 3.99 Tg |

All [V].

**FRP-to-dry-matter conversion** (Kaiser et al. 2012, BG 9:527, Table 2): peat **5.87** kg MJ⁻¹; tropical forest 0.96; savanna 0.78; agriculture 0.29 [V]. The universal factor of Wooster et al. 2005 is 0.368 kg MJ⁻¹ [V, as quoted by Kaiser].

### 1.3 Gap

- **EF.** The current 17.3 g/kg is the lowest of the three Kalimantan field central values (17.3, 21.5, 28). The literature-mean 22.3 (Kiely 2019) is +29% [D]. Labelling 17.3 as "measured" hides a real spread of 17–28 for landscape means and 6–61 for individual plumes.
- **Fuel consumption on repeat burns.** The 330 t/ha "per fire" is defensible for a first fire. Repeat burns on already-burned peat consume much less: 206 → 115 → 69 → 23 t/ha (Konecny). Vetrita & Cochrane 2020 find 36% of Kalimantan peat burned more than twice in 2001–2018 [V]. So applying 330 t/ha to every burned peat hectare overstates emissions on degraded landscapes.
- **Temporal release.** The constant 14-day release is a tuning knob with no physical basis. Its own caveat calls it the "single biggest lever".
- **Synthesised geometry.** The fire clusters are invented. This is the largest data gap: the rebuild should use observed daily emissions.
- **Burn-stage dependence.** Roulston 2018 decay is real but measured on dense Malaysian agricultural peat. Its transfer to Kalimantan is unverified, so it should be a sensitivity option, not the default.

### 1.4 Specified change

**E1. Per-hectare emissions (used by the stock model and scenario tools)**

```
PM_kg(ha) = FC(n) [t DM/ha] × 1000 × EF [g/kg] / 1000          # = FC × EF, kg PM2.5 per ha

peat:   FC(n) = rho_b × d1 × r_n
        rho_b = 0.11 g cm-3   (= 110 kg m-3; range 0.093–0.127)    Kiely 2019 lit. mean; Warren 2012
        d1    = 0.26 m        (range 0.17–0.37)                   Huijnen 2016 (2015 equiv.); Konecny 2016 low; Kiely 2019 high
        r_n   = 1.00, 0.56, 0.33, 0.11 for fire n = 1, 2, 3, >=4  Konecny 2016 Table 3 fuel ratios (206/115/69/23)
        => FC(1) = 286 t/ha (range 187–470)                        [D]
        EF_peat = 22.3 g/kg (low 17.3, high 28.0; plume range 6.0–61)
                                                                   Kiely 2019 mean of Jayarathne/Stockwell/Wooster
        => PM per first-fire peat ha = 6.38 t (range ~3.2–13)      [D]

tropical forest (non-peat):  EF = 8.3 ± 3.3 g/kg  (Andreae 2019)
savanna / scrub / grass:     EF = 6.7 ± 3.3 g/kg  (Andreae 2019)
forest and scrub fuel consumption: keep the current 55 / 75 / 12 t/ha, but mark them UNVERIFIED.
    (They are not traced to a specific table. Replacing them with GFED5 per-class
     Equatorial Asia fuel consumption is the preferred fix once that table is extracted.)
```

The stock model must carry, per peat hectare, the **number of previous burns** (or at least a burned-before flag), so that r_n can be applied.

**E2. Daily emissions for transport (replaces the synthesised clusters and the 14-day release)**

Primary path: CAMS GFAS v1.2 daily PM2.5 flux, 0.1°, 2003 onward, CC-BY:

```
E_PM(cell, day) = GFAS_PM2.5(cell, day) × F_peat(cell)
F_peat = 2.0 (range 1.5–3.4) on cells whose peat fraction is >= 0.5; 1.0 elsewhere   [J/D]
```

Justification for F_peat:

| Ratio | Value | Status |
|---|---|---|
| Wooster 2018: 9.1 Tg vs GFAS 3.99 Tg | 2.28 | [D] |
| Kiely 2019: 7.33 Tg vs GFED4s 4.14 Tg | 1.77 | [D] |
| Koplitz 2016 applied GFAS × 1.5 "somewhat arbitrarily" | 1.5 | [V] |
| Kaiser et al. had recommended 3.4 (per Koplitz) | 3.4 | [V-sec] |

Fallback path (no GFAS access; near-real-time): FIRMS VIIRS/MODIS FRP.

```
DM_kg(cell, day) = FRE_MJ × beta,  beta = 5.87 kg/MJ peat, 0.96 tropical forest, 0.78 savanna   (Kaiser 2012)
PM_kg = DM_kg × EF
```

FRE must be integrated over the diurnal cycle from the overpass FRP. That needs a diurnal-cycle assumption [J], and raw FIRMS FRP is not cloud/smoke-gap-filled. GFAS does both, which is why it is primary.

**E3. Optional burn-stage EF (sensitivity toggle only)**

```
EF(t) = max(EF_floor, 58 × 0.91^t),  t = days since ignition
```

Roulston 2018. Use EF_floor = 17.3 [J]. Off by default. When on, the time-mean over a fire's life must be renormalised to 22.3 so that totals match the field landscape mean [J].

**What this changes in the headline numbers.**

- Peat PM2.5 per first-burned hectare goes from 5.71 t (330 × 17.3) to 6.38 t (+12%) [D].
- Per repeat-burned hectare it falls by 44–89%.
- Season-total regional PM2.5 for 2015 should land between 4 and 11 Tg. Use this as a unit test (§9).
- Replacing the 14-day release with daily GFAS removes the near-fire concentration bias the 3D view currently reports against itself.

---

## 2. Fire-season climate drivers and the end of the season

### 2.1 Current implementation

`burn.py` is a discrete-time survival model:

```
h(d) = sigmoid(b0 + b1·rain_7d + b2·drought_factor(KBDI) + b3·ONI + b4·monsoon_progress)
```

- Fitted coefficients (`hazard_fit.json`): b0 −3.60, b1 +0.0084/mm, b2 −5.54, b3 −0.159/°C, b4 +5.99.
- KBDI drives a logistic drought factor (centre 400, scale 110).
- Monsoon progress is a difference of logistics. Southern onset is DOY 305 (~1 Nov), northern DOY 288.
- Beyond 15 days, rainfall is a Markov-gamma generator scaled by `exp(−0.35·w_m·ONI)`.
- Season start/end labels come from an ERA5 **rainfall** rule. The code documents this circularity honestly.
- RMSE: 14.2 days in-sample, 18.0 days leave-one-out, 25.5 days forecast-mode.
- There is no Indian Ocean Dipole (IOD) term. `meteo.py`'s caveat already says this is a gap.

### 2.2 What the literature says

**Rainfall thresholds (all nonlinear, piecewise-linear "hinge" fits)**

| Source | Threshold | Window / index |
|---|---|---|
| Field, van der Werf & Shen 2009, *Nat. Geosci.* 2:185, doi:10.1038/ngeo443 | Sumatra **609 mm** (5-month, 1960–83); **631 mm** (5-month, 1984–2006). Kalimantan **672 mm** (4-month, 1984–2006) [V] | Back-totalled rain vs haze extinction (Bext) from airport visibility. Kalimantan 672 mm/4 months ≈ 5.5 mm d⁻¹ [D]. |
| Field et al. 2016, PNAS 113:9204, doi:10.1073/pnas.1524888113 | Fire and haze increase rapidly below **4 mm d⁻¹**; little activity above **6 mm d⁻¹**. Change-point estimates **3.9–5.2 mm d⁻¹**. Kalimantan haze threshold **3.7 mm d⁻¹** [V] | 12-week mean CPC gauge rainfall. Aug–Nov holds 85% of fire detections. Regions: S Sumatra 6°S–0°, 99–106°E; S-central Kalimantan 4°S–0°, 110–117°E. |
| Fanin & van der Werf 2017, BG 14:3995, doi:10.5194/bg-14-3995-2017 | Mean 120-day accumulation before each active fire: **200 mm** (S Sumatra), **305 mm** (S Kalimantan) for high-fire years. 120 days was the best window [V] | 305 mm/120 d ≈ 2.5 mm d⁻¹ [D]. "Small rain events are apparently capable of pausing the fire season". |
| Spessa et al. 2015, NHESS 15:429, doi:10.5194/nhess-15-429-2015 | Jun–Nov mean rain threshold ≈ **6 mm** (d⁻¹) [V] | ECMWF System 4 seasonal forecasts initialised 1 May explain ≥70% of burned-area variance. Observed rain R² 0.86–0.90. |

The formats disagree (multi-month totals, 12-week means, 120-day per-fire accumulations, seasonal means), so the numbers are **not interchangeable**. The robust, consistent message is: a hinge, below roughly 4–5.5 mm d⁻¹ of multi-month rainfall.

**ENSO and IOD**

- Field 2009 [V]. Share of Jul–Nov rainfall variance explained, 1948–2006:

  | Region | Niño3.4 | DMI | Combined |
  |---|---|---|---|
  | Kalimantan | 58% | 49% | **72%** |
  | Sumatra | 30% | 61% | 61% |

  Also: "the Indian Ocean Dipole pattern is as important a contributing factor". 1961 burned under neutral ENSO with a strongly positive IOD.
- Pan et al. 2018, JGR 123:7974 [V abstract]. Eastern-Pacific El Niños emitted almost twice the carbon of Central-Pacific events. Weak CP years coincide with weak or negative IOD.
- Nurdiati et al. 2022, TAO 33:16 [V abstract]. A weak El Niño with a positive IOD can surpass a single moderate El Niño. The IOD impact is strongest in southern Sumatra.
- Lam et al. 2024, IJC 44:2087 [V]. The IOD link to **JJA** Borneo rainfall is "rather weak, insignificant and inconsistent". Regression coefficients: ENSO −0.72, IOD +0.27. This partly disagrees with Field 2009, which used Jul–Nov.
- Ratna et al. 2021, GRL 48:e2020GL091497 [V]. 2019 Sep–Nov DMI was **4σ** above the 1981–2010 climatology (ERSST); 1997 was the strongest in HadISST. ENSO 2019 was "not considered to be an El Niño".
- Fanin & van der Werf 2017 [V] attribute the smaller 2015 fire count (1997 had 2.2× more active fires) partly to "neutral Indian Ocean Dipole conditions" in 2015.
- No study was found that cross-validates fire-prediction skill for ENSO-only vs ENSO+IOD side by side.

**DMI values for the fire seasons** [D]. Computed from NOAA OSMC weekly OISST DMI, °C, 1991–2020 base, not detrended. Monthly means of the weekly values:

| Year | Aug | Sep | Oct | Nov | SON |
|---|---|---|---|---|---|
| 1997 | 0.34 | 0.77 | 1.48 | 1.57 | 1.29 |
| 2006 | 0.41 | 0.63 | 1.08 | 0.88 | 0.88 |
| 2015 | 0.84 | 0.97 | 0.47 | 0.37 | 0.59 |
| 2019 | 1.19 | 1.46 | 1.85 | 1.62 | **1.63** |
| 2023 | 0.87 | 0.99 | 1.39 | 1.08 | 1.17 |

Sources disagree on whether 2015 was a positive-IOD year. Treat it as at most weak or short-lived.

**Drought indices**

- **KBDI.** Keetch & Byram 1968. 0–800 scale on an 8-inch store [V]. Developed for temperate mineral soils. The current code's own caveat notes that it saturates.
- **Canadian Drought Code (DC).** Van Wagner 1987, FTR-35 [V]; reference implementation in R `cffdrs` [V]. This is the index BMKG uses operationally. Equations:

  ```
  rain:  if r_o > 2.8 mm:  r_d = 0.83 r_o − 1.27
                           Q_o = 800 exp(−DC_{t−1}/400)
                           Q_r = Q_o + 3.937 r_d
                           DC_r = 400 ln(800/Q_r)          (floor 0)
  dry:   V = 0.36 (T_max + 2.8) + L_f   (V >= 0),   T_max in °C at noon
         DC_t = DC_r (or DC_{t−1}) + 0.5 V
  L_f  = 1.4 for |lat| <= 20°   (cffdrs tropical setting)
  ```

  The DC time lag is 52 days in standard weather [V].
- **Indonesian DC thresholds.**

  | Source | Threshold | Status |
  |---|---|---|
  | Field et al. 2004, Atmos. Env. 38:1869 | Severe haze likely above **DC 388.2** (1994–98 data) | [V abstract] |
  | GFWED (Field et al. 2015) | Same value rounded to "DC = 400" | [V] |
  | BMKG operational classes | 0–140 wet; 141–260 moist; 261–350 dry; **>350 very dry**, the range in which severe haze has occurred | [V, grey: bmkg.go.id] |
  | GFWED climatology | September mean DC peaks near 200 in southern Kalimantan and approaches 300 in southern Sumatra | [V] |
  | Shawki et al. 2017, GRL 44:9996 | CFSv2 DC forecast high DC over southern Kalimantan **180 days** ahead in 2015; only 60 days for southern Sumatra | [V] |

**Peat water table**

| Source | Finding | Status |
|---|---|---|
| Putra & Hayasaka 2011, *Tropics* 19:145 | Fires start when groundwater level (GWL) falls to about −30 cm and become severe below **−40 cm** | [V] |
| Wösten et al. 2008, Catena 73:212 | Recommends keeping GWL between −40 cm and +100 cm (a management band, not a fitted threshold) | [V] |
| Taufik et al. 2017, *Nat. Clim. Change* 7:428 | Uses groundwater recharge < 20 mm/month, not −40 cm. Adding hydrology makes maximum burned area 154–275% larger in strong ENSO years | [V] |

**What marks the end of the season**

No peer-reviewed definition of fire-season end from hotspot statistics was found. The closest evidence:

- Putra & Hayasaka 2011, Mega Rice area, 2001–2006 composites [V]. Fire season ends **DN 306** (~2 Nov) in non-El Niño years and **DN 323** (~19 Nov) in El Niño years, "about ten days after the end of the dry season". Minimum GWL was −117.4 cm (El Niño) vs −59.5 cm.
- Fanin 2017 [V]. 2015 fires stopped in late October; 1997 continued a week into November. In 2006, late-October rain under 8 mm d⁻¹ in S Kalimantan allowed further burning.
- Rainy-season onset definitions:

  | Source | Definition | Status |
  |---|---|---|
  | Giarno et al. 2012, J. Meteorol. Geofis. 13:1 (BMKG's journal) | **3 consecutive dasarian (10-day periods) each ≥ 50 mm**; onset = first of these | [V] |
  | Moron et al. 2009, J. Climate 22:840, describing BMG practice | **2** consecutive dasarian ≥ 50 mm after 1 Sep | [V] |
  | Moron et al. 2009, own definition | First wet day of the first 5-day spell ≥ 40 mm, with no 10-day spell < 5 mm in the following 30 days | [V] |

  Moron's southern-Kalimantan (south of 1°S) onset quartiles are **17 Sep / 22 Sep / 25 Oct** (accepted manuscript) [V]. These are much earlier than both the model's DOY 305 and the observed fire end. Early onset rains *pause* fires but do not end the peat episode.

### 2.3 Gap

1. **The target is circular.** End dates come from a rainfall rule. For 2015 the rule gives 13 Nov; observed fires stopped in late October (Fanin 2017). The calibration skill is therefore not skill at predicting fire.
2. **There is no IOD term.** 2019 was ENSO-neutral during its burn window with an extreme positive IOD. The current model gets 2019 "right" only because ONI is read from OND (0.72, post-season). A forecast issued in August 2019 would have seen JAS ONI = 0.20 and predicted a near-neutral season. Field 2009's 58% → 72% gain in explained Kalimantan Jul–Nov rainfall variance implies about an 18% smaller rainfall RMSE in the climatological (beyond-forecast) part of the simulation [D: √(0.28/0.42) = 0.82]. That is the part that dominates forecast-mode error.
3. **KBDI is the wrong drought index for the region.** The operational Indonesian system and the haze thresholds are in DC units. KBDI's 800 cap saturates in El Niño years (the code admits this).
4. **The logistic hazard cannot produce threshold behaviour.** The literature is unanimous that the response is a hinge.
5. **The monsoon onset is a fixed logistic at DOY 305.** It should be computed per year from the BMKG criterion. The 2015 effect is about 3 weeks [D: 13 Nov rule date vs late-October fire stop].

### 2.4 Specified change

**S1. New target labels: fire-observed season dates**

Compute daily active-fire counts from FIRMS:

- MODIS C6.1 from Nov 2000; VIIRS S-NPP from Jan 2012; NOAA-20 from Apr 2018.
- Region: the peat-belt box of Field 2016, 4°S–0°, 110–117°E. Run separately for the S Sumatra box (6°S–0°, 99–106°E).
- Use a consistent sensor per series: MODIS for 2001–present for the long fit, with VIIRS as a check.

Definitions [J; no literature definition exists; must be labelled as the site's own definition]:

- `start` = first day after 1 May on which the 7-day running count exceeds 10% of that season's maximum 7-day count, staying above it for ≥ 7 days.
- `end` = first day after the seasonal peak from which the 7-day running count stays below 10% of the season maximum for **14 consecutive days**. Use the first day of that 14-day run.
- Years with a seasonal maximum below a floor (e.g. the 10th percentile of all years' maxima) have **no season**. This reproduces the current treatment of 2010 and 2022.

Sanity anchors:

- Putra & Hayasaka composites: end DN 306 (non-El Niño) and DN 323 (El Niño), Mega Rice area, 2001–06.
- Fanin 2017: 2015 ended late October.

For 1997–2000 (no MODIS), keep the rainfall-rule dates but flag them `target_source: "rainfall_rule"`. Exclude them from the skill metrics.

**S2. Replace KBDI with the Drought Code (equations above)**

```
dc_term(d) = max(0, DC(d) − DC_crit) / 100,   DC_crit = 350 (BMKG "very dry"; range 300–400; Field 2004 388.2)
```

Keep KBDI computable for continuity, but do not use it in the hazard.

**S3. Replace `rain_7d` with a hinge on the 12-week mean rainfall plus a short-window term**

```
deficit(d) = max(0, alpha − P84(d)),  P84 = mean daily rain over the previous 84 days (mm/d)
alpha = 3.7 mm/d for Kalimantan (Field 2016; range 3.7–5.5 from Field 2016 / Field 2009)
alpha = 4.0 mm/d for S Sumatra (range 3.9–5.2)
```

Keep a short-window wet-spell term, `wet10(d) = 1 if dasarian rain >= 50 mm else 0` (BMKG unit). It lets "small rain events pause the season" (Fanin 2017).

New hazard:

```
h(d) = sigmoid( c0 + c1·wet10(d) + c2·n_wet_dasarian_consecutive(d)
               − c3·deficit(d) − c4·dc_term(d) − c5·ONI_3mo(d) − c6·DMI_3mo(d) )
```

- `n_wet_dasarian_consecutive` ∈ {0,1,2,3+} encodes the BMKG onset criterion directly. The hazard should jump when it reaches 3.
- ONI and DMI enter as **3-month means ending at the forecast date** (e.g. JAS on 1 Oct), **not** post-season values. This fixes the 2019 labelling bug.
- Sign constraints: c1, c2, c3, c4, c5, c6 ≥ 0 as written.

**S4. IOD in the rainfall generator**

```
R_m(ONI, DMI) = R_m × exp(−k_enso·w_m·ONI − k_iod·w_m·DMI)
```

- Fit k_enso and k_iod by OLS of log monthly ERA5 (or CHIRPS) rainfall, Jun–Nov 1981–2025, on ONI and DMI at the Kalimantan peat-belt points.
- The current `ENSO_RAIN_K = 0.35` is **UNVERIFIED** (no source found); it must be refitted, not kept.
- The DMI forecast for the simulation horizon can be persisted from the latest weekly value. That is a weak assumption; flag it in the caveats.

**S5. Monsoon onset as a per-year diagnostic, not a fixed DOY**

In the historical fit, compute each year's BMKG onset (3 consecutive dasarian ≥ 50 mm, starting after 1 Aug) from ERA5/CHIRPS. Use its climatological distribution, conditioned on ONI and DMI, in the forecast. Report the onset distribution on the site.

**S6. Calibration and validation**

- Leave-one-season-out on fire-observed labels, 2001–2025 (~25 seasons).
- Report RMSE separately for positive-IOD years (DMI_SON > 0.4 °C [J]; BoM's +0.4 threshold is UNVERIFIED at source) and others.
- Expected effect:
  - Positive-IOD/weak-ENSO years like 2019 move from "short season" to "long season" in forecast mode.
  - Forecast-mode RMSE should fall modestly; the rainfall-variance argument above gives an order of 10–20% [D].
  - In-sample RMSE will probably **rise**, because the circularity bonus disappears. That is the honest outcome, and the site should say so.

**What this gives up.** The model still has no ignition term and no spatially resolved water table. The GWL −40 cm threshold (Putra & Hayasaka 2011) is the physically right predictor, but there is no regional GWL dataset to drive it. DC is the operational proxy.

---

## 3. Burned area and its uncertainty

### 3.1 Current implementation

`seasons.json` uses three kinds of burned-area figure:

- KLHK national figures for 2015–2025.
- A GFED4.1s carbon regression for 1998–2014.
- Tacconi 2003 for 1997.

Kalimantan is national × 0.29, except 2015 (two-province lower bound, 0.78 Mha), 2019 and 2022. The stock model's `A_base` (0.198 Mha/yr) and ENSO multipliers are computed from these values.

### 3.2 What the literature says

**Year-by-year estimates**

| Year | KLHK (official, Landsat) | MCD64A1 | Sentinel-1/2 | GFED5 |
|---|---|---|---|---|
| 2015, Indonesia | **2.61 Mha** [V-sec: SiPongi via GoodStats; KLHK via JPNN] | — | **4.60 Mha** (Lohberger et al. 2018, GCB 24:644; S-1; Sumatra, Kalimantan, West Papua; 84% accuracy) [V] | — |
| 2019, Indonesia | **1.65 Mha** [V-sec] | 2.03–2.04 (Gaveau 2021); 2.09 (Gaveau 2026); 2.2 (Chen 2023) [V] | **>3.11 Mha** (Gaveau et al. 2021, ESSD 13:5353, S-2 annual); **2.64 Mha** (Gaveau et al. 2026, PLoS ONE 21:e0331831, S-2 monthly) [V] | **3.8 Mha** (Chen et al. 2023) [V] |
| 2023, Indonesia | **1.16 Mha** [V-sec] | 1.11 Mha (Gaveau 2026) [V] | **1.70 Mha** (Gaveau 2026) [V] | — |

Notes on these figures:

- **2015.**
  - Lohberger's peat share is 37%, with half the area in Kalimantan [V-sec: Uda et al. 2019].
  - KLHK's peat share is 33% (0.87 Mha) [V-sec: press, UNVERIFIED-primary].
  - The 3.79 Mha that Kiely 2021 quotes covers Sumatra and Kalimantan only [V-sec].
- **2019.**
  - Gaveau 2021 corrected the official map for omission: 2.29 Mha (CI 1.96–2.63) in seven provinces, "a 1.1 Mha discrepancy" with the mapped area [V].
  - Producer's accuracy: official map 49.5%; MCD64A1 53.1%; S-2 75.6% [V].
  - Peat share: 31% (S-2 2021) vs 39% (official) vs 40% (MCD64A1) [V]. Gaveau 2026 gives 16.1% because it uses a newer, smaller peat map [V].
  - Interim KLHK reports in December 2019 said 0.86 Mha [V-sec]. The KLHK figure itself changes over time.
- **GFED5** (Chen et al. 2023, ESSD 15:5227) [V]:
  - Equatorial Asia burned area is **3.7 ± 2.0 Mha/yr** (2001–2020).
  - That is 3.71 vs 1.43 Mha/yr for MCD64A1 C6, i.e. ×2.6 [D], and 3.95 vs 2.05 for GFED4s, i.e. ×1.9 [D].
  - Equatorial Asia peat burned area is 0.56 Mha/yr.
- **GFED5 emissions** (van der Werf et al. 2025, Sci. Data 12:1870) [V]:
  - Equatorial Asia averages 101.5 Tg C/yr, with a maximum of 353 Tg C in 2015.
  - It is the **only region where GFED5 emissions are lower than GFED4s (−10%)**: burned area goes up but fuel consumption per area goes down.
- **MCD64A1 C6** (Giglio et al. 2018, RSE 217:72) [V]:
  - Global commission error 0.24 and omission error 0.37.
  - Equatorial Asia burned area 1.3 → 1.7 Mha/yr vs C5.1.
  - The paper does not discuss peat or smoke specifically. Small-fire and peat omission is documented by Gaveau 2021/2026 and Chen 2023.
- **Fire frequency** (Vetrita & Cochrane 2020, *Remote Sens.* 12:5; MODIS C6, 2001–2018) [V]:
  - Kalimantan peat: 28.6% of the area affected; mean annual burned fraction 3.6%/yr; fire-return interval **28 yr** (Sumatra 45 yr).
  - Of the burned Kalimantan peat, 13.5% burned once, 12.8% twice and **36.0% more than twice**.
  - The paper states the Kalimantan peat area inconsistently (5.7 in the text, 5.8 Mha in a table footnote).

### 3.3 Gap

- KLHK is consistently the **low end**. Its Landsat-based producer's accuracy was 49.5% in 2019.
- The site uses KLHK as the series and × 0.29 for Kalimantan. That share is the mean of three years spanning 0.12–0.38, and it erases the year-to-year variation.
- The pre-2015 regression on GFED4.1s carbon inherits GFED4's known small-fire omission.
- Net effect: `A_base` and the El Niño multiplier are probably too low by a factor of about 1.5–2.6. The exhaustion model is optimistic accordingly; its own caveat says so qualitatively.

### 3.4 Specified change

**B1. Store a range per year, not a number**

```json
"burned_area": {
  "indonesia": {"low": 2.61, "central": null, "high": 4.60,
                 "estimates": [{"value": 2.61, "source": "KLHK SiPongi", "method": "Landsat-8 official"},
                               {"value": 4.60, "source": "Lohberger 2018", "method": "Sentinel-1"}]},
  "kalimantan": {...same shape...}
}
```

- `low` = minimum of the independent estimates. `high` = maximum.
- `central` is **null unless at least one independent satellite product exists for that year**. When defined, it is the GFED5 value, for internal consistency across 1997–2022 [J].
- The UI shows "official X Mha; satellite estimates Y–Z Mha" and never a single number.

**B2. Consistent model input series**

Drive `A_base`, the ENSO/IOD multipliers and the per-province split from **GFED5.1 gridded burned area**:

- 1997–2022 via the globalfiredata.org SFTP; GFED5.1NRT for 2023 onward.
- Clip to Kalimantan and Sumatra province polygons, so that the Kalimantan share is **measured each year**.
- Run a sensitivity case with MCD64A1 C6.1 as the low series and KLHK as the official floor.
- Licence: not stated on the GFED page. Check before redistributing derived numbers.

**B3. Peat vs non-peat split**

Intersect the burned-area grid with a peat map (§4) instead of using the single 2015 KLHK 33% value. Report the peat share with its map-dependence (16–40% for 2019, depending on map and product).

**B4. Validation numbers for unit tests** (Indonesia, Mha)

| Year | Official | Satellite |
|---|---|---|
| 2015 | 2.61 | 4.60 |
| 2019 | 1.65 | 2.03–3.8 |
| 2023 | 1.16 | 1.11–1.70 |

**Effect.** A satellite-consistent `A_base` raises base burn rates by roughly the satellite/KLHK ratio (×1.4–2.6) [D]. Every year in the exhaustion distribution moves earlier, and emissions per season rise proportionally in the stock model's PM2.5 total.

---

## 4. Fuel stock, land-cover change and non-fire peat loss

### 4.1 Current implementation

`stock.py` uses five pools (`fuel_stock.json`):

| Pool | Area (Mha) |
|---|---|
| Intact peat | 1.8 |
| Drained peat | 2.7 |
| Primary forest | 12.76 |
| Secondary forest | 12.04 |
| Scrub | 12.11 |

- Peat is carried as "burn-cycle hectares" = area × burnable depth (0.5 m intact, 1.0 m drained) / 0.33 m, capped at 4 cycles.
- Burning is `A_base × m(ENSO) × s_p × (S/S0)^0.6`.
- Deforestation is 0.10 Mha/yr; restoration is 0.05 Mha/yr.
- "Exhausted" means **peat + forest** stock below 5% of baseline.
- With defaults (seed 1, 2,000 sims), the run here returned `never_pct = 100%`; the drained-peat pool p50 was 2222 with 76.9% "never".
- There is **no non-fire peat loss term**.

### 4.2 What the literature says

**Peat extent in Kalimantan**

| Source | Kalimantan peat | Notes |
|---|---|---|
| MoA / BBSDLP 2011 (Ritung et al.), via Warren et al. 2017, CBM 12:12 | **4.78 Mha** (4,778,005 ha), 32% of 14.9 Mha national [V] | Depth classes 0.5–1 / 1–2 / 2–3 / >3 m = **22 / 29 / 22 / 27%** [V] |
| Wetlands International (Wahyunto), via Warren 2017 | **5.77 Mha** [V] | |
| Anda et al. 2021, Geoderma 402:115235 | ~4.5 Mha of 13.43 Mha national, **UNVERIFIED-primary** | Abstract not retrievable. Secondary sources give 13.3–13.4 Mha nationally and "roughly 45,000 km²" in Kalimantan. |
| Hooijer & Vernimmen **2013** (Deltares/QANS report, **grey literature**; *not* 2021) | — [V] | Both national maps underestimate extent: WI by 13% (1.7 Mha), BBSDLP by a further 1.7 Mha, for Sumatra + Kalimantan. WI underestimates thickness by 1.5 m (27%). Mean measured Kalimantan peat thickness **5.4 m**. |
| Miettinen et al. 2016, GECO 6:67 (Peninsular Malaysia + Sumatra + Borneo) | — [V] | Peat swamp forest fell from 76% (1990) to 41% (2007) to **29% (4.6 Mha)** in 2015. Managed land is 50% (7.8 Mha). Industrial plantations are 27% (4.3 Mha). Borneo-only figures UNVERIFIED. |

**Forest loss**

| Source | Finding |
|---|---|
| Gaveau et al. 2022, PLoS ONE 17:e0266178 | Kalimantan forest loss **4.02 Mha (14%) over 2001–2019**, ≈0.21 Mha/yr [V; /yr D] |
| Gaveau et al. 2016, Sci. Rep. 6:32017 | 14.4 Mha of old-growth cleared in Indonesian Borneo, 1973–2015 [V] |
| Gaveau et al. 2014, PLoS ONE 9:e101654 | Borneo was 75.7% forested in 1973; Kalimantan lost 30.7% of its 1973 forest by 2010 [V] |
| Margono et al. 2014, NCC 4:730 | Indonesia primary forest loss 6.02 Mha over 2000–2012 [V]. Kalimantan split UNVERIFIED. |
| Auriga Nusantara | Kalimantan 2023 ≈ 0.125 Mha, **UNVERIFIED** (search snippets only) |

**Subsidence and oxidation (the missing term)**

| Source | Finding |
|---|---|
| Hooijer et al. 2012, BG 9:1053 | 142 cm subsidence in the first 5 years after drainage (75 cm in year 1). Then "**constant at around 5 cm yr⁻¹**" at a 0.7 m water table. Oxidation is 75% of subsidence after 5 years and **92% after 18 years**. Regression: S = 0.69 − 5.98·WD (cm/yr; WD in m, negative down) [V] |
| Evans et al. 2019, Geoderma 338:410 | Acacia plantations **4.3 ± 2.0 cm/yr**; forest 3.4 ± 1.8 cm/yr (>2000 site-years, Sumatra). Raising the water table to 40 cm cuts subsidence by 25–30% [V] |
| Couwenberg & Hooijer 2013, *Mires and Peat* 12 | Oil palm average 4.2 cm/yr; ~18 t C ha⁻¹ yr⁻¹ [V abstract] |
| Hooijer et al. 2010, BG 7:1505 | CO2 = 91 t ha⁻¹ yr⁻¹ per m of drainage depth (0.91 per cm) [V] |
| IPCC 2014 Wetlands, Table 2.1 (t CO2-C ha⁻¹ yr⁻¹) | Acacia 20 (16–24); oil palm 11 (5.6–17); drained forest/shrub 5.3 (−0.7–9.5) [V] |

**Peat accumulation** (for the "never grows back" statement)

- Page et al. 2004, JQS 19:625 [V]: late-Holocene 0.15–0.38 mm/yr; >2 mm/yr in the early Holocene.
- Dommain et al. 2011, QSR 30:999: Central Kalimantan 0.54 mm/yr; coastal 1.77–1.89 mm/yr. **[V-sec, snippet only]**.

**Repeat fire** (Vetrita & Cochrane 2020; Konecny 2016): see §1 and §3.

### 4.3 Gap

- **No subsidence term.** On drained peat, oxidation at 3.4–5 cm/yr removes more peat than fire does. Konecny's cumulative fire loss over ≥4 fires is ~0.35 m [D: 0.17+0.10+0.06+0.02]. Subsidence removes 0.35 m in 7–10 years [D].
- **Wrong exhaustion criterion.** The burn-cycle stock treats only 0.5–1.0 m as burnable and then declares a hectare done. On drained peat the water table follows the subsiding surface down, so the peat keeps burning until it is gone or reaches the drainage base [J, consistent with Hooijer 2012's constant long-term rate]. The current model therefore cannot "run out" within 200 years. Its headline "never" is an artefact of that criterion.
- **Peat extent.** The current "intact" 1.8 Mha residual and "drained" 2.7 Mha have no single source. MoA 4.78 Mha with published depth classes is the best-documented baseline.
- **Deforestation.** 0.10 Mha/yr is about half the 2001–2019 Kalimantan mean of 0.21 Mha/yr. Recent years are lower (UNVERIFIED), so use a range.
- **One combined headline.** Exhaustion of "peat + forest" combined is dominated by 24.8 Mha of forest that barely burns. The site's question is about peat.

### 4.4 Specified change

**F1. Peat as a thickness column per depth class (replaces burn cycles)**

State per (province, depth class k, drainage status) cell: area A, mean thickness z (m), fire count n.

Initialise from MoA 2011 Kalimantan = 4.78 Mha:

| Depth class | Share | Initial z |
|---|---|---|
| 0.5–1 m | 22% | 0.75 m |
| 1–2 m | 29% | 1.5 m |
| 2–3 m | 22% | 2.5 m |
| >3 m | 27% | 5.0 m [J] |

The >3 m value is a judgement: Hooijer & Vernimmen 2013 give a 5.4 m mean for all Kalimantan peat.

- The drained fraction is a parameter. 2.7/4.5 = 60% [current; unsourced] and should be replaced by Miettinen 2016's Borneo managed/degraded fraction once extracted.
- Alternative extents for sensitivity: WI 5.77 Mha; Anda ~4.5 Mha (UNVERIFIED).

Annual update for drained cells:

```
z_{t+1} = z_t − s_d − Σ_fires d1·r_{n}   (fire-depth term only for the burned fraction of A)
s_d = 0.043 m/yr (Evans 2019 Acacia; range 0.034 forest-drained – 0.05 Hooijer 2012 long-term)
newly drained cells: add the Hooijer 2012 first-5-yr consolidation (1.42 m total, 0.75 m in yr 1) to surface lowering,
   but only 25% of it as mass loss (oxidation share 75% → 92%; use 0.75 for yr 1–5, 0.92 thereafter)
```

Intact (undrained) cells: s = 0, and burn probability is lower via susceptibility. A hectare exits the peat stock when z ≤ 0.

The drainage base is not modelled. Flag that peat may stop subsiding before z = 0 because it reaches the drainage limit and floods [J]; no regional drainage-base data was verified.

Mass loss for the carbon/PM outputs is `Δz × rho_b` (0.11 g cm⁻³). Only the fire part emits PM2.5. Oxidation emits CO2 only, which is useful for a "carbon" panel.

**F2. Burn probability per peat cell**

```
p_burn(year) = p0 × m(ENSO, IOD) × s_status
```

- p0 is calibrated so that the 2001–2018 mean Kalimantan peat burned fraction is **3.6%/yr** (Vetrita & Cochrane 2020). Note this is MODIS-based and therefore a lower bound [V].
- Repeat-burn depth r_n comes from Konecny.

**F3. Deforestation**

Use 0.21 Mha/yr (Gaveau 2022, 2001–2019 mean) with a range of 0.10–0.21. Show the low end as "recent years", flagged UNVERIFIED until a primary 2020s Kalimantan source is confirmed.

**F4. Headlines**

Report separately:

- (a) **"Drained peat remaining"**: Mha and the year it falls below 50% and 5%.
- (b) **"Peat lost to fire vs to drainage"**, cumulative.
- (c) The forest stock.

Drop the combined peat+forest exhaustion year, or demote it to a secondary metric.

**Effect, back-of-envelope** [D, ignoring fire and the drainage base; at 4.3 cm/yr]:

| MoA depth class | Time to reach zero |
|---|---|
| 0.5–1 m | 12–23 yr |
| 1–2 m | 23–47 yr |
| 2–3 m | 47–70 yr |
| >3 m | >70 yr |

So roughly half of drained Kalimantan peat (the two shallowest classes, 51% of area) reaches zero thickness in about 20–50 years from oxidation alone. Fire shortens this. The current "never within 200 years" is not supported by the subsidence literature. Present the new result with the caveat that the drainage base and rewetting can halt it.

---

## 5. Haze transport and dispersion

### 5.1 Current implementation

`dispersion.py` and `haze3d.py` use a steady-state Gaussian plume:

- **Wind:** one user-supplied wind speed and direction for the whole 900 km domain.
- **Dispersion:** Briggs class D/E sigmas plus a Fickian mesoscale term, K_h = 5×10⁴ m² s⁻¹. The mixed-layer cap is H (default 800 m).
- **Removal:**
  - Dry deposition: v_d = 0.2 cm/s.
  - Wet scavenging: Λ = 1.0×10⁻⁴·R^0.7 s⁻¹, with R = 5 mm/h scaled by the raining fraction.
- **Sources:** Kalimantan only, from the synthesised clusters.
- **Not modelled:** secondary organic aerosol (SOA) and injection height. All emissions are ground-level.

The site's own caveat says Singapore and KL "will usually show little or nothing".

### 5.2 What the literature says

**Regional modelling studies**

| Study | Model and setup | Key results |
|---|---|---|
| Koplitz et al. 2016, ERL 11:094023 | GEOS-Chem **adjoint**, 0.5°×0.67°. Sensitivities from Jul–Oct **2006** meteorology applied to 2015 GFAS emissions **× 1.5**. Smoke = OC + BC | South Sumatra gave ">50% of the regional smoke exposure" in both 2006 and 2015. South Sumatra emissions produced "more than three times" the exposure per unit of Central Kalimantan's (2006). Riau did not contribute significantly. Singapore Jul–Oct 2015 smoke: **27 modelled vs 30 µg m⁻³ observed** [V] |
| Kim et al. 2015, Atmos. Env. 102:11 | GEOS-Chem adjoint, GFED3 × 1.5 | Sumatra was 37% of emissions but **64%** of Singapore smoke (Jul–Nov 2006). Linear source–receptor: receptor PM = ∫∫ (∂PM/∂E)·E dx dt [V] |
| Reddington et al. 2014, ERL 9:094006 | GLOMAP at 2.8°, GFED3, **unscaled** | Fire PM2.5 in Singapore 2004–09: **southern Sumatra 42%, central Sumatra 35%, Indonesian Borneo 14%**. Singapore PM2.5 NMB −64% [V] |
| Lee et al. 2017, ACP 17:965 | WRF-Chem 36 km, 2003–2014, tagged tracers. Peat injection **capped at 700 m** | Singapore annual fire PM2.5: Sumatra+Java 40.7%, Borneo 14.3%, mainland SE Asia 36.7%. **Kuching: Borneo 84.6%.** KL: Sumatra+Java 49.6%. Aug–Oct scavenging time **< 3 days** along the MJO path, > 5 days over drier seas. Flow is SE south of the Equator, turning NE past it [V] |
| Hansen et al. 2019, ACP 19:5363 | NAME III Lagrangian on UM 25 km winds, GFAS v1.2 with GFAS injection heights, primary PM10 only, 2010–2015 | **Aug–Oct 2015 Singapore shares (west / east stations):** South Sumatra 38.2% / 21.8%; **Central Kalimantan 31.2% / 41.5%**; Bangka–Belitung 11.3% / 13.6%; West Kalimantan 6.6% / 9.4%; Jambi 6.5% / 6.0%. June 2013: ~98% Riau [V] |
| Kiely et al. 2019, 2020 | WRF-Chem 30 km, FINNpeatSM | Half of emissions at the surface, half spread through the boundary layer; surface-only gives ×1.34 surface PM2.5. SOA = **0.04 g per g fire CO**, which gave 3.12 Tg in 2015 (**31% of fire PM**). Mean fractional bias −0.11 at 55 sites [V] |
| Crippa et al. 2016, Sci. Rep. 6:37074 | WRF-Chem 10 km, FINNv2 unscaled | Singapore Sep–Nov 2015 observed mean **52 µg m⁻³** vs modelled 45. Pekanbaru PM10 observed 174 vs modelled 140 [V] |

**Was Singapore's 2015 haze predominantly Sumatran?** The evidence is mixed.

- Climatological and adjoint studies (Kim 2015, Reddington 2014, Lee 2017, Koplitz 2016) say Sumatra dominates.
- Hansen 2019, using 2015 meteorology, finds Kalimantan **co-dominant at west Singapore and dominant at east Singapore** in Aug–Oct 2015 [V].
- The brief's premise ("predominantly Sumatran") is therefore only partly supported. Both sources are required.

**Injection height**

| Source | Finding |
|---|---|
| Tosca et al. 2011, JGR 116:D08207 (MISR, 2001–2009) | Mean plume height **709 ± 14 m Borneo, 749 ± 24 m Sumatra**; 96% of plumes within 500 m of the boundary layer; smoke clouds at 1–2 km; free-troposphere injection "not important" [V] |
| Zender et al. 2012, ACP 12:3437 | Borneo plume height IQR **523–993 m**; median plume age 2.8 h [V] |

**Secondary organic aerosol**

| Source | Finding |
|---|---|
| Kiely 2019 | 31% of fire PM [V] |
| Aouizerats et al. 2015, ACP 15:363 (VBS, 2006) | SOA ≤ 20 µg m⁻³ vs POA up to 600 µg m⁻³: "a minor role" [V] |
| Budisulistiorini et al. 2018, ACP 18:16481 | Singapore Oct 2015: OA ≈ 80% of NR-PM1; oxygenated OA ≈ 50% of OA (includes oxidised POA) [V] |

**Removal**

| Source | Finding |
|---|---|
| AeroCom (Textor et al. 2006) | Global residence time **6 days (POM)** and 7 days (BC) [V] |
| Lee 2017 | < 3 days in Maritime Continent wet regions [V] |
| Wang et al. 2010, ACP 10:5685 | Below-cloud scavenging coefficients uncertain by **1–2 orders of magnitude** for 0.01–3 µm [V] |
| Emerson et al. 2020, PNAS 117:26076 | The Zhang scheme is OK at the accumulation-mode centre. **No single v_d value was verified**; 0.1–0.2 cm/s is UNVERIFIED. |

**Winds.** See §6 and `research/data/wind_850_925_summary.txt`. The sources are GFS (2022–25) and IFS (2024–25) historical forecasts, not ERA5. Aug–Sep, 925 hPa:

| Location | Speed | Direction |
|---|---|---|
| Java Sea | 7.4–8.6 m/s | from ~113–116° |
| Karimata Strait | 5.6–7.0 m/s | from SE |
| Palangka Raya | 4.6 m/s | SE (Aug) |
| Singapore | 4.8–5.8 m/s | from S |
| Kuching (850 hPa) | 3.2–5.0 m/s | from SSW–WSW |

Surface SE winds over Borneo are 3–4 m/s (Zender 2012) [V].

### 5.3 Gap

- One wind vector cannot represent the observed flow: SE over the Java Sea, turning S/SW toward Singapore and Sarawak.
- Without Sumatra, the peninsula and Singapore are missing 41–77% of their climatological fire PM [V: Lee 2017 40.7% (Sumatra+Java); Kim 2015 64%; Reddington 2014 42+35 = 77% [D]]. In Aug–Oct 2015 the gap is 41–56% (Hansen; South Sumatra + Jambi + Bangka–Belitung) [D].
- No vertical structure: no residual-layer decoupling at night, no injection profile.
- No SOA: far-field PM is low by 0–31%.
- Removal coefficients are uncalibrated.

### 5.4 Specified change: reduced-form Lagrangian particle model ("LPM-3L")

**Domain and data**

- 95–120°E, 8°S–8°N.
- Winds: ERA5 hourly u, v at 10 m, 100 m, 925 hPa and 850 hPa, plus precipitation and BLH, at 0.25°.
  - Pressure-level winds must come from the Copernicus CDS: Open-Meteo's ERA5 archive does **not** serve 850 hPa (checked).
  - Live forecast: Open-Meteo GFS/IFS 925/850 hPa (available, checked).
- Emissions: E2 (§1.4), Kalimantan **and Sumatra** cells (Riau, Jambi, South Sumatra, Bangka–Belitung, Lampung), plus Peninsular Malaysia peat for completeness.

**Particles and layers**

- Release N particles per active cell per hour [J: 4 per cell-hour], with mass = E × Δt / N.
- Three layers:
  - **L1 (mixed layer):** 0 to H(t), the ERA5 BLH at the particle position.
  - **L2 (residual layer):** H(t) to H_day,max (the previous afternoon's maximum BLH).
  - **L3 (free troposphere):** above H_day,max.

**Injection**

- **Peat smouldering** (cells with peat fraction ≥ 0.5): 50% of mass at 0–70 m and 50% uniform over 0–min(H(t), 700 m). Sources: Kiely 2019/2020 split, Lee 2017 700 m cap.
- **Vegetation flaming:** uniform over 0–(H(t) + 500 m), capped at 2,000 m. Sources: Tosca 2011 (96% within 500 m of the boundary layer; smoke clouds 1–2 km).

**Advection (Δt = 15 min [J])**

- L1 by day: mean of the 100 m and 925 hPa winds.
- L1 by night: 100 m wind.
- L2: 925 hPa wind.
- L3: 850 hPa wind.
- Horizontal turbulence: random walk with K_h. Default 1×10⁴ m² s⁻¹, range 5×10³–5×10⁴ (UNVERIFIED; tune against plume widths).

**Vertical exchange**

- While H(t) is rising (morning), any L2 particle with z < H(t) joins L1. L1 particles are redistributed uniformly in [0, H(t)] every step (well mixed).
- While H(t) is falling (evening), L1 particles with z > H(t) become L2 and keep their z. They no longer contribute to the surface.
- This reproduces nocturnal trapping of near-source smoke and the morning fumigation of residual smoke at downwind cities. Huang et al. 2024 (GRL) describe the latter for Singapore in Oct 2023; their numbers are UNVERIFIED.

**Surface concentration**

```
C_sfc(cell) = Σ_{p in L1, in cell} m_p / (A_cell × H(t)) + C_bg
```

C_bg is the non-fire background. Koplitz 2016 gives ~10–15 µg m⁻³ [V-sec, quoted by the reviewing agent from the main text].

**Removal (first-order, per step)**

- Dry: λ_d = v_d / H, L1 only. v_d = 0.15 cm s⁻¹, range 0.05–0.3 [J/UNVERIFIED].
- Wet: λ_w = Λ0 · (P / 1 mm h⁻¹)^0.7 in cells with precipitation P > 0.1 mm/h, applied to all layers below 5 km.
  - Λ0 is **calibrated**, not taken from literature (Wang 2010: 1–2 orders of magnitude uncertainty).
  - Calibration target: the Jul–Oct domain-mean e-folding lifetime of fire PM should fall within **3–6 days** (Lee 2017 < 3 d wet / > 5 d dry; AeroCom POM 6 d).

**SOA (toggle; default ON, with the "OFF" bound shown as the uncertainty)**

```
dM_SOA/dt = (Y × EF_CO / EF_PM) × M_POA,fresh / τ_SOA
```

- Y = 0.04 g SOA per g CO (Kiely 2019/2020, after Hodzic & Jimenez 2011).
- EF_CO peat = 255 g/kg (Huijnen peat-only; range 238–291).
- So the asymptotic SOA/POA ≈ 0.04 × 255 / 22.3 ≈ 0.46 [D].
- τ_SOA = 1 day is a placeholder (**UNVERIFIED**; Hodzic & Jimenez's actual time constant was not checked). Report results with SOA ON (Kiely) and OFF (Aouizerats) as the range.

**Browser path: source–receptor matrix (SRM)**

Offline, run LPM-3L with unit emissions from each source region × release day for Jul–Oct 2010–2023 ERA5. Store:

```
M[r, s, month, lag]  (µg m⁻³ per kg/day)
```

- r = receptor city (12 in `receptors.json`).
- s = source region (province × peat/non-peat).
- lag = 0–7 days.

The browser then computes `C_r(day) = Σ_s Σ_lag M[r,s,m,lag] · E_s(day − lag)`. This is the linear framework of Kim 2015 and Koplitz 2016, and it is valid because LPM-3L is linear in emissions. The animated 3D view can run the particle model directly over a reduced domain.

**What this gives up compared with GEOS-Chem / WRF-Chem**

- No aerosol–radiation or aerosol–cloud feedback (smoke suppressing rainfall, §2 caveat).
- No convective venting or explicit cloud processing; wet removal is a single calibrated coefficient.
- No nonlinear chemistry: SOA is a fixed yield, and POA is non-volatile, as in Kiely.
- No sea-breeze or terrain circulation below the ERA5 0.25° scale.
- One climatological SRM per month rather than a re-run for every year's meteorology (unless the SRM is built per year).

**Validation targets** (§8 has the data)

| Target | Value | Source |
|---|---|---|
| Singapore Jul–Oct 2015 mean fire smoke | 27–30 µg m⁻³ (definition: OC+BC smoke) | Koplitz |
| Singapore Sep–Nov 2015 mean PM2.5 | **52** µg m⁻³ observed | Crippa |
| Singapore 1-h PM2.5 maximum, 19 Oct 2015 23:00 | 471 µg m⁻³ | NEA |
| Pekanbaru Sep–Nov 2015 mean PM10 | 174 µg m⁻³ | Crippa |
| Kuching fire PM share from Borneo | ≈ 85% | Lee 2017 |
| Singapore Aug–Oct 2015 source split | Sumatra ≈ 28–45%, Kalimantan ≈ 38–51%, Bangka 11–14% | Hansen 2019 |
| Palangka Raya 24-h PM2.5, Sep–Oct 2015 | "nearly 400" µg m⁻³ | Santoso 2022 |

**Effect.** Singapore and KL go from "this model cannot tell you" to quantitative seasonal and daily estimates. Kuching is driven by Borneo sources, consistent with Lee 2017. Absolute near-source values fall, because daily emissions replace the summed synthetic season.

---

## 6. Diurnal boundary layer over Borneo

### 6.1 Current implementation

`haze3d.py` scales a user-supplied daily-mean mixing height (default 800 m) by an analytic profile:

- Night: 0.28× the daily mean.
- Afternoon peak: 1.85×.
- Floor 120 m, ceiling 3,000 m.
- One profile for every location.

### 6.2 What the literature and reanalysis say

**Peer-reviewed observations.** None were found with hourly boundary-layer height (BLH) for Palangka Raya, Pekanbaru or Kuching in haze season. The closest:

- Rahman et al. 2021, Sci. Rep. 11:367, doi:10.1038/s41598-020-79486-6. C-band radar at Palangka Raya, Oct 2015. Daytime echo layer (BL or smoke top) below 2–3 km. Visibility fell to 300 m just before sunrise [V].
- Dias-Júnior et al. 2022, *Remote Sens.* 14:4561, the nearest tropical-forest analogue (Amazon, including 2015) [V]:
  - ERA5 daytime BLH is biased −30% to +12% depending on site.
  - ERA5 night values show "no significant correlation" with observations, sit around 250 m, and underestimate by about 90 m.
- Guo et al. 2021, ACP 21:17079: ERA5 underestimates daytime BLH by ~130 m globally. No Maritime Continent or night-time results [V].

**ERA5 analysis done for this spec** [D]

- Source: ERA5 via the Open-Meteo archive.
- Period: Aug–Oct 2010–2023, local time.
- Night = 02–05 LT mean; afternoon = 13–15 LT mean.
- Files: `research/data/era5_blh_summary.*`.

| Site (ERA5 cell) | Daily mean (m) | Night / mean | Afternoon / mean | Night hours < 120 m |
|---|---|---|---|---|
| Palangka Raya (−2.25, 114.0) | 488 (296–697 across years) | **0.28** (0.25–0.36) | **2.46** (2.30–2.58) | 51% |
| Pontianak (0.0, 109.25), coastal | 358 | 0.46 | 2.13 | 40% |
| Kuching (1.5, 110.25) | 278 | **0.17** | **3.20** | 93% |
| Singapore (1.5, 103.75), coastal | 386 | 0.47 | 2.02 | 27% |
| Pekanbaru (0.5, 101.5) | 323 | 0.25 | 2.96 | 80% |

- In fire years Palangka Raya's daily mean is **640–697 m** (2015: 691; 2019: 640; 2023: 697), about 1.4× climatology.
- The diurnal minimum is at 05 LT and the maximum at 13 LT (1,319 m composite).
- The 95th percentile is ≤ 2,130 m. Hours above 3,000 m are ≤ 0.14%.

Normalised hourly profiles, h = 00…23 LT (value = BLH(h) / daily mean):

```
palangkaraya: .36 .35 .33 .31 .29 .23 .27 .59 .99 1.51 2.03 2.41 2.65 2.70 2.57 2.14 1.44 .51 .40 .39 .39 .39 .39 .38
pontianak:    .45 .46 .46 .47 .48 .44 .45 .63 .93 1.33 1.78 2.16 2.39 2.40 2.19 1.78 1.33 .79 .62 .55 .51 .49 .47 .46
kuching:      .16 .16 .16 .17 .17 .18 .16 .18 .36 .87 1.60 2.32 2.91 3.29 3.35 2.97 2.09 1.16 .50 .38 .28 .22 .19 .17
singapore:    .46 .45 .45 .46 .47 .48 .44 .45 .61 1.09 1.45 1.76 1.99 2.08 2.04 1.93 1.79 1.63 1.19 .74 .57 .52 .49 .47
pekanbaru:    .24 .24 .25 .26 .26 .24 .23 .30 .61 1.18 1.86 2.46 2.89 3.10 3.07 2.70 1.88 .60 .34 .29 .27 .26 .25 .24
```

### 6.3 Gap

- The night factor 0.28 matches inland Kalimantan by coincidence. It is too high for Kuching (0.17) and too low for the coastal cells (~0.46).
- The afternoon factor 1.85 is **too low everywhere**. ERA5 gives 2.0–3.2; Palangka Raya gives 2.46. At Palangka Raya the model's afternoon concentrations are therefore too high by 2.46/1.85 ≈ **1.33×** [D].
- The 800 m default daily mean is above the ERA5 Palangka Raya climatology (488 m) but close to fire-year values (640–697 m).
- The 3,000 m ceiling is never reached in ERA5.

### 6.4 Specified change

**H1. Profiles from ERA5**

- Use the tabulated hourly profiles above. Assign each grid point the profile of its region type:
  - Inland Borneo peat → Palangka Raya.
  - Coastal West Kalimantan → Pontianak.
  - Sarawak → Kuching.
  - Sumatra peat → Pekanbaru.
  - Singapore / Peninsular coast → Singapore.
- Better: in the LPM (§5), read ERA5 hourly BLH directly at each particle's position and time, so no profile is needed.

**H2. Daily mean defaults**

- Default daily mean = ERA5 Aug–Oct climatology for the region.
- "Fire-year" preset = 1.4× climatology (Palangka Raya 2015/2019/2023 mean 676 m) [D].

**H3. Floor and ceiling**

- Floor: 100 m [J], range 50–150. ERA5 night values of 30–80 m are plausible but likely low-biased by ~90 m (Dias-Júnior 2022, Amazon). Applying 120 m would bind 51–93% of night hours, which silently changes the profile.
- Ceiling: 2,500 m [D, ≥ ERA5 99.9th percentile].

**H4. Bias note to display**

ERA5 nocturnal BLH is a weakly constrained diagnostic. Night-time concentrations near sources are therefore uncertain by a factor of about 2 [J].

**Effect.** At Palangka Raya, afternoon concentrations fall by ~25%. At Kuching, night concentrations rise by 65% (0.28 → 0.17 means a 1.65× smaller night volume) and afternoon concentrations fall by 42%. The day–night contrast at Kuching becomes about 19:1 rather than 6.6:1 [D: 3.20/0.17 vs 1.85/0.28].

---

## 7. Health impact

### 7.1 Current implementation

- `seasons.json` stores `excess_deaths_est = 100300` for 2015 and 37,600 for 2006, both from Koplitz.
- The Methods text says "on the order of 100,000 excess deaths".
- The history chart and map popups show the single figure.
- There is no per-scenario health calculation.

### 7.2 What the literature says

**2015 excess-death estimates**

| Study | Estimate | Method | Status |
|---|---|---|---|
| **Koplitz et al. 2016**, ERL 11:094023 | **100,300** total. Indonesia 91,600 (24,000–159,200); Malaysia 6,500 (1,700–11,300); Singapore 2,200 (600–3,800). 2006: 37,600 | GEOS-Chem adjoint (2006 meteorology) × GFAS × 1.5. **Jul–Oct exposure**, adult mortality. Long-term CRF from high-income-country cohorts, linear above 50 µg m⁻³ | [V]. CRF identity, RR and age cutoff are in the SI and **UNVERIFIED**. The total range is not stated by the authors; the sum of country bounds is 26,300–174,300 [D]. |
| **Kiely et al. 2020**, ERL 15:094054 | **44,040** (Table 2: 44,041; 95% UI 34,672–53,948). 131,700 for 2004–2015 combined | WRF-Chem 30 km, FINNpeatSM. **GEMM** NCD+LRI, adults. Annual-mean fire PM2.5 increment | [V]. Applying Koplitz's function to Kiely's PM gives **106,000**: "the largest uncertainty in health impacts is due to uncertainty in exposure response function". |
| **Crippa et al. 2016**, Sci. Rep. 6:37074 | **11,880 (6,153–17,270)** short-term; **~75,600** long-term-equivalent | WRF-Chem 10 km. Short-term: daily PM2.5 × Atkinson 2014 all-cause β = 0.00104 per µg m⁻³, Sep–Oct. Long-term: IER on an annual mean | [V]. Both numbers are correct; they are different metrics. |
| Hein et al. 2022, *Environ. Health* 21:62 | ~33,100 adults + 2,900 infants per **average** year (CRF range 19,900–64,800) | Sumatra + Kalimantan, long-term | [V] |
| Marlier et al. 2013, NCC 3:131 | 10,800 (6,800–14,300) per strong El Niño year | **Adult cardiovascular** only, 1997–2006 | [V]. The "~15,000" often quoted is a news figure. |
| Jayachandran 2009, J. Human Resources 44:916 | 15,600 "missing children" from the 1997 fires | Infant/early-life mortality | [V] |

**Exposure–response functions**

**GEMM** (Burnett et al. 2018, PNAS 115:9592) [V]:

```
z = max(0, PM2.5 − 2.4)
HR(z) = exp( θ · log(1 + z/α) / (1 + exp(−(z − μ)/ν)) )
```

NCD+LRI, age 25+ parameters, from the authors' GEMM Calculator workbook [V]:

| Variant | θ (SE) | α | μ | ν |
|---|---|---|---|---|
| With China cohort | **0.1430** (0.01807) | 1.6 | 15.5 | 36.8 |
| Without China cohort | **0.1231** (0.01797) | 1.5 | 10.4 | 25.9 |

- Uncertainty is θ ± 2 SE.
- Excluding the China cohort lowers excess mortality by about 14%.
- Age-specific θ (with China) runs from 0.1585 (25–29) to 0.1141 (80+).

**Short-term CRF** (Atkinson et al. 2014, *Thorax* 69:660) [V]:

- All-cause **+1.04% (0.52–1.56%) per 10 µg m⁻³** of daily PM2.5.
- Respiratory 1.51%; cardiovascular 0.84%.

**Is smoke PM more toxic?** Aguilera et al. 2021, *Nat. Commun.* 12:1493 [V]: respiratory hospitalisations rise **1.3–10% per 10 µg m⁻³** for wildfire PM2.5, vs 0.67–1.3% for non-fire PM2.5, in California. This is morbidity, not mortality, and not peat smoke. Cite it as a reason the range could be higher, not as a multiplier.

No dedicated methods paper critiquing long-term CRFs applied to episodic haze was found. The disagreement is visible directly in Crippa's two metrics (×6).

### 7.3 Gap

The site shows **one** figure, the highest of four peer-reviewed central estimates, with no metric or interval. The studies differ by a factor of about 8 (11,880 to 100,300). Most of the spread comes from the CRF choice, and Kiely 2020 demonstrates this explicitly.

### 7.4 Specified change

**M1. Presentation (mandatory)**

Replace the single value with a table. Headline wording: "Published estimates of deaths attributable to the 2015 haze range from about **12,000 to 100,000**. They differ mainly in how they convert smoke exposure into risk."

| Study | Deaths (2015) | Interval | What it counts |
|---|---|---|---|
| Crippa et al. 2016 | 11,880 | 6,153–17,270 | Short-term (daily) exposure, Sep–Oct, all-cause |
| Kiely et al. 2020 | 44,040 | 34,672–53,948 | Long-term exposure function (GEMM) applied to the 2015 fire-season PM |
| Crippa et al. 2016 | ~75,600 | not given | As if the Sep–Oct exposure were sustained long-term (IER) |
| Koplitz et al. 2016 | 100,300 | country intervals sum to 26,300–174,300 | Long-term function, Jul–Oct exposure, ID+MY+SG |

- In `seasons.json`, store `excess_deaths` as `{low: 11880, high: 100300, estimates: [...]}`. Keep 2006 as `{estimates: [Koplitz 37,600; Kiely 22,100]}`. Kiely's 2006 value is [V] per the health agent's reading of Kiely 2020.
- Charts must show a bar or interval, not a point.
- Change the Methods paragraph from "on the order of 100,000" to the range.

**M2. Per-scenario estimate (optional; feasible offline or in the browser)**

Inputs:

- Gridded population aged 25+.
- Baseline NCD+LRI mortality rate y0 by country. GBD 2019/2021 values are **UNVERIFIED**; download from the GBD Results Tool and check IHME terms.
- Fire-PM2.5 increment from the LPM/SRM.
- Background C_bg.

```
long-term (GEMM):
  C_fire_annual(cell) = Σ_days C_fire(cell, day) / 365
  AF(cell) = 1 − HR(C_bg) / HR(C_bg + C_fire_annual)
  ΔM = Σ_cells pop25+ × y0 × AF
  report θ ± 2SE and with/without-China variants as the interval

short-term (Atkinson):
  ΔM_st = Σ_cells Σ_days D_daily(cell) × (1 − exp(−0.00104 × C_fire(cell, day)))
  D_daily = all-cause deaths/365 per cell (UNVERIFIED source; WHO/GBD)
  β interval 0.00052–0.00156
```

- Always display both, labelled. Never sum them.
- Unit test: with 2015 emissions, the long-term result should fall within 30,000–110,000 and the short-term within 6,000–18,000 (the published spread) [J].
- C_bg is critical because GEMM is supralinear at low concentration. Use 10–15 µg m⁻³ as the range (Koplitz) and show sensitivity [V-sec].

---

## 8. Validation data

| Dataset | Variable / resolution | Coverage | Access & terms | Status |
|---|---|---|---|---|
| **NEA Singapore, Historical 1-hr PM2.5** (data.gov.sg `d_6c0d5fc34838b12472475fdb73c0af29`) | Hourly PM2.5, 5 regions | 2014-04-01 → 2026-08 | Singapore Open Data Licence v1.0; attribution required, commercial use OK | [V] |
| **NEA Singapore, Historical 24-hr PSI** (`d_b4cf557f8750260d229c49fd768e11ed`) | Hourly-updated 24-h PSI, 5 regions | 2014-04-01 → | Same | [V] |
| NEA pre-2014 and 3-h PSI | — | 1997–2013 | Not on data.gov.sg. The 2013 value of 401 (3-h PSI) is press-only | UNVERIFIED |
| Malaysia DOE APIMS / EQMS (apims.doe.gov.my → eqms.doe.gov.my) | Hourly API, ~68 stations, real-time | Historical by request to DOE | Terms UNVERIFIED | [V redirect] |
| data.gov.my "Monthly Air Pollution" | National monthly means (PM2.5, PM10, …) | 2017-01 → 2024-12 | CC BY 4.0. Not useful for 2015 station validation | [V] |
| BMKG PM2.5 (beta-attenuation monitors; Palangka Raya listed) | Hourly | Start year UNVERIFIED | No bulk download or licence found | partial |
| KLHK ISPU (ispu.menlhk.go.id; Permen LHK P.14/2020 requires hourly PM2.5) | Hourly ISPU | — | Did not respond; terms UNVERIFIED | UNVERIFIED |
| Palangka Raya 2015 peaks | PM10 1,889 µg m⁻³ (Jakarta Post, 17 Oct 2015); ISPU/PM10 "1,912" (26 Sep 2015, units muddled); 24-h PM2.5 "nearly 400" and PM10 ~800 µg m⁻³ (Santoso 2022) | — | Press (grey); Santoso peer-reviewed | [V-sec / V] |
| **AERONET** sites: Palangkaraya (113.946, −2.228); Pontianak (109.191, 0.075); Kuching (110.349, 1.491) and Kuching_Swinburne; Singapore (103.780, 1.298); Jambi (103.642, −1.632); USM_Penang. No "Kuala_Lumpur" site | AOD, inversions | Years UNVERIFIED (server refused after the first fetch) | AERONET data policy; terms UNVERIFIED | [V sites] |
| Eck et al. 2019, JGR 124:4722 | Palangkaraya 2015: AOD(675 nm) up to ~7; estimated AOD(550) 11–13; SSA ~0.975 | 2015 | — | [V] |
| MODIS MAIAC MCD19A2 v061 | 1 km AOD | 2000-02 → | NASA open | [V] |
| MODIS MOD04/MYD04 C6.1 (Dark Target + Deep Blue) | 10 km (3 km DT) | Terra 2000-02, Aqua 2002-07 | NASA open | [V] |
| VIIRS Deep Blue AERDB_L2 SNPP v2 | 6 km | 2012-03 → | NASA open | [V] |
| Himawari-8 AHI AOD (JAXA P-Tree v3.1) | 5 km, 10 min | from 7 Jul 2015 | Registration; **non-profit research/education only; no redistribution** | [V] |
| CAMS EAC4 reanalysis | 0.75°, 3-hourly aerosols | 2003–2025 | CC-BY | [V] |
| CAMS GFAS | 0.1° daily fire emissions | 2003 → | CC-BY | [V] |
| MERRA-2 M2T1NXAER | 0.5°×0.625°, hourly | 1980 → | NASA open | [V] |
| Open-Meteo Air Quality (CAMS global) | 0.4°, 3-hourly PM2.5 and AOD | from Aug 2022 | Attribution to CAMS and Open-Meteo | [V] |
| FIRMS active fire (MODIS C6.1, VIIRS) | 1 km / 375 m points with FRP | MODIS 2000-11 →; VIIRS SNPP 2012-01 →; NOAA-20 2018-04 → | Earthdata login for archive; API needs MAP_KEY | [V] |
| US Embassy Jakarta monitors | Hourly PM2.5 | ~2015 → **4 Mar 2025, when transmission stopped** | — | partial |
| OpenAQ v3 | Aggregated stations | — | API key; per-source licences | partial |

**Known retrieval caveat.** Shi et al. 2019, ACP 19:259 [V]: MODIS Dark Target cloud masks "mistook heavy smoke plumes for clouds". Operational DT missed ~0.22 of regional mean AOD, and up to AOD 3.0 in 0.5° boxes, during 2015. AERONET V2 cloud screening also removed many high-AOD points. Validation against satellite AOD therefore under-samples the worst days.

**Validation protocol** [J]:

1. For each of 2015, 2019 and 2023, run E2 + LPM-3L. Compare daily-mean PM2.5 with NEA (5 regions), with model minus a non-fire background (June/December baseline, the Kim 2015 approach).
2. Report normalised mean bias factor (NMBF), daily r, and the fraction within a factor of 2.
3. Literature skill for context:

   | Study | Skill |
   |---|---|
   | Crippa | NMBF −0.15, r 0.55 at Singapore |
   | Kiely 2019 | MFB −0.11 |
   | Kiely 2020 | NMBF 0.14, r 0.43 |
   | Hansen | r 0.79–0.80 |
   | Reddington | NMB −64% |

4. Compare model AOD at the AERONET sites, and MAIAC with the smoke-mask caveat.
5. Publish the scores on the site next to the map.

---

## 9. Implementation order and unit-test targets

| Order | Item | Test |
|---|---|---|
| 1 | Data fixes §0.4 (PSI/PM2.5 labels, EF attribution, 2019 phase, stale caveat, mortality range) | Schema test: `psi_peak` contains only 24-h PSI with a metric field; `excess_deaths` is a range object |
| 2 | E1 per-hectare emissions | First-fire peat ha = 6.38 t PM2.5 ± tolerance; repeat-fire ratios 0.56/0.33/0.11 |
| 3 | B1–B3 burned-area ranges + GFED5 series | 2015 Indonesia low 2.61 / high 4.60; 2019 low 1.65 / high 3.8 |
| 4 | F1–F4 peat column + subsidence | Shallow drained class with no fire reaches z = 0 in 0.75/0.043 ≈ 17 yr |
| 5 | S1–S6 season model (FIRMS labels, DC, hinge, DMI) | DC implementation matches cffdrs on a reference series; 2019 forecast from 1 Aug 2019 is longer than the ENSO-only model's |
| 6 | E2 + LPM-3L + SRM | 2015 Sep–Oct total primary PM2.5 in 4–11 Tg; Singapore Jul–Oct 2015 mean fire PM within ×2 of 27–30 µg m⁻³; Kuching Borneo share > 70% |
| 7 | H1–H4 BLH | Palangka Raya profile mean = 1.00; afternoon/mean = 2.46 ± 0.05 |
| 8 | M1–M2 health | 2015 GEMM within 30,000–110,000; short-term within 6,000–18,000 |

## Appendix A: Items that remain UNVERIFIED (do not publish as settled)

- Page et al. 2002 mean burn depth 0.51 ± 0.05 m (secondary only; no ±).
- Page et al. 2011 Indonesia area/volume in the final version; bulk density ~0.09 g cm⁻³ and C ~56%.
- Huijnen 2016 peat fraction of 2015 carbon (likely in the SI).
- Anda et al. 2021 national 13.43 Mha and island split; Kalimantan ~4.5 Mha.
- Miettinen 2016 Borneo-only class breakdown.
- Margono 2014 Kalimantan split and the 43% wetland share (WRI blog only).
- Dommain et al. 2011 accumulation rates (search snippet only).
- Lohberger 2018 peat hectares, its MCD64/GFED comparison numbers, and KLHK comparison (abstract only).
- KLHK 2015 peat split (869,754 ha; press only); SiPongi annual table read via GoodStats, not directly.
- Auriga 2023/2024 Kalimantan deforestation figures.
- Koplitz 2016 CRF identity, RR, age range (SI).
- IER 2014 parameter values.
- GBD baseline mortality rates and the IHME licence for redistribution.
- Dry-deposition velocity 0.1–0.2 cm/s for accumulation-mode smoke; below-cloud scavenging Λ0; K_h.
- SOA formation time constant τ_SOA.
- Huang et al. 2024 Singapore lidar BLH and low-level-jet numbers.
- Field et al. 2004 DC level boundaries other than 388.2; de Groot 2007 "DC 400 / FFMC 82" (secondary).
- BoM IOD threshold of +0.4 °C.
- ERA5 BLH diagnostic definition (critical bulk Richardson number 0.25).
- AERONET site operating years and data policy; BMKG and ISPU archive access; APIMS terms.
- Current model constants with no traceable source: `ENSO_RAIN_K = 0.35`, `MEAN_WET_DAY_MM = 13`, `WET_PERSISTENCE = 0.35`, forest and scrub fuel loads 55/75/12 t/ha, `burn_susceptibility` values, `PEAT_DRAINAGE_COUPLING = 0.25`, `THETA_DEPLETION = 0.6`.
- Singapore 2013 3-h PSI 401 and the metric of the 1997 (226) and 2006 (150) values.
