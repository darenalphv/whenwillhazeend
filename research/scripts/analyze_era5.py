import json, os, sys, math, statistics as st
from collections import defaultdict
D = sys.argv[1]
SITES = ["palangkaraya","pontianak","kuching","singapore","pekanbaru"]
FOCUS = [2015, 2019, 2023]
def load(name, yr):
    d = json.load(open(os.path.join(D, f"{name}_{yr}.json")))
    return d
def mean(x): return sum(x)/len(x)
out = {}
for s in SITES:
    print("="*70); 
    hourly_all = defaultdict(list); yrstats = {}
    meta=None
    u10a=[];v10a=[];spd10=[];u100a=[];v100a=[];spd100=[]
    for yr in range(2010,2024):
        d = load(s, yr); h = d["hourly"]
        if meta is None: meta=(d["latitude"],d["longitude"],d["timezone"],d["elevation"])
        byhour = defaultdict(list); byday = defaultdict(list)
        for t,b in zip(h["time"], h["boundary_layer_height"]):
            if b is None: continue
            hr = int(t[11:13]); byhour[hr].append(b); byday[t[:10]].append(b)
            hourly_all[hr].append(b)
        dm = [mean(v) for v in byday.values() if len(v)==24]
        daily_mean = mean(dm)
        hm = {k: mean(v) for k,v in byhour.items()}
        night = mean([hm[k] for k in (2,3,4,5)])
        aft = mean([hm[k] for k in (13,14,15)])
        mn_hr = min(hm, key=hm.get); mx_hr = max(hm, key=hm.get)
        # per-day ratios
        dn=[];da=[]
        for day,v in byday.items():
            if len(v)!=24: continue
            m=mean(v); dn.append(mean(v[2:6])/m); da.append(mean(v[13:16])/m)
        # fraction of hours below 120 m and above 3000
        allv=[b for b in h["boundary_layer_height"] if b is not None]
        yrstats[yr]=dict(daily_mean=daily_mean, night_0205=night, aft_1315=aft, r_night=night/daily_mean, r_aft=aft/daily_mean,
                          min_hr=mn_hr, min_val=hm[mn_hr], max_hr=mx_hr, max_val=hm[mx_hr],
                          r_night_daymed=st.median(dn), r_aft_daymed=st.median(da),
                          frac_lt120=sum(1 for b in allv if b<120)/len(allv), frac_gt3000=sum(1 for b in allv if b>3000)/len(allv),
                          p05=sorted(allv)[int(0.05*len(allv))], p95=sorted(allv)[int(0.95*len(allv))], absmax=max(allv), absmin=min(allv))
        for ws,wd,U,V,SP in ((h["wind_speed_10m"],h["wind_direction_10m"],u10a,v10a,spd10),(h["wind_speed_100m"],h["wind_direction_100m"],u100a,v100a,spd100)):
            for a,b in zip(ws,wd):
                if a is None or b is None: continue
                r=math.radians(b); U.append(-a*math.sin(r)); V.append(-a*math.cos(r)); SP.append(a)
    print(s, "grid lat/lon/tz/elev:", meta)
    print("Hourly mean BLH (m), 2010-2023 Aug-Oct, local clock time:")
    hm_all = {k: mean(v) for k,v in sorted(hourly_all.items())}
    print("  "+" ".join(f"{k:02d}:{v:.0f}" for k,v in hm_all.items()))
    print(f"{'yr':>5} {'dmean':>6} {'n0205':>6} {'a1315':>6} {'rN':>5} {'rA':>5} {'rNmed':>6} {'rAmed':>6} {'minhr':>5} {'minv':>5} {'maxhr':>5} {'maxv':>5} {'<120':>5} {'>3000':>6} {'p05':>5} {'p95':>5} {'absmax':>6}")
    for yr,v in yrstats.items():
        tag = "*" if yr in FOCUS else " "
        print(f"{yr}{tag} {v['daily_mean']:6.0f} {v['night_0205']:6.0f} {v['aft_1315']:6.0f} {v['r_night']:5.2f} {v['r_aft']:5.2f} {v['r_night_daymed']:6.2f} {v['r_aft_daymed']:6.2f} {v['min_hr']:5d} {v['min_val']:5.0f} {v['max_hr']:5d} {v['max_val']:5.0f} {v['frac_lt120']:5.3f} {v['frac_gt3000']:6.4f} {v['p05']:5.0f} {v['p95']:5.0f} {v['absmax']:6.0f}")
    rn=[v['r_night'] for v in yrstats.values()]; ra=[v['r_aft'] for v in yrstats.values()]; dmv=[v['daily_mean'] for v in yrstats.values()]
    print(f"2010-2023: daily mean {mean(dmv):.0f} m (range {min(dmv):.0f}-{max(dmv):.0f}); night/mean {mean(rn):.2f} (range {min(rn):.2f}-{max(rn):.2f}, sd {st.pstdev(rn):.2f}); aft/mean {mean(ra):.2f} (range {min(ra):.2f}-{max(ra):.2f}, sd {st.pstdev(ra):.2f})")
    for lab,U,V,SP in (("10m",u10a,v10a,spd10),("100m",u100a,v100a,spd100)):
        um,vm=mean(U),mean(V); vs=math.hypot(um,vm); dirn=(math.degrees(math.atan2(-um,-vm))+360)%360
        print(f"Wind {lab} Aug-Oct 2010-23: mean scalar speed {mean(SP):.2f} m/s; vector-mean {vs:.2f} m/s from {dirn:.0f} deg; steadiness {vs/mean(SP):.2f}")
    out[s]=dict(hourly=hm_all, years=yrstats)
json.dump(out, open(os.path.join(os.path.dirname(D.rstrip('/')),"era5_blh_summary.json"),"w"), indent=1)
