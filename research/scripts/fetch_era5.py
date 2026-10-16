import json, os, sys, time, urllib.request
OUT = sys.argv[1]
SITES = {
 "palangkaraya": (-2.21, 113.92, "Asia/Jakarta"),
 "pontianak": (-0.03, 109.34, "Asia/Jakarta"),
 "kuching": (1.55, 110.36, "Asia/Kuching"),
 "singapore": (1.35, 103.82, "Asia/Singapore"),
 "pekanbaru": (0.51, 101.45, "Asia/Jakarta"),
}
VARS = "boundary_layer_height,wind_speed_10m,wind_direction_10m,wind_speed_100m,wind_direction_100m"
for name,(lat,lon,tz) in SITES.items():
    for yr in range(2010, 2024):
        fn = os.path.join(OUT, f"{name}_{yr}.json")
        if os.path.exists(fn) and os.path.getsize(fn) > 1000: continue
        url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
               f"&start_date={yr}-08-01&end_date={yr}-10-31&hourly={VARS}&timezone={tz}&models=era5&wind_speed_unit=ms")
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    data = r.read()
                break
            except Exception as e:
                print("retry", name, yr, e); time.sleep(20)
        open(fn, "wb").write(data)
        print(name, yr, len(data)); time.sleep(1.5)
