"""Live "this week" data for Kuala Lumpur, Selangor and Singapore.

Three upstream sources, each in its own module, each failing on its own:

* :mod:`app.live.nea`   - Singapore NEA PSI and PM2.5 via data.gov.sg (official, measured)
* :mod:`app.live.cams`  - CAMS global air-quality model via Open-Meteo (modelled)
* :mod:`app.live.firms` - NASA FIRMS VIIRS active-fire detections (satellite)

Malaysia's official Air Pollutant Index (API / IPU) is *not* fetched: DOE publishes
it on the APIMS portal but, as of 2026-10-16, through no documented public
endpoint or open dataset with reuse terms (see :mod:`app.live.places`).

Importing this package does no I/O and no network. Every fetch happens on demand,
behind an in-process + on-disk TTL cache (:mod:`app.live.cache`).
"""


def warm_in_background() -> None:
    """Prefetch every source on a daemon thread (call once at startup if wanted).

    Cold fetches take ~1 s (NEA, CAMS points), ~3 s (CAMS field + wind) and
    3-10 s (the 6 MB FIRMS file). Failures are swallowed: the request path
    reports unavailability itself. Does nothing at import time. When the
    Open-Meteo policy is off (app.config.open_meteo_mode), CAMS and wind are
    not fetched - build_observed skips CAMS before any network call.
    """
    import threading

    def run() -> None:
        from app.live import build, cams

        fns = [build.build_observed, build.build_hotspots]
        if cams.mode() != "off":  # no Open-Meteo traffic at all under the off policy
            fns.insert(1, build.build_field)
        for fn in fns:
            try:
                fn()
            except Exception:  # noqa: BLE001 - warming is best-effort
                pass

    threading.Thread(target=run, name="live-warm", daemon=True).start()
