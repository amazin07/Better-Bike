import networkx as nx
import pytest
from shapely.geometry import LineString

from app import create_app
from routing import Collision, Router, attach_collisions, load_collisions, parse_hour, time_weight


def record(identifier="a", hour=8, fatal=False):
    return Collision(identifier, 43.66, -79.39, 2024, hour, fatal)


def graph():
    g = nx.MultiDiGraph(crs="EPSG:4326")
    for n, x, y in [(1, -79.3900, 43.6600), (2, -79.3890, 43.6600),
                     (3, -79.3895, 43.6605), (4, -79.3880, 43.6600)]:
        g.add_node(n, x=x, y=y, collision_ids=[])
    g.add_edge(1, 2, length=100)
    g.add_edge(2, 4, length=100)
    g.add_edge(1, 3, length=130)
    g.add_edge(3, 4, length=130)
    return g


def test_current_schema_groups_people_and_preserves_fatal(tmp_path):
    path = tmp_path / "ksi.csv"
    path.write_text("collision_id,cyclist,acclass,latitude,longitude,accdate\n"
                    "2024:1,false,Non-Fatal Injury,43.66,-79.39,2024-02-01T23:10:00\n"
                    "2024:1,true,Fatal Injury,43.66,-79.39,2024-02-01T23:10:00\n"
                    "2024:2,false,Non-Fatal Injury,43.66,-79.39,2024-02-01T08:00:00\n")
    records, counts = load_collisions(path)
    assert len(records) == 1 and records[0].fatal and records[0].hour == 23
    assert counts["person_rows"] == 3


def test_legacy_schema_and_missing_time(tmp_path):
    path = tmp_path / "ksi.csv"
    path.write_text("ACCNUM,CYCLIST,ACCLASS,LATITUDE,LONGITUDE,DATE,TIME\n"
                    "1,Yes,Fatal,43.66,-79.39,2019-01-01T00:00:00,0030\n"
                    "2,Yes,Non-Fatal Injury,43.66,-79.39,2019-01-01T00:00:00,\n")
    records, _ = load_collisions(path)
    assert records[0].hour == 0 and records[0].fatal
    assert records[1].hour is None


def test_invalid_rows_are_counted_and_missing_identifiers_not_merged(tmp_path):
    path = tmp_path / "ksi.csv"
    path.write_text("ACCNUM,CYCLIST,LATITUDE,LONGITUDE,DATE\n"
                    "1,Yes,nan,-79.39,2019-01-01\n"
                    ",Yes,43.66,-79.39,2019-01-01\n")
    records, stats = load_collisions(path)
    assert records == [] and stats["invalid_cyclist_records"] == 1


@pytest.mark.parametrize("value,result", [("0030", 0), ("2359", 23), ("08:30", 8),
                                          ("2400", None), ("1267", None), ("", None)])
def test_hour_parsing(value, result):
    assert parse_hour(value) == result


def test_midnight_and_missing_time():
    assert time_weight(23, 1) == 2
    assert time_weight(1, 23) == 2
    assert time_weight(22, 1) == 1
    assert time_weight(None, 0) == 1


def test_node_radius_and_unique_route_counts():
    g = graph()
    g.nodes[2]["x"] = -79.3899  # same collision attaches to two nearby nodes
    records = [record(), Collision("far", 43.70, -79.39, 2024, 8, False)]
    assert attach_collisions(g, records) == 1
    router = Router(g, records)
    route = router._route(1, 4, 0, 8)
    assert route["ksi_total"] == 1
    assert g.nodes[3]["collision_ids"] == []


def test_distance_baseline_risk_detour_and_level():
    g = graph()
    g.nodes[2]["collision_ids"] = ["a"]
    router = Router(g, [record()])
    assert router._route(1, 4, 0, 8)["distance_m"] == 200
    assert router._route(1, 4, 0.9, 8)["distance_m"] == 260
    assert router._route(1, 4, 0.2, 8)["distance_m"] == 200
    g[1][3][0]["lane_multiplier"] = 0.55
    g[3][4][0]["lane_multiplier"] = 0.55
    assert router._route(1, 4, 0, 8)["distance_m"] == 200


def test_hour_changes_route():
    g = graph()
    # Two corridors, different collision times; normalized maximum changes by hour.
    g[1][3][0]["length"] = 115
    g[3][4][0]["length"] = 115
    g.nodes[2]["collision_ids"] = ["a"]
    g.nodes[3]["collision_ids"] = ["b"]
    router = Router(g, [record("a", 8), record("b", 22)])
    assert router._route(1, 4, 0.9, 8)["distance_m"] == 230
    assert router._route(1, 4, 0.9, 22)["distance_m"] == 200


def test_parallel_edge_selection_preserves_geometry():
    g = graph()
    curve = [(-79.3900, 43.6600), (-79.3898, 43.6601), (-79.3890, 43.6600)]
    g.add_edge(1, 2, length=110, lane_multiplier=0.55, geometry=LineString(curve[::-1]))
    router = Router(g, [])
    direct = router._route(1, 4, 0, 8)
    safer = router._route(1, 4, 0.5, 8)
    assert direct["distance_m"] == 200 and safer["distance_m"] == 210
    assert safer["geometry"]["coordinates"][:3] == curve
    assert safer["duration_s"] == round(210 / (14000 / 3600))


@pytest.fixture
def client():
    return create_app(Router(graph(), [])).test_client()


def payload():
    return {"origin": [43.66, -79.39], "destination": [43.66, -79.388], "level": 2, "hour": 8, "city": "toronto"}


def test_api_valid_route_and_same_node(client):
    response = client.post("/api/route", json=payload())
    assert response.status_code == 200
    data = response.json
    assert data["direct"]["geometry"]["coordinates"][0] == [-79.39, 43.66]
    same = payload()
    same["destination"] = same["origin"]
    assert client.post("/api/route", json=same).json["direct"]["distance_m"] == 0
    assert client.get("/api/health").json["graph_nodes"] == 4


@pytest.mark.parametrize("field,value", [("level", True), ("level", 4), ("hour", 24), ("hour", 8.5),
                                         ("origin", [float("nan"), 0]), ("origin", [True, 0]),
                                         ("destination", "wrong"), ("city", "nyc")])
def test_api_rejects_invalid_inputs(client, field, value):
    body = payload()
    body[field] = value
    assert client.post("/api/route", json=body).status_code == 400


def test_outside_coverage_and_no_route(client):
    body = payload()
    body["origin"] = [44, -80]
    assert client.post("/api/route", json=body).status_code == 422
    body = payload()
    body["origin"], body["destination"] = body["destination"], body["origin"]
    assert client.post("/api/route", json=body).status_code == 422


def test_absent_cache_keeps_website_available(tmp_path):
    client = create_app(cache_path=tmp_path / "missing").test_client()
    assert client.get("/").status_code == 200
    assert client.get("/api/health").status_code == 503
    assert client.post("/api/route", json=payload()).status_code == 503
    assert client.post("/api/route", data="bad", content_type="application/json").status_code == 400


def test_carto_key_is_optional_browser_configuration(client, monkeypatch):
    monkeypatch.delenv("CARTO_BASEMAP_KEY", raising=False)
    assert client.get("/api/config").json == {"carto_key": ""}
    monkeypatch.setenv("CARTO_BASEMAP_KEY", "test-key")
    assert client.get("/api/config").json == {"carto_key": "test-key"}
