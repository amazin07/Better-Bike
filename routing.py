"""Collision parsing and deterministic, node-based routing. No network access."""
from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import networkx as nx
from pyproj import Transformer
from scipy.spatial import cKDTree

from scoring import safety_score

CENTER = (43.6629, -79.3957)
ALPHAS = {1: 0.9, 2: 0.5, 3: 0.2}
# Heuristic metres of lane-weighted distance at maximum normalized node risk.
# Keep infrastructure discounts strong; do not discount the collision penalty.
RISK_PENALTY_M = 600.0
MODEL_VERSION = "lane-priority-node-penalty-v2"   # the old lane-weighted cost; scripts/evaluate_routing.py
SELECTION_VERSION = "score-threshold-v3"          # what Router.route does now
# Riding style (the request's `level`) -> the safety score a route must beat. Confident
# riders (None) always get the fastest route, whatever its score. Beginner and
# Intermediate riders get the shortest route whose score is strictly above the target.
TARGET_SCORES = {1: 90, 2: 70, 3: None}
# Collision-penalty weights tried, in order, while hunting for a route that qualifies.
# Weight 0 is the fastest route; each larger weight detours further to avoid recorded
# collisions. The first qualifying weight is then refined by bisection.
CANDIDATE_ALPHAS = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 13, 20, 40)
REFINE_STEPS = 4
# Bike parking near the destination (the yellow leg). A destination with a spot within
# PARKING_AT_DESTINATION_M of the pin needs no separate path. Otherwise the nearest spot by
# riding distance within PARKING_SEARCH_M wins. A spot joins the street network only if a
# street node lies within PARKING_SNAP_M; the last stretch is a straight connector.
PARKING_AT_DESTINATION_M = 30.0
PARKING_SEARCH_M = 1500.0
PARKING_SNAP_M = 90.0
SPEED_MPS = 14000 / 3600
PROJECT =Transformer.from_crs(4326, 26917, always_xy=True)


@dataclass(frozen=True)
class Collision:
    id: str
    lat: float
    lng: float
    year: int
    hour: int | None
    fatal: bool
    injury: str = ""
    impact: str = ""
    light: str = ""
    visibility: str = ""
    road_class: str = ""

    def public(self):
        return {"lat": self.lat, "lng": self.lng, "year": self.year,
                "severity": "fatal" if self.fatal else "serious"}


def parse_hour(value):
    value = str(value or "").strip()
    try:
        if ":" in value:
            hour, minute = (int(x) for x in value.split(":")[:2])
        else:
            number = float(value)
            if not number.is_integer():
                return None
            hour, minute = divmod(int(number), 100)
        return hour if 0 <= hour <= 23 and 0 <= minute <= 59 else None
    except (ValueError, OverflowError):
        return None


def load_collisions(path: Path):
    """Group person rows before filtering so flags/severity cannot be lost."""
    groups = {}
    row_count = 0
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = {x.lower() for x in reader.fieldnames or []}
        if not fields.intersection({"accnum", "collision_id"}) or "cyclist" not in fields:
            raise ValueError("KSI CSV needs ACCNUM/collision_id and CYCLIST/cyclist columns")
        for raw in reader:
            row_count += 1
            row = {k.lower(): (v or "").strip() for k, v in raw.items() if k}
            identifier = row.get("collision_id") or row.get("accnum")
            if not identifier:
                continue
            group = groups.setdefault(identifier, {"cyclist": False, "fatal": False, "row": {}})
            group["cyclist"] |= row.get("cyclist", "").lower() in {"yes", "true", "1"}
            group["fatal"] |= row.get("acclass", "").lower() in {"fatal", "fatal injury"}
            for key, value in row.items():
                if value and not group["row"].get(key):
                    group["row"][key] = value
    records = []
    invalid = 0
    for identifier, group in groups.items():
        if not group["cyclist"]:
            continue
        row = group["row"]
        try:
            lat, lng = float(row["latitude"]), float(row["longitude"])
            if not (math.isfinite(lat) and math.isfinite(lng) and 43 < lat < 45 and -81 < lng < -78):
                raise ValueError("Invalid Toronto coordinates")
            date_value = row.get("accdate") or row.get("date", "")
            date = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
            hour = parse_hour(row["time"]) if "time" in row else (
                date.hour if "accdate" in row and ("T" in date_value or " " in date_value) else None)
            records.append(Collision(identifier, lat, lng, date.year, hour, group["fatal"],
                                     row.get("injury", ""), row.get("impactype", ""),
                                     row.get("light", ""), row.get("visibility", row.get("visible", "")),
                                     row.get("road_class", "")))
        except (KeyError, ValueError, OverflowError):
            invalid += 1
    records.sort(key=lambda item: item.id)
    return records, {"person_rows": row_count, "unique_collisions": len(groups),
                     "cyclist_collisions": len(records), "invalid_cyclist_records": invalid}


def time_weight(record_hour: int | None, hour: int):
    if record_hour is None:
        return 1.0
    delta = abs(record_hour - hour)
    return 2.0 if min(delta, 24 - delta) <= 2 else 1.0


