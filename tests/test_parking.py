"""Bicycle theft and bike parking data, and the yellow route from a destination to the
nearest bike parking.

Destinations that already have parking need no separate path. The routes for each riding
style must be unaffected by any of this. All routing data is synthetic; only the last
group of tests reads the published data files.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pytest

from app import create_app
from bikedata import load_parking, normalize_parking, normalize_thefts
from routing import PARKING_AT_DESTINATION_M, PARKING_SEARCH_M, Router

ROOT = Path(__file__).resolve().parent.parent
BOUNDS = [[43.6275, -79.4454], [43.6989, -79.3460]]
ORIGIN = [43.66, -79.39]
DESTINATION = [43.66, -79.388]           # node 4


def epoch_ms(year):
    return int(datetime(year, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)


# --- theft and parking normalisation ---------------------------------------------------------

def test_thefts_keep_valid_points_round_them_and_drop_identifiers():
    records = [
        {"LAT_WGS84": 43.6601234567, "LONG_WGS84": -79.3901234567, "OCC_DATE_AGOL": epoch_ms(2025),
         "PREMISES_TYPE": " Outside ", "EVENT_UNIQUE_ID": "GO-00000000000"},
        {"LAT_WGS84": 0, "LONG_WGS84": 0, "OCC_DATE_AGOL": epoch_ms(2025), "PREMISES_TYPE": "House"},
        {"LAT_WGS84": None, "LONG_WGS84": -79.39, "PREMISES_TYPE": "House"},
        {"LAT_WGS84": float("nan"), "LONG_WGS84": -79.39},
        {"LAT_WGS84": 40.0, "LONG_WGS84": -79.39},                     # not Toronto
        {"LAT_WGS84": 43.7, "LONG_WGS84": -79.4, "OCC_DATE_AGOL": None, "PREMISES_TYPE": ""},
    ]
    assert normalize_thefts(records) == [[43.66012, -79.39012, 2025, "Outside"],
                                         [43.7, -79.4, None, "Unknown"]]
    assert "GO-0000" not in json.dumps(normalize_thefts(records))


def feature(lng, lat, kind="MultiPoint", **props):
    coordinates = [[lng, lat]] if kind == "MultiPoint" else [lng, lat]
    return {"type": "Feature", "properties": props, "geometry": {"type": kind, "coordinates": coordinates}}


def test_parking_keeps_only_existing_spots_inside_the_map_with_their_capacity():
    datasets = {
        "ring": {"features": [feature(-79.39, 43.66, STATUS="Existing"),
                              feature(-79.39, 43.661, STATUS="Temporarily Removed"),
                              feature(-79.90, 43.66, STATUS="Existing")]},          # far outside the map
        "rack": {"features": [feature(-79.391, 43.66, STATUS="Installed", CAPACITY="8"),
                              feature(-79.392, 43.66, STATUS="Proposed", CAPACITY=8)]},
        "large": {"features": [feature(-79.393, 43.66, BICYCLE_CAPACITY=24)]},      # no status field: exists
        "station": {"features": [feature(-79.38, 43.645, kind="Point", BIKE_CAPACITY=80)]},
    }
    assert sorted(normalize_parking(datasets, BOUNDS)) == sorted([
        [43.66, -79.39, "ring", None], [43.66, -79.391, "rack", 8],
        [43.66, -79.393, "large", 24], [43.645, -79.38, "station", 80]])


def test_loading_parking_survives_a_missing_or_broken_file(tmp_path):
    assert load_parking(tmp_path / "missing.json") == []
    (tmp_path / "broken.json").write_text("not json")
    assert load_parking(tmp_path / "broken.json") == []
    (tmp_path / "ok.json").write_text(json.dumps({"spots": [[43.66, -79.39, "ring", None]]}))
    assert load_parking(tmp_path / "ok.json") == [{"lat": 43.66, "lng": -79.39, "kind": "ring", "capacity": None}]


# --- routing to parking ----------------------------------------------------------------------

def two_way_graph(length=None):
    g = nx.MultiDiGraph(crs="EPSG:4326")
    for n, x, y in [(1, -79.3900, 43.6600), (2, -79.3890, 43.6600),
                    (3, -79.3895, 43.6605), (4, -79.3880, 43.6600)]:
        g.add_node(n, x=x, y=y, collision_ids=[])
    for u, v, metres in [(1, 2, 100), (2, 4, 100), (1, 3, 130), (3, 4, 130)]:
        g.add_edge(u, v, length=length or metres)
        g.add_edge(v, u, length=length or metres)
    return g


def spot(lat, lng, kind="ring", capacity=None):
    return {"lat": lat, "lng": lng, "kind": kind, "capacity": capacity}


def router_with(*spots, graph=None):
    return Router(graph or two_way_graph(), [], parking=list(spots) if spots else None)


def route(router, level=2):
    return router.route(ORIGIN, DESTINATION, level, 8)


def test_a_destination_with_parking_needs_no_separate_path():
    result = route(router_with(spot(43.66, -79.38796)))          # about 3 m from the destination
    parking = result["parking"]
    assert parking["at_destination"] is True and parking["geometry"] is None
    assert parking["spot"]["lat"] == 43.66 and parking["distance_m"] <= PARKING_AT_DESTINATION_M


def test_otherwise_the_nearest_parking_by_route_gets_a_path_from_the_destination():
    near = spot(43.66, -79.38895)                                 # by node 2: 100 m away on the street
    far = spot(43.6605, -79.38945)                                # by node 3: 130 m away
    parking = route(router_with(far, near))["parking"]
    assert parking["at_destination"] is False
    assert (parking["spot"]["lat"], parking["spot"]["lng"]) == (43.66, -79.38895)
    assert 100 <= parking["distance_m"] <= 110 and parking["duration_s"] > 0
    coordinates = parking["geometry"]["coordinates"]
    assert coordinates[0] == [-79.388, 43.66] and coordinates[-1] == [-79.38895, 43.66]
    assert parking["geometry"]["type"] == "LineString"


def test_spots_too_far_from_any_street_are_ignored_and_none_found_is_reported():
    parking = route(router_with(spot(43.68, -79.30)))["parking"]
    assert parking == {"at_destination": False, "spot": None, "distance_m": None,
                       "duration_s": None, "geometry": None}


def test_parking_beyond_the_search_range_is_not_offered():
    slow = two_way_graph(length=PARKING_SEARCH_M * 0.7)          # node 1 is then out of range
    parking = route(router_with(spot(43.66, -79.38995), graph=slow))["parking"]
    assert parking["spot"] is None


def test_without_parking_data_the_field_is_null():
    assert route(router_with())["parking"] is None


@pytest.mark.parametrize("level", [1, 2, 3])
def test_parking_never_changes_the_routes_for_any_riding_style(level):
    without = route(router_with(), level)
    with_parking = route(router_with(spot(43.66, -79.38895)), level)
    for key in ("direct", "safer", "selection"):
        assert with_parking[key] == without[key]


def test_the_api_returns_the_parking_field_and_serves_the_data_files(tmp_path):
    client = create_app(router_with(spot(43.66, -79.38895))).test_client()
    body = {"origin": ORIGIN, "destination": DESTINATION, "level": 2, "hour": 8, "city": "toronto"}
    parking = client.post("/api/route", json=body).json["parking"]
    assert parking["spot"]["kind"] == "ring" and parking["geometry"]["coordinates"]
    assert create_app(router_with()).test_client().post("/api/route", json=body).json["parking"] is None
    for path in ("/static/bike-thefts.json", "/static/bike-parking.json"):
        assert client.get(path).status_code == 200


# --- the published data files ----------------------------------------------------------------

def test_published_theft_file_is_valid_attributed_and_free_of_ids():
    text = (ROOT / "static" / "bike-thefts.json").read_text(encoding="utf-8")
    data = json.loads(text)
    meta = data["metadata"]
    assert meta["count"] == len(data["points"]) > 500
    assert "Open Government Licence" in meta["licence"] and "Toronto Police Service" in meta["attribution"]
    assert not re.search(r"GO-\d", text), "police event ids must not be published"
    for lat, lng, year, premises in data["points"]:
        assert 43.4 < lat < 44.0 and -80.0 < lng < -78.8
        assert year is None or 2000 < year <= 2027
        assert isinstance(premises, str) and premises


def test_published_parking_file_covers_the_map_with_known_kinds():
    data = json.loads((ROOT / "static" / "bike-parking.json").read_text(encoding="utf-8"))
    assert data["metadata"]["count"] == len(data["spots"]) > 5000
    (south, west), (north, east) = BOUNDS
    for lat, lng, kind, capacity in data["spots"]:
        assert south - 0.01 < lat < north + 0.01 and west - 0.01 < lng < east + 0.01
        assert kind in ("ring", "rack", "large", "station")
        assert capacity is None or (isinstance(capacity, int) and capacity > 0)
    assert {spot_kind for _, _, spot_kind, _ in data["spots"]} >= {"ring", "rack", "large"}
