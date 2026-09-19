"""Compare original and current heuristics on every directed landmark pair.

Uses the existing local cache, with no downloads. This is a behavior comparison,
not a measurement or prediction of real-world injury rates.
"""
import argparse
import json
import pickle
import statistics
import sys
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import LANDMARKS
from routing import ALPHAS, MODEL_VERSION, RISK_PENALTY_M, Collision, Router


class OriginalRouter(Router):
    """Reference implementation of the original spec, evaluation only."""
    def __init__(self, *args):
        super().__init__(*args)
        for hour in range(24):
            raw = self._raw_risk(hour)
            peak = max(raw.values(), default=0) or 1
            self.risks[hour] = {node: value / peak for node, value in raw.items()}

    def edge_cost(self, destination, data, alpha, hour):
        if alpha == 0:
            return float(data["length"])
        return (data["length"] * data.get("lane_multiplier", 1.0)
                * (1 + alpha * self.risks[hour][destination]))


def measure(router, start, end, alpha=0.5, hour=8):
    graph = router.graph
    def cost(u, v, edges):
        return min(router.edge_cost(v, data, alpha, hour) for data in edges.values())
    path = nx.shortest_path(graph, start, end, weight=cost, method="dijkstra")
    ids = set().union(*(set(graph.nodes[node].get("collision_ids", [])) for node in path))
    distance = lane_distance = 0.0
    signature = []
    for u, v in zip(path, path[1:]):
        key, data = min(graph[u][v].items(), key=lambda item: router.edge_cost(v, item[1], alpha, hour))
        distance += data["length"]
        if data.get("lane_multiplier", 1) < 1:
            lane_distance += data["length"]
        signature.append((u, v, key))
    return {"ksi_total": len(ids), "distance_m": distance,
            "lane_share": lane_distance / distance if distance else 0, "signature": signature}


def summarize(rows, direct, night, novice, confident):
    ratios = [row["distance_m"] / base["distance_m"] for row, base in zip(rows, direct) if base["distance_m"]]
    return {
        "sum_route_collision_counts": sum(row["ksi_total"] for row in rows),
        "fewer_than_direct": sum(row["ksi_total"] < base["ksi_total"] for row, base in zip(rows, direct)),
        "same_as_direct": sum(row["ksi_total"] == base["ksi_total"] for row, base in zip(rows, direct)),
        "more_than_direct": sum(row["ksi_total"] > base["ksi_total"] for row, base in zip(rows, direct)),
        "mean_lane_share": round(statistics.mean(row["lane_share"] for row in rows), 4),
        "median_distance_ratio_to_direct": round(statistics.median(ratios), 4),
        "maximum_distance_ratio_to_direct": round(max(ratios), 4),
        "routes_changed_8am_to_10pm": sum(a["signature"] != b["signature"] for a, b in zip(rows, night)),
        "routes_changed_novice_to_confident": sum(a["signature"] != b["signature"] for a, b in zip(novice, confident)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with (ROOT / "data/processed/toronto.pkl").open("rb") as source:
        bundle = pickle.load(source)  # trusted local build only
    records = [Collision(**record) for record in bundle["records"]]
    current = Router(bundle["graph"], records)
    original = OriginalRouter(bundle["graph"], records)
    nodes = [(name, current.nearest([lat, lng])) for name, lat, lng in LANDMARKS]
    pairs = [(name_a, a, name_b, b) for name_a, a in nodes for name_b, b in nodes if name_a != name_b]
    direct = [measure(current, a, b, alpha=0) for _, a, _, b in pairs]
    report = {"model": MODEL_VERSION, "risk_penalty_m": RISK_PENALTY_M,
              "data_build": bundle["metadata"], "route_pairs": len(pairs),
              "settings": {"level": 2, "hour": 8, "comparison_hour": 22},
              "notes": "Descriptive heuristic comparison on landmark trips, not safety validation. "
                       "Collision counts repeat across different trips. Lane share is an unweighted mean of route shares.",
              "direct": {"sum_route_collision_counts": sum(row["ksi_total"] for row in direct),
                         "mean_lane_share": round(statistics.mean(row["lane_share"] for row in direct), 4)},
              "models": {}}
    for name, router in [("original", original), ("current", current)]:
        rows = [measure(router, a, b) for _, a, _, b in pairs]
        night = [measure(router, a, b, hour=22) for _, a, _, b in pairs]
        novice = [measure(router, a, b, alpha=ALPHAS[1]) for _, a, _, b in pairs]
        confident = [measure(router, a, b, alpha=ALPHAS[3]) for _, a, _, b in pairs]
        report["models"][name] = summarize(rows, direct, night, novice, confident)
    report["examples"] = []
    for start, end in [("Kensington Market", "Christie Pits Park"),
                       (LANDMARKS[0][0], "St. Lawrence Market")]:
        node_map = dict(nodes)
        example = {"origin": start, "destination": end}
        for name, router, alpha, hour in [("direct", current, 0, 8), ("original", original, 0.5, 8),
                                          ("current_8am", current, 0.5, 8), ("current_10pm", current, 0.5, 22)]:
            result = measure(router, node_map[start], node_map[end], alpha, hour)
            result.pop("signature")
            example[name] = result
        report["examples"].append(example)
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded)


if __name__ == "__main__":
    main()
