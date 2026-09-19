"""Stage existing static assets for Vercel's CDN; never download routing data."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "routing-data/toronto.json.gz").is_file():
    raise SystemExit("Missing routing bundle: run scripts/export_routing_bundle.py locally.")
target = ROOT / "public/static"
loader = ROOT / "node_modules/@stripe/connect-js/dist/pure.esm.js"
bundled = ROOT / "static/stripe-connect-loader.js"
if bundled.read_bytes() != loader.read_bytes():
    raise SystemExit("Refresh static/stripe-connect-loader.js from the locked @stripe/connect-js package.")
shutil.copytree(ROOT / "static", target, dirs_exist_ok=True)
