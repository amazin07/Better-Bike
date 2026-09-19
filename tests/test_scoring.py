"""Route safety score: collision penalty, congestion matching, and the API fields.

All data is synthetic. Nothing here touches the network or the real .env.
"""
from datetime import datetime, timezone

import networkx as nx
import pytest

import scoring
from app import create_app
from routing import Collision, Router, attach_collisions
from traffic import CongestionIndex, TrafficCache, normalize_flow

# One east-west road along lat 43.66 (about 966 m) and its western half.
ROAD = [(-79.400, 43.66), (-79.388, 43.66)]
HALF = [(-79.400, 43.66), (-79.394, 43.66)]


def flow_payload(*roads):
    """Roads are (name, jamFactor, [(lng, lat), ...], closed)."""
    results = []
    for name, jam, points, closed in roads:
        results.append({
            "location": {"description": name, "length": 100.0,
                         "shape": {"links": [{"points": [{"lat": lat, "lng": lng} for lng, lat in points],
                                              "length": 100.0, "functionalClass": 4}]}},
            "currentFlow": {"speed": 5.0, "freeFlow": 10.0, "jamFactor": jam,
                            "traversability": "closed" if closed else "open"},
        })
    return {"sourceUpdated": "2026-09-19T16:00:00Z", "results": results}


def index_for(*roads):
    return CongestionIndex.from_flow(normalize_flow(flow_payload(*roads)))


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class StaticClient:
    counts_toward_budget = True

    def __init__(self, flow):
        self.flow = flow
        self.calls = []

    def fetch(self, endpoint):
        self.calls.append(endpoint)
        return self.flow if endpoint == "flow" else {"sourceUpdated": "x", "results": []}


# --- score formula ---------------------------------------------------------------------------

def test_collision_penalty_weights_fatal_and_uses_at_least_one_km():
    assert scoring.collision_penalty(0, 0, 5000) == 0
    assert scoring.collision_penalty(2, 1, 2000) == pytest.approx(20.0)   # (2 + 2*1) / 2 km * 10
    assert scoring.collision_penalty(1, 0, 300) == pytest.approx(10.0)    # under 1 km divides by 1 km
    assert scoring.collision_penalty(50, 10, 1000) == 70                  # capped


def test_traffic_penalty_scales_with_congested_share_and_clamps():
    assert scoring.traffic_penalty(0) == 0
    assert scoring.traffic_penalty(0.5) == pytest.approx(15.0)
    assert scoring.traffic_penalty(1) == 30
    assert scoring.traffic_penalty(2) == 30
    assert scoring.traffic_penalty(-1) == 0


def test_score_uses_collisions_only_without_traffic():
    result = scoring.safety_score(2, 1, 2000)
    assert result == {"score": 80, "inputs": ["collisions"], "collision_penalty": 20.0,
                      "traffic_penalty": None, "congested_share": None}


def test_score_adds_the_traffic_penalty_when_traffic_is_known():
    result = scoring.safety_score(0, 0, 1000, congested_share=0.5)
    assert result["score"] == 85 and result["inputs"] == ["collisions", "traffic"]
    assert result["traffic_penalty"] == pytest.approx(15.0) and result["congested_share"] == 0.5
    assert scoring.safety_score(0, 0, 1000, congested_share=0.0)["inputs"] == ["collisions", "traffic"]


def test_score_is_a_clamped_whole_number():
    worst = scoring.safety_score(100, 100, 1000, congested_share=1.0)
    assert worst["score"] == 0 and isinstance(worst["score"], int)
    best = scoring.safety_score(0, 0, 1000)
    assert best["score"] == 100 and isinstance(best["score"], int)
    assert scoring.safety_score(1, 0, 1700, congested_share=0.333)["score"] == 84


# --- matching a route to congested roads -------------------------------------------------------

def test_share_is_full_when_the_route_follows_a_congested_road():
    assert index_for(("Busy Rd", 6.0, HALF, False)).share(HALF) == pytest.approx(1.0, abs=0.02)


def test_share_is_partial_when_only_part_of_the_route_is_congested():
    assert index_for(("Busy Rd", 6.0, HALF, False)).share(ROAD) == pytest.approx(0.5, abs=0.05)


def test_share_is_zero_for_a_route_elsewhere():
    far = [(lng, lat + 0.01) for lng, lat in ROAD]
    assert index_for(("Busy Rd", 6.0, ROAD, False)).share(far) == 0.0


def test_a_short_crossing_is_not_counted_as_riding_along_the_road():
    crossing = [(-79.397, 43.655), (-79.397, 43.665)]           # crosses the road at right angles
    assert index_for(("Busy Rd", 6.0, ROAD, False)).share(crossing) == 0.0


def test_only_congestion_at_or_above_the_threshold_counts():
    below = scoring.CONGESTED_JAM_FACTOR - 0.1
    assert index_for(("Calm Rd", below, ROAD, False)).share(ROAD) == 0.0
    assert index_for(("Busy Rd", scoring.CONGESTED_JAM_FACTOR, ROAD, False)).share(ROAD) > 0.9


