"""Route safety score: a transparent 0-100 index, not a prediction.

    score = 100 - collision penalty - traffic penalty, clamped to 0..100

* Collision penalty: recorded cyclist KSI collisions along the path, per km.
  A fatal collision counts 3x (the routing model's weights). Paths under 1 km
  are divided by 1 km so a very short trip is not blown up. The scale was chosen
  on all 132 landmark-to-landmark routes so scores spread out: medians near 73
  for the direct route and 82 for the suggested one, ranging from about 30 to 92.
* Traffic penalty: only when live traffic data is available. The share of the
  route that runs along HERE-reported congested roads, times MAX_TRAFFIC_PENALTY.

Both inputs are heuristics built on incomplete data. KSI counts have no cyclist
exposure denominator, zero recorded collisions is not proof of safety, and
congested car traffic is not the same thing as danger to a cyclist. The score is
not calibrated to injury outcomes; it is a way to compare routes.
"""
from __future__ import annotations

FATAL_WEIGHT = 3                 # a fatal collision counts three times, as in the routing model
COLLISION_POINTS = 10.0          # points lost per weighted collision per km
MAX_COLLISION_PENALTY = 70.0
MIN_DISTANCE_KM = 1.0            # shorter paths are divided by 1 km, not their own length
MAX_TRAFFIC_PENALTY = 30.0

CONGESTED_JAM_FACTOR = 4.0       # HERE jamFactor at or above this counts as congested
TRAFFIC_MATCH_M = 15.0           # a route point this close to a congested road is on it
MIN_OVERLAP_M = 40.0             # shorter overlaps are crossings, not riding along the road


def _clamp(value, low, high):
    return max(low, min(high, value))


def collision_penalty(total, fatal, distance_m):
    """Points lost to recorded collisions: weighted collisions per km, scaled and capped."""
    weighted = total + (FATAL_WEIGHT - 1) * fatal          # `total` already counts each fatal once
    kilometres = max(distance_m / 1000.0, MIN_DISTANCE_KM)
    return min(MAX_COLLISION_PENALTY, COLLISION_POINTS * weighted / kilometres)


def traffic_penalty(congested_share):
    """Points lost to congestion: the congested share of the route (0 to 1) times the maximum."""
    return MAX_TRAFFIC_PENALTY * _clamp(float(congested_share), 0.0, 1.0)


def safety_score(total, fatal, distance_m, congested_share=None):
    """Score one route. Pass congested_share=None when live traffic is unavailable."""
    collisions = collision_penalty(total, fatal, distance_m)
    inputs = ["collisions"]
    traffic = share = None
    if congested_share is not None:
        share = _clamp(float(congested_share), 0.0, 1.0)
        traffic = traffic_penalty(share)
        inputs.append("traffic")
    score = _clamp(100.0 - collisions - (traffic or 0.0), 0.0, 100.0)
    return {
        "score": int(round(score)),
        "inputs": inputs,
        "collision_penalty": round(collisions, 1),
        "traffic_penalty": None if traffic is None else round(traffic, 1),
        "congested_share": None if share is None else round(share, 3),
    }