def attach_collisions(graph, records, radius=30):
    nodes = list(graph)
    tree = cKDTree([PROJECT.transform(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in nodes])
    for node in nodes:
        graph.nodes[node]["collision_ids"] = []
    matched = set()
    for record in records:
        nearby = tree.query_ball_point(PROJECT.transform(record.lng, record.lat), radius)
        for index in nearby:
            graph.nodes[nodes[index]]["collision_ids"].append(record.id)
            matched.add(record.id)
    return len(matched)


class RouteError(ValueError):
    pass


class Router:
    def __init__(self, graph, records, metadata=None, parking=None):
        self.graph = graph
        self.records = {r.id: r for r in records}
        self.metadata = dict(metadata or {})
        self.nodes = list(graph)
        self.tree = cKDTree([PROJECT.transform(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in self.nodes])
        raw_risks = {hour: self._raw_risk(hour) for hour in range(24)}
        # One scale across all hours: doubling a record's time weight must not
        # disappear because that hour's maximum also doubled.
        self.risk_scale = max((max(values.values(), default=0) for values in raw_risks.values()), default=0) or 1
        self.risks = {hour: {node: value / self.risk_scale for node, value in values.items()}
                      for hour, values in raw_risks.items()}
        self.metadata["routing_model"] = {"version": SELECTION_VERSION, "risk_penalty_m": RISK_PENALTY_M,
                                          "normalization_peak": self.risk_scale}
        self.points = [record.public() for record in records]
        self._index_parking(parking)

    def _index_parking(self, parking):
        """Keep every spot for the at-destination check, and attach each to its nearest street node."""
        self.parking = list(parking or [])
        self.parking_enabled = bool(self.parking)
        self.parking_tree = None
        self._spots_by_node = {}
        if not self.parking:
            return
        xs, ys = PROJECT.transform([s["lng"] for s in self.parking], [s["lat"] for s in self.parking])
        points = list(zip(xs, ys))
        self.parking_tree = cKDTree(points)
        distances, indexes = self.tree.query(points, distance_upper_bound=PARKING_SNAP_M)
        for spot_index, (metres, node_index) in enumerate(zip(distances, indexes)):
            if node_index < len(self.nodes):
                self._spots_by_node.setdefault(self.nodes[node_index], []).append((spot_index, float(metres)))

    def _raw_risk(self, hour):
        return {n: sum((3.0 if self.records[i].fatal else 1.0) * time_weight(self.records[i].hour, hour)
                       for i in set(data.get("collision_ids", [])))
                for n, data in self.graph.nodes(data=True)}

    def edge_cost(self, destination, data, alpha, hour, lanes=True):
        length = float(data["length"])
        if alpha == 0:
            return length
        # lanes=False drops the bike-lane discount: route selection compares real distance.
        multiplier = data.get("lane_multiplier", 1.0) if lanes else 1.0
        return length * multiplier + alpha * RISK_PENALTY_M * self.risks[hour][destination]

    def nearest(self, coordinate):
        distance, index = self.tree.query(PROJECT.transform(coordinate[1], coordinate[0]))
        if distance > 250:
            raise RouteError("Choose points inside the downtown coverage area, closer to a street.")
        return self.nodes[index]

    def _trace(self, path, edge_cost):
        """Coordinates and length of a node path, using the cheapest of any parallel edges."""
        coordinates = []
        length = 0.0
        for u, v in zip(path, path[1:]):
            data = min(self.graph[u][v].values(), key=lambda d: edge_cost(v, d))
            length += data["length"]
            source = (self.graph.nodes[u]["x"], self.graph.nodes[u]["y"])
            target = (self.graph.nodes[v]["x"], self.graph.nodes[v]["y"])
            segment = list(data["geometry"].coords) if data.get("geometry") is not None else [source, target]
            if math.dist(segment[-1], source) < math.dist(segment[0], source):
                segment.reverse()
            coordinates.extend(segment if not coordinates else segment[1:])
        if not coordinates:
            node = self.graph.nodes[path[0]]
            coordinates = [(node["x"], node["y"])] * 2
        return coordinates, length

    def _parking(self, end, destination):
        """The bike parking to use at the destination, or None when there is no parking data.

        Independent of riding style and hour: it never changes the blue routes. A spot within
        PARKING_AT_DESTINATION_M of the pin means no separate path. Otherwise the nearest spot
        by riding distance from the destination's street node, with the path to it.
        """
        if not self.parking_enabled:
            return None
        pin = PROJECT.transform(destination[1], destination[0])
        metres, index = self.parking_tree.query(pin, distance_upper_bound=PARKING_AT_DESTINATION_M)
        if index < len(self.parking):
            return {"at_destination": True, "spot": dict(self.parking[index]),
                    "distance_m": round(float(metres), 1), "duration_s": round(metres / SPEED_MPS),
                    "geometry": None}

        def length(u, v, edges):
            return min(float(data["length"]) for data in edges.values())

        reach, paths = nx.single_source_dijkstra(self.graph, end, cutoff=PARKING_SEARCH_M, weight=length)
        best = None
        for node, ridden in reach.items():
            for spot_index, connector in self._spots_by_node.get(node, ()):
                total = ridden + connector
                if total <= PARKING_SEARCH_M and (best is None or (total, spot_index) < best[:2]):
                    best = (total, spot_index, node)
        if best is None:
            return {"at_destination": False, "spot": None, "distance_m": None,
                    "duration_s": None, "geometry": None}
        total, spot_index, node = best
        spot = self.parking[spot_index]
        coordinates, _ = self._trace(paths[node], lambda v, data: float(data["length"]))
        line = []
        for point in [*coordinates, (spot["lng"], spot["lat"])]:
            if not line or list(point) != line[-1]:
                line.append(list(point))
        return {"at_destination": False, "spot": dict(spot), "distance_m": round(total, 1),
                "duration_s": round(total / SPEED_MPS),
                "geometry": {"type": "LineString", "coordinates": line}}

    def _route(self, start, end, alpha, hour):
        return self._route_with_path(start, end, alpha, hour)[0]

    def _route_with_path(self, start, end, alpha, hour, lanes=True):
        """One route and its node path, so callers can tell whether two searches found the same route."""
        def edge_cost(v, data):
            return self.edge_cost(v, data, alpha, hour, lanes)

        def weight(u, v, edges):
            return min(edge_cost(v, data) for data in edges.values())

        try:
            path = nx.shortest_path(self.graph, start, end, weight=weight, method="dijkstra")
        except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
            raise RouteError("No cycling route between those points. Try moving one closer to a street.") from exc
        coordinates, length = self._trace(path, edge_cost)
        ids = set().union(*(set(self.graph.nodes[n].get("collision_ids", [])) for n in path))
        route = {"geometry": {"type": "LineString", "coordinates": coordinates},
                 "distance_m": round(length, 1), "duration_s": round(length / (14000 / 3600)),
                 "ksi_total": len(ids), "ksi_fatal": sum(self.records[i].fatal for i in ids)}
        return route, tuple(path)

    def _choose(self, start, end, hour, direct, target, score_fn):
        """Pick the shortest route whose score beats `target`; else the highest-scoring one found.

        Candidates run from the fastest route toward the safest by raising the weight on the
        collision penalty. Every candidate is scored with `score_fn`, the same score the
        cards show. The first weight that qualifies is refined by bisection so the route is
        no longer than it has to be. Speed is constant, so the fastest route is the shortest.
        """
        if target is None:
            return direct, {"target": None, "met": True, "extra_distance_m": 0.0,
                            "same_route": True, "candidates": 1}

        def qualifies(route):
            return route["safety"]["score"] > target

        found = {direct["_path"]: direct}

        def run(alpha):
            route, path = self._route_with_path(start, end, alpha, hour, lanes=False)
            if path not in found:
                route["safety"] = score_fn(route)
                found[path] = route
            return found[path]

        if not qualifies(direct):
            low = 0.0
            for alpha in CANDIDATE_ALPHAS:
                if qualifies(run(alpha)):
                    for _ in range(REFINE_STEPS):
                        middle = (low + alpha) / 2
                        if qualifies(run(middle)):
                            alpha = middle
                        else:
                            low = middle
                    break
                low = alpha
        routes = list(found.values())
        meeting = [route for route in routes if qualifies(route)]
        if meeting:
            chosen = min(meeting, key=lambda route: (route["distance_m"], -route["safety"]["score"]))
        else:
            chosen = max(routes, key=lambda route: (route["safety"]["score"], -route["distance_m"]))
        # same_route: the fastest route itself. A different route of equal length (common on
        # Toronto's grid) is not the same route, and the page must not say it is.
        return chosen, {"target": target, "met": bool(meeting),
                        "extra_distance_m": round(chosen["distance_m"] - direct["distance_m"], 1),
                        "same_route": chosen is direct, "candidates": len(routes)}

    def route(self, origin, destination, level, hour, score_fn=None):
        """The fastest route and the route chosen for the rider's style.

        `score_fn(route)` returns the safety score dict for a route; by default the
        collisions-only score. Confident riders (level 3) get the fastest route whatever its
        score; levels 1 and 2 get the shortest route scoring above TARGET_SCORES[level].
        """
        start, end = self.nearest(origin), self.nearest(destination)
        score_fn = score_fn or (lambda r: safety_score(r["ksi_total"], r["ksi_fatal"], r["distance_m"]))
        direct, direct_path = self._route_with_path(start, end, 0, hour)
        direct["safety"] = score_fn(direct)
        direct["_path"] = direct_path
        chosen, selection = self._choose(start, end, hour, direct, TARGET_SCORES[level], score_fn)
        for route in (direct, chosen):
            route.pop("_path", None)
        return {"direct": direct, "safer": chosen, "selection": selection,
                "parking": self._parking(end, destination),
                "risk_points": self.points,
                "snapped": {"origin": [self.graph.nodes[start]["y"], self.graph.nodes[start]["x"]],
                            "destination": [self.graph.nodes[end]["y"], self.graph.nodes[end]["x"]]},
                "metadata": self.metadata}


def serialize_records(records):
    return [asdict(record) for record in records]
