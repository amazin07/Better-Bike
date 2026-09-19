"""Build all routing artifacts from official sources; run once before serving."""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import requests

from routing import CENTER, attach_collisions, load_collisions, serialize_records

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show"
PACKAGES = {"ksi": "motor-vehicle-collisions-involving-killed-or-seriously-injured-persons",
            "cycling": "cycling-network"}


def download(name, extension, refresh=False):
    target = DATA / "raw" / f"{name}.{extension}"
    if target.exists() and not refresh:
        return target
    response = requests.get(CKAN, params={"id": PACKAGES[name]}, timeout=90)
    response.raise_for_status()
    package = response.json()["result"]
    resource = next(r for r in package["resources"] if r["name"].lower().endswith(f"4326.{extension}"))
    print(f"Downloading {resource['name']}", flush=True)
    result = requests.get(resource["url"], timeout=180)
    result.raise_for_status()
    partial = target.with_suffix(".tmp")
    partial.write_bytes(result.content)
    partial.replace(target)
    (DATA / "raw" / f"{name}-source.json").write_text(json.dumps({
        "url": resource["url"], "resource_id": resource["id"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "package_modified": package.get("metadata_modified")}, indent=2))
    return target


def infra_multiplier(value):
    value = str(value).lower()
    if any(tag in value for tag in ("cycle track", "multi-use trail", "bike trail", "protected")):
        return 0.55
    if "bike lane" in value or "bicycle lane" in value:
        return 0.80
    return 1.0


def apply_infrastructure(graph, path):
    network = gpd.read_file(path).to_crs(26917)
    network["multiplier"] = network.apply(lambda row: max(
        infra_multiplier(row.get("INFRA_LOWORDER", "")),
        infra_multiplier(row.get("INFRA_HIGHORDER", ""))), axis=1)
    network = network[network.multiplier < 1].reset_index(drop=True)
    edges = ox.graph_to_gdfs(graph, nodes=False).to_crs(26917)
    index = network.sindex
    counts = Counter()
    for (u, v, key), row in edges.iterrows():
        geometry = row.geometry
        multiplier = 1.0
        # Require sustained overlap: crossing a bike lane at a junction is not a lane.
        for candidate in index.query(geometry.buffer(12)):
            lane = network.iloc[candidate]
            overlap = geometry.intersection(lane.geometry.buffer(12)).length
            if geometry.length > 0 and overlap / geometry.length >= 0.65:
                multiplier = min(multiplier, lane.multiplier)
        graph[u][v][key]["lane_multiplier"] = float(multiplier)
        counts[multiplier] += 1
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Refresh official CSV/infrastructure (reuse graph)")
    parser.add_argument("--refresh-graph", action="store_true", help="Download the bounded street graph again")
    args = parser.parse_args()
    for folder in [DATA / "raw", DATA / "processed", ROOT / "static"]:
        folder.mkdir(parents=True, exist_ok=True)
    csv_path = download("ksi", "csv", args.refresh)
    records, counts = load_collisions(csv_path)
    print(f"KSI: {counts}", flush=True)
    if not records:
        raise RuntimeError("No cyclist collisions loaded; inspect the source schema.")
    graph_path = DATA / "raw" / "bike-graph.pkl"
    if graph_path.exists() and not args.refresh_graph:
        # Trusted local build artifact only. Never unpickle uploads or remote files.
        with graph_path.open("rb") as source:
            graph = pickle.load(source)
    else:
        ox.settings.cache_folder = str(DATA / "osm-cache")
        ox.settings.log_console = True
        ox.settings.requests_timeout = 180
        graph = ox.graph_from_point(CENTER, dist=4000, network_type="bike")
        with graph_path.open("wb") as target:
            pickle.dump(graph, target, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Graph: {len(graph):,} nodes / {graph.number_of_edges():,} edges", flush=True)
    west = min(d["x"] for _, d in graph.nodes(data=True))
    east = max(d["x"] for _, d in graph.nodes(data=True))
    south = min(d["y"] for _, d in graph.nodes(data=True))
    north = max(d["y"] for _, d in graph.nodes(data=True))
    local = [r for r in records if west <= r.lng <= east and south <= r.lat <= north]
    matched = attach_collisions(graph, local)
    print(f"Collisions in coverage: {len(local)}; within 30m of graph nodes: {matched}", flush=True)
    cycling_path = download("cycling", "geojson", args.refresh)
    lane_counts = apply_infrastructure(graph, cycling_path)
    print(f"Infrastructure edge multipliers: {lane_counts}", flush=True)
    metadata = {"built_at": datetime.now(timezone.utc).isoformat(), "city": "toronto",
                "graph_nodes": len(graph), "graph_edges": graph.number_of_edges(),
                "collision_records": len(local), "matched_collision_records": matched,
                "years": [min(r.year for r in records), max(r.year for r in records)],
                "bounds": [[south, west], [north, east]], "source_counts": counts,
                "infrastructure_edges": lane_counts,
                "sources": {name: {"catalogue": f"https://open.toronto.ca/dataset/{PACKAGES[name]}/",
                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                            for name, path in [("ksi", csv_path), ("cycling", cycling_path)]}}
    bundle = {"graph": graph, "records": serialize_records(local), "metadata": metadata}
    target = DATA / "processed" / "toronto.pkl"
    temporary = target.with_suffix(".tmp")
    with temporary.open("wb") as output:
        pickle.dump(bundle, output, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(target)
    # Public, minimal, real records remain available independently of the route API.
    (ROOT / "static" / "collisions.json").write_text(json.dumps({"metadata": metadata,
        "risk_points": [r.public() for r in local]}, separators=(",", ":")))
    (DATA / "processed" / "metadata.json").write_text(json.dumps(metadata, indent=2))
    print("Ready. Run .venv/bin/python app.py and open http://127.0.0.1:5001", flush=True)


if __name__ == "__main__":
    main()
