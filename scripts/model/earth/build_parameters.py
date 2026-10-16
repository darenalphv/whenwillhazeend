"""Copy the parameters the MODEL-EARTH code uses from research/PARAMETERS.json into
app/data/earth/parameters.json, so the shipped app never reads research/ at runtime
and every number keeps its source, DOI and `verified` flag.

Usage: .venv/bin/python scripts/model/earth/build_parameters.py
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "research" / "PARAMETERS.json"
DST = ROOT / "app" / "data" / "earth" / "parameters.json"

USED_BY = ("emissions", "stock", "transport", "boundary_layer", "health", "validation",
           "burned_area", "winds")


def main():
    src = json.loads(SRC.read_text())
    keep = [p for p in src["parameters"]
            if any(u in str(p.get("used_by", "")) for u in USED_BY)
            and "season_end" not in str(p.get("used_by", ""))]
    out = {
        "generated_from": "research/PARAMETERS.json",
        "research_generated": src.get("generated"),
        "note": ("Subset used by app/model/{emissions,stock,transport,health,haze3d}.py. "
                 "verified=false values must never be presented as settled (MODEL_SPEC "
                 "Appendix A). Regenerate with scripts/model/earth/build_parameters.py."),
        "parameters": {p["id"]: {k: v for k, v in p.items() if k != "id"} for p in keep},
    }
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(keep)} parameters to {DST}")


if __name__ == "__main__":
    main()
