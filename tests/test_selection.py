"""Riding-style route selection: Confident gets the fastest route whatever its score;
Beginner and Intermediate get the shortest route that beats their target score.

Two corridors join nodes 1 and 4: a short one through node 2 (200 m) and a longer one
through node 3 (260 m). Collisions are placed on a corridor to change its score.
All data is synthetic; nothing here touches the network.
"""
import networkx as nx
import pytest

from app import create_app
from routing import CANDIDATE_ALPHAS, TARGET_SCORES, Collision, Router, attach_collisions

ORIGIN = [43.66, -79.39]
DESTINATION = [43.66, -79.388]
SHORT_CORRIDOR = (43.66, -79.389)     # node 2
LONG_CORRIDOR = (43.6605, -79.3895)   # node 3


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


def router_with(*collisions, lanes_on_long=None):
    """collisions are (id, (lat, lng), fatal)."""
    g = graph()
    if lanes_on_long:
        g[1][3][0]["lane_multiplier"] = lanes_on_long
        g[3][4][0]["lane_multiplier"] = lanes_on_long
    records = [Collision(cid, lat, lng, 2024, 8, fatal) for cid, (lat, lng), fatal in collisions]
    attach_collisions(g, records)
    return Router(g, records)


def dangerous_short_corridor():
    return router_with(("a", SHORT_CORRIDOR, True))       # 1 fatal over ~1 km: score 70


# --- the rules -------------------------------------------------------------------------------

def test_targets_follow_the_riding_styles():
    assert TARGET_SCORES == {1: 90, 2: 70, 3: None}
    assert list(CANDIDATE_ALPHAS) == sorted(CANDIDATE_ALPHAS) and CANDIDATE_ALPHAS[0] > 0


def test_confident_always_gets_the_fastest_route_even_when_it_is_dangerous():
    result = dangerous_short_corridor().route(ORIGIN, DESTINATION, 3, 8)
    assert result["direct"]["safety"]["score"] == 70
    assert result["safer"]["distance_m"] == result["direct"]["distance_m"] == 200
    assert result["safer"]["geometry"] == result["direct"]["geometry"]
    assert result["selection"] == {"target": None, "met": True, "extra_distance_m": 0.0,
                                   "same_route": True, "candidates": 1}


def test_beginner_takes_the_shortest_route_that_beats_ninety():
    result = dangerous_short_corridor().route(ORIGIN, DESTINATION, 1, 8)
    assert result["safer"]["distance_m"] == 260 and result["safer"]["safety"]["score"] == 100
    assert result["selection"]["target"] == 90 and result["selection"]["met"] is True
    assert result["selection"]["extra_distance_m"] == 60


def test_intermediate_needs_strictly_more_than_seventy():
    result = dangerous_short_corridor().route(ORIGIN, DESTINATION, 2, 8)
    assert result["direct"]["safety"]["score"] == 70           # exactly 70 is not above 70
    assert result["safer"]["distance_m"] == 260 and result["selection"]["met"] is True


@pytest.mark.parametrize("level", [1, 2, 3])
def test_every_style_keeps_the_fastest_route_when_it_already_qualifies(level):
    result = router_with().route(ORIGIN, DESTINATION, level, 8)
    assert result["safer"]["geometry"] == result["direct"]["geometry"]
    assert result["selection"]["met"] is True and result["selection"]["extra_distance_m"] == 0


def test_beginner_gets_the_highest_scoring_route_when_nothing_beats_the_target():
    # Both corridors are dangerous, so no route scores above 90: fall back to the best found.
    both = router_with(("a", SHORT_CORRIDOR, True), ("b", LONG_CORRIDOR, True))
    result = both.route(ORIGIN, DESTINATION, 1, 8)
    assert result["selection"]["met"] is False
    assert result["safer"]["distance_m"] == 200                # tie on score: the shorter route
    # A merely serious collision on the long corridor scores 90, which is still not above 90.
    softer = router_with(("a", SHORT_CORRIDOR, True), ("b", LONG_CORRIDOR, False))
    result = softer.route(ORIGIN, DESTINATION, 1, 8)
    assert result["safer"]["safety"]["score"] == 90 and result["selection"]["met"] is False
    assert result["safer"]["distance_m"] == 260                # the higher score wins over distance
    assert result["selection"]["candidates"] > 1


def test_a_qualifying_fastest_route_beats_a_longer_bike_lane_route():
    # The old model preferred a longer route with lanes; distance now comes first.
    result = router_with(lanes_on_long=0.55).route(ORIGIN, DESTINATION, 2, 8)
    assert result["safer"]["distance_m"] == 200


def test_an_equally_short_safer_route_is_not_reported_as_the_fastest_route():
    # Toronto's grid has many routes of about the same length. Here the risky corridor is
    # 0.04 m shorter, so it is the fastest route, yet a beginner should get the equally
    # short safe one and the page must not claim the fastest route already qualified.
    g = graph()
    g[1][2][0]["length"] = g[2][4][0]["length"] = 99.98
    g[1][3][0]["length"] = g[3][4][0]["length"] = 100.0
    records = [Collision("a", *SHORT_CORRIDOR, 2024, 8, True)]
    attach_collisions(g, records)
    result = Router(g, records).route(ORIGIN, DESTINATION, 1, 8)
    assert result["direct"]["safety"]["score"] == 70 and result["safer"]["safety"]["score"] > 90
    selection = result["selection"]
    assert selection["extra_distance_m"] == 0.0        # 0.04 m rounds away...
    assert selection["same_route"] is False            # ...but it is a different route
    assert selection["met"] is True
    assert result["safer"]["geometry"] != result["direct"]["geometry"]


def test_the_score_function_decides_what_qualifies():
    def by_distance(route):
        return {"score": 95 if route["distance_m"] > 250 else 50}

    result = dangerous_short_corridor().route(ORIGIN, DESTINATION, 1, 8, score_fn=by_distance)
    assert result["safer"]["distance_m"] == 260 and result["safer"]["safety"] == {"score": 95}
    always = dangerous_short_corridor().route(ORIGIN, DESTINATION, 1, 8, score_fn=lambda r: {"score": 100})
    assert always["safer"]["distance_m"] == 200


def test_every_route_carries_a_safety_score_and_the_search_is_bounded():
    result = dangerous_short_corridor().route(ORIGIN, DESTINATION, 1, 8)
    for key in ("direct", "safer"):
        assert set(result[key]["safety"]) >= {"score", "inputs", "collision_penalty"}
    assert 1 <= result["selection"]["candidates"] <= len(CANDIDATE_ALPHAS) + 1 + 4   # sweep + direct + refinement


# --- API -------------------------------------------------------------------------------------

def body(level, **extra):
    return {"origin": ORIGIN, "destination": DESTINATION, "level": level, "hour": 8, "city": "toronto", **extra}


def test_api_reports_how_the_route_was_chosen():
    client = create_app(dangerous_short_corridor()).test_client()
    confident = client.post("/api/route", json=body(3)).json
    beginner = client.post("/api/route", json=body(1)).json
    assert confident["selection"]["target"] is None and confident["safer"]["distance_m"] == 200
    assert beginner["selection"] == {"target": 90, "met": True, "extra_distance_m": 60.0,
                                     "same_route": False,
                                     "candidates": beginner["selection"]["candidates"]}
    assert beginner["safer"]["distance_m"] == 260
    assert client.post("/api/route", json=body(4)).status_code == 400
