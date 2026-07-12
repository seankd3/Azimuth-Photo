"""Build js/search-data.js from live /api/search captures.

Run from the site root. For each query it hits the live archive over SSH,
keeps only hits inside the Selected Landscapes collection (rank order
preserved), and records which engines actually answered. Re-run any time —
e.g. after the semantic model finishes loading — to refresh the rankings.
"""
import json, subprocess, urllib.parse, pathlib

# "sunset over water" exercises the AI engines (captions + embeddings);
# the others are honest metadata hits (folder / filename trigram FTS).
# Add more semantic queries here once the embedding model is up, then re-run.
QUERIES = [
    "sunset over water",
    "costa rica",
    "beacon",
    "skd-west",
]
API = "http://100.102.150.104:8000/api/search"

root = pathlib.Path(__file__).resolve().parent.parent
collection = {r["id"] for r in json.load(open(root / "assets/collection_raw.json"))}

out = []
for q in QUERIES:
    url = f"{API}?q={urllib.parse.quote(q)}&limit=500"
    raw = subprocess.run(["ssh", "omarchy", f'curl -s "{url}"'], capture_output=True, text=True).stdout
    d = json.loads(raw)
    ids = [im["id"] for im in d.get("images", []) if im["id"] in collection][:12]
    out.append({
        "q": q,
        "ids": ids,
        "sources": d.get("search_sources", []),
        "ms": round(d.get("latency_ms", 0)),
        "ai": not d.get("ai_unavailable", True),
    })
    print(f"{q!r}: {len(ids)} in-scope hits · sources={out[-1]['sources']} · {out[-1]['ms']}ms")

kept = [s for s in out if s["ids"]]
with open(root / "js/search-data.js", "w") as f:
    f.write("// Captured from the live archive's /api/search — rank order preserved.\n")
    f.write("export const SEARCHES = " + json.dumps(kept, separators=(",", ":")) + ";\n")
print(f"wrote js/search-data.js with {len(kept)} queries")
