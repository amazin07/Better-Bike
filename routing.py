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

CENTER = (43.6629, -79.3957)
ALPHAS = {1: 0.9, 2: 0.5, 3: 0.2}
PROJECT = Transformer.from_crs(4326, 26917, always_xy=True)


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
    def __init__(self, graph, records, metadata=None):
        self.graph = graph
        self.records = {r.id: r for r in records}
        self.metadata = metadata or {}
        self.nodes = list(graph)
        self.tree = cKDTree([PROJECT.transform(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in self.nodes])
        self.risks = {hour: self._risk(hour) for hour in range(24)}
        self.points = [record.public() for record in records]

    def _risk(self, hour):
        values = {n: sum((3.0 if self.records[i].fatal else 1.0) * time_weight(self.records[i].hour, hour)
                         for i in set(data.get("collision_ids", [])))
                  for n, data in self.graph.nodes(data=True)}
        maximum = max(values.values(), default=0) or 1
        return {node: value / maximum for node, value in values.items()}

    def nearest(self, coordinate):
        distance, index = self.tree.query(PROJECT.transform(coordinate[1], coordinate[0]))
        if distance > 250:
            raise RouteError("Choose points inside the downtown coverage area, closer to a street.")
        return self.nodes[index]

    def _route(self, start, end, alpha, hour):
        def edge_cost(v, data):
            length = float(data["length"])
            if alpha == 0:
                return length
            return length * data.get("lane_multiplier", 1.0) * (1 + alpha * self.risks[hour][v])

        def weight(u, v, edges):
            return min(edge_cost(v, data) for data in edges.values())

        try:
            path = nx.shortest_path(self.graph, start, end, weight=weight, method="dijkstra")
        except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
            raise RouteError("No cycling route between those points. Try moving one closer to a street.") from exc
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
            node = self.graph.nodes[start]
            coordinates = [(node["x"], node["y"])] * 2
        ids = set().union(*(set(self.graph.nodes[n].get("collision_ids", [])) for n in path))
        return {"geometry": {"type": "LineString", "coordinates": coordinates},
                "distance_m": round(length, 1), "duration_s": round(length / (14000 / 3600)),
                "ksi_total": len(ids), "ksi_fatal": sum(self.records[i].fatal for i in ids)}

    def route(self, origin, destination, level, hour):
        start, end = self.nearest(origin), self.nearest(destination)
        return {"direct": self._route(start, end, 0, hour),
                "safer": self._route(start, end, ALPHAS[level], hour),
                "risk_points": self.points,
                "snapped": {"origin": [self.graph.nodes[start]["y"], self.graph.nodes[start]["x"]],
                            "destination": [self.graph.nodes[end]["y"], self.graph.nodes[end]["x"]]},
                "metadata": self.metadata}


def serialize_records(records):
    return [asdict(record) for record in records]
