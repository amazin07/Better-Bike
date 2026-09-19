from dataclasses import asdict
import gzip
import json
from pathlib import Path

import networkx as nx
import pytest
from shapely.geometry import LineString

from app import create_app
from graph_bundle import export_bundle, load_bundle
from routing import Collision, Router


def test_export_preserves_routes_parallel_edges_and_collision_deduplication(tmp_path):
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=-79.39, y=43.66, collision_ids=["private-source-id"])
    graph.add_node(2, x=-79.389, y=43.66, collision_ids=["private-source-id"])
    graph.add_edge(1, 2, key=7, length=150, name="Long street")
    line = LineString([(-79.39, 43.66), (-79.3895, 43.6601), (-79.389, 43.66)])
    graph.add_edge(1, 2, key=9, length=100, geometry=line, name="Short street")
    record = Collision("private-source-id", 43.66, -79.39, 2024, 23, True, injury="OMIT")
    bundle = {"graph": graph, "records": [asdict(record)], "metadata": {}}
    path = tmp_path / "graph.json.gz"
    export_bundle(bundle, path)
    raw = gzip.decompress(path.read_bytes()).decode()
    assert "private-source-id" not in raw and "OMIT" not in raw
    restored = load_bundle(path)
    assert set(restored["graph"][1][2]) == {7, 9}
    assert not restored["graph"].has_edge(2, 1)
    assert list(restored["graph"][1][2][9]["geometry"].coords) == list(line.coords)
    original = Router(graph, [record], {})
    portable = Router(restored["graph"], [Collision(**r) for r in restored["records"]], {})
    for hour in (0, 12, 23):
        for level in (1, 2, 3):
            assert original.route([43.66, -79.39], [43.66, -79.389], level, hour) == portable.route(
                [43.66, -79.39], [43.66, -79.389], level, hour)


def test_committed_bundle_serves_routes_without_local_pickle(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("HERE_API_KEY", raising=False)
    client = create_app().test_client()
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json["graph_nodes"] > 10000
    result = client.post("/api/route", json={"origin": [43.6629, -79.3957],
        "destination": [43.6453, -79.3806], "level": 1, "hour": 23})
    assert result.status_code == 200


def test_unknown_bundle_version_is_rejected(tmp_path):
    path = tmp_path / "graph.json.gz"
    path.write_bytes(gzip.compress(json.dumps({"version": 999}).encode()))
    with pytest.raises(ValueError, match="version"):
        load_bundle(path)
