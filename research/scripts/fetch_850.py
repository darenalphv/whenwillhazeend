import json, os, sys, time, math, urllib.request
OUT=sys.argv[1]
SITES={"palangkaraya":(-2.21,113.92),"pontianak":(-0.03,109.34),"kuching":(1.55,110.36),"singapore":(1.35,103.82),"pekanbaru":(0.51,101.45),"karimata_strait":(-1.5,108.5),"java_sea":(-4.5,111.0)}
res={}
for model in ("gfs_global","ecmwf_ifs025"):
  for name,(la,lo) in SITES.items():
    for yr in (2022,2023,2024,2025):
        fn=os.path.join(OUT,f"{model}_{name}_{yr}.json")
        if not os.path.exists(fn):
            url=(f"https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={la}&longitude={lo}&start_date={yr}-06-01&end_date={yr}-10-31"
                 f"&hourly=wind_speed_850hPa,wind_direction_850hPa,wind_speed_925hPa,wind_direction_925hPa&models={model}&wind_speed_unit=ms&timezone=GMT")
            open(fn,"wb").write(urllib.request.urlopen(url,timeout=60).read()); time.sleep(1)
        d=json.load(open(fn))['hourly']
        for lev in ("850","925"):
          for mon in ("06","07","08","09","10"):
            U=[];V=[];S=[]
            for t,s,dd in zip(d['time'],d[f'wind_speed_{lev}hPa'],d[f'wind_direction_{lev}hPa']):
                if s is None or dd is None or t[5:7]!=mon: continue
                r=math.radians(dd);U.append(-s*math.sin(r));V.append(-s*math.cos(r));S.append(s)
            if not S: continue
            res.setdefault((model,name,lev,mon),[]).append((yr,len(S),sum(S)/len(S),sum(U)/len(U),sum(V)/len(V)))
for model in ("gfs_global","ecmwf_ifs025"):
  print("#",model)
  for name in SITES:
    for lev in ("925","850"):
      row=[]
      for mon in ("06","07","08","09","10"):
        L=res.get((model,name,lev,mon),[])
        if not L: row.append(f"{mon}: n/a"); continue
        sp=sum(x[2] for x in L)/len(L); u=sum(x[3] for x in L)/len(L); v=sum(x[4] for x in L)/len(L)
        dirn=(math.degrees(math.atan2(-u,-v))+360)%360
        row.append(f"{mon}: {sp:4.1f} m/s, vec {math.hypot(u,v):4.1f} from {dirn:3.0f} [{','.join(str(x[0]) for x in L)}]")
      print(f"{name:16s} {lev}hPa | "+" | ".join(row))