def test_a_closed_road_is_not_treated_as_congestion():
    assert index_for(("Shut Rd", 10.0, ROAD, True)).share(ROAD) == 0.0


def test_share_handles_empty_and_degenerate_input():
    assert CongestionIndex.from_flow(None).share(ROAD) == 0.0
    assert CongestionIndex.from_flow({"type": "FeatureCollection", "features": []}).share(ROAD) == 0.0
    assert index_for(("Busy Rd", 6.0, ROAD, False)).share([(-79.4, 43.66)]) == 0.0
    assert index_for(("Busy Rd", 6.0, ROAD, False)).share([]) == 0.0


# --- the cache hands out an index without ever calling HERE -------------------------------------

def test_cache_index_is_none_until_data_exists_and_never_calls_here():
    client = StaticClient(flow_payload(("Busy Rd", 6.0, ROAD, False)))
    cache = TrafficCache(client, clock=FakeClock())
    assert cache.congestion_index() is None
    assert client.calls == []
    cache.snapshot()
    used = len(client.calls)
    index = cache.congestion_index()
    assert isinstance(index, CongestionIndex)
    assert cache.congestion_index() is index                     # rebuilt only when the data changes
    assert len(client.calls) == used


def test_cache_index_is_none_when_the_snapshot_is_stale_or_disabled():
    client = StaticClient(flow_payload(("Busy Rd", 6.0, ROAD, False)))
    clock = FakeClock()
    cache = TrafficCache(client, clock=clock)
    cache.snapshot()
    assert cache.congestion_index() is not None
    clock.advance(601)                                           # older than twice the 300 s ttl
    assert cache.congestion_index() is None
    assert TrafficCache(None).congestion_index() is None


# --- API -----------------------------------------------------------------------------------------

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


def body(**extra):
    return {"origin": [43.66, -79.39], "destination": [43.66, -79.388], "level": 2, "hour": 8,
            "city": "toronto", **extra}


def web(cache, with_collision=False):
    g = graph()
    records = [Collision("a", 43.66, -79.389, 2024, 8, True)] if with_collision else []
    attach_collisions(g, records)
    return create_app(Router(g, records), traffic=cache).test_client()


def test_route_returns_a_score_for_both_routes_from_collisions_alone():
    data = web(TrafficCache(None), with_collision=True).post("/api/route", json=body()).json
    assert data["traffic_used"] is False
    direct, safer = data["direct"]["safety"], data["safer"]["safety"]
    assert direct["inputs"] == ["collisions"] and direct["traffic_penalty"] is None
    assert direct["collision_penalty"] == 30.0 and direct["score"] == 70     # 1 fatal = weight 3 over 1 km
    assert safer["score"] == 100                                             # the detour avoids that collision


def test_route_without_any_collision_scores_one_hundred():
    data = web(TrafficCache(None)).post("/api/route", json=body()).json
    assert data["direct"]["safety"]["score"] == 100 and data["safer"]["safety"]["score"] == 100


def test_route_includes_live_traffic_only_when_asked_and_available():
    client = StaticClient(flow_payload(("Busy Rd", 6.0, [(-79.391, 43.66), (-79.387, 43.66)], False)))
    cache = TrafficCache(client, clock=FakeClock())
    cache.snapshot()                                             # the overlay being on primes the cache
    calls_before = len(client.calls)
    server = web(cache, with_collision=True)
    with_traffic = server.post("/api/route", json=body(traffic=True)).json
    assert with_traffic["traffic_used"] is True
    direct = with_traffic["direct"]["safety"]
    assert direct["inputs"] == ["collisions", "traffic"]
    assert direct["congested_share"] == pytest.approx(1.0, abs=0.02)
    assert direct["traffic_penalty"] == pytest.approx(30.0, abs=0.6) and direct["score"] == 40
    assert with_traffic["safer"]["safety"]["congested_share"] < 0.5
    without = server.post("/api/route", json=body()).json                    # switch off: collisions only
    assert without["traffic_used"] is False and without["direct"]["safety"]["inputs"] == ["collisions"]
    assert len(client.calls) == calls_before, "a route request must never call HERE"


@pytest.mark.parametrize("cache_factory", [lambda: TrafficCache(None),
                                           lambda: TrafficCache(StaticClient(flow_payload()))])
def test_route_falls_back_to_collisions_when_no_traffic_data_exists(cache_factory):
    cache = cache_factory()
    data = web(cache).post("/api/route", json=body(traffic=True)).json
    assert data["traffic_used"] is False
    assert data["direct"]["safety"]["inputs"] == ["collisions"]
    if cache._client is not None:
        assert cache._client.calls == [], "asking for traffic must not trigger a HERE refresh"


@pytest.mark.parametrize("value", ["yes", 1, None, []])
def test_route_rejects_a_non_boolean_traffic_flag(value):
    response = web(TrafficCache(None)).post("/api/route", json=body(traffic=value))
    assert response.status_code == 400
