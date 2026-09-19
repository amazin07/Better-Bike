"""Export only a trusted, locally built graph cache for deployment."""
import pickle
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graph_bundle import export_bundle


if __name__ == "__main__":
    with (ROOT / "data/processed/toronto.pkl").open("rb") as source:
        bundle = pickle.load(source)
    target = ROOT / "routing-data/toronto.json.gz"
    export_bundle(bundle, target)
    print(f"Exported {len(bundle['graph'])} nodes to {target.relative_to(ROOT)} ({target.stat().st_size:,} bytes)")
