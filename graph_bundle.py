"""Portable routing data: JSON, never executable pickle in hosted deployments."""
import gzip
import json

import networkx as nx
from shapely.geometry import LineString


def export_bundle(bundle, path):
    graph = bundle["graph"]
    # Local opaque IDs preserve deduplication without publishing source event IDs.
    ids = {row["id"]: str(i) for i, row in enumerate(bundle["records"])}
    nodes = [[n, {"x": float(d["x"]), "y": float(d["y"]),
                  "collision_ids": [ids[i] for i in d.get("collision_ids", [])]}]
             for n, d in graph.nodes(data=True)]
    edges = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        attrs = {k: data[k] for k in ("length", "lane_multiplier", "name") if k in data}
        if data.get("geometry") is not None:
            attrs["geometry"] = list(data["geometry"].coords)
        edges.append([u, v, key, attrs])
    records = [{**{k: row[k] for k in ("lat", "lng", "year", "hour", "fatal")},
                "id": ids[row["id"]]} for row in bundle["records"]]
    value = {"version": 1, "nodes": nodes, "edges": edges,
             "records": records, "metadata": bundle["metadata"]}
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(encoded, mtime=0))


def load_bundle(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        value = json.load(source)
    if value.get("version") != 1:
        raise ValueError("Unsupported routing bundle version")
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(value["nodes"])
    for u, v, key, attrs in value["edges"]:
        if "geometry" in attrs:
            attrs["geometry"] = LineString(attrs["geometry"])
        graph.add_edge(u, v, key=key, **attrs)
    return {"graph": graph, "records": value["records"], "metadata": value["metadata"]}
