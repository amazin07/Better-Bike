"""BikeBetter website. All route calculations use the local Toronto cache."""
from __future__ import annotations

import logging
import math
import os
import pickle
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from bikedata import load_parking
from graph_bundle import load_bundle
from routing import Collision, RouteError, Router
from scoring import safety_score
from traffic import DEFAULT_BOUNDS, TrafficCache, load_env_file
from firebase_config import public_firebase_config
from payments import payments_blueprint, public_payment_config
from rewards import rewards_blueprint

ROOT = Path(__file__).resolve().parent
LANDMARKS = [
    ("University of Toronto · King's College Circle", 43.6629, -79.3957),
    ("Union Station", 43.6453, -79.3806),
    ("Trinity Bellwoods Park", 43.6478, -79.4139),
    ("Toronto Metropolitan University", 43.6577, -79.3788),
    ("St. Lawrence Market", 43.6487, -79.3715),
    ("Kensington Market", 43.6547, -79.4023),
    ("Christie Pits Park", 43.6647, -79.4207),
    ("Toronto Reference Library", 43.6718, -79.3867),
    ("Nathan Phillips Square", 43.6525, -79.3835),
    ("Royal Ontario Museum", 43.6677, -79.3948),
    ("Harbourfront Centre", 43.6389, -79.3828),
    ("Riverdale Park West", 43.6670, -79.3606),
]


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Send a JSON object with origin, destination, level, and hour.")
    if payload.get("city", "toronto") != "toronto":
        raise ValueError("Only Toronto is supported.")
    for key in ("origin", "destination"):
        value = payload.get(key)
        if not isinstance(value, list) or len(value) != 2 or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value
        ):
            raise ValueError(f"{key} must be [latitude, longitude].")
        if not -90 <= value[0] <= 90 or not -180 <= value[1] <= 180:
            raise ValueError(f"{key} coordinates are out of range.")
    if type(payload.get("level")) is not int or payload["level"] not in (1, 2, 3):
        raise ValueError("level must be 1, 2, or 3.")
    if type(payload.get("hour")) is not int or not 0 <= payload["hour"] <= 23:
        raise ValueError("hour must be an integer from 0 to 23.")
    if "traffic" in payload and type(payload["traffic"]) is not bool:
        raise ValueError("traffic must be true or false.")
    return payload


def create_app(router=None, cache_path=None, traffic=None):
    app = Flask(__name__, static_folder=str(ROOT / "static"))
    app.register_blueprint(payments_blueprint())
    app.register_blueprint(rewards_blueprint())
    app.config["MAX_CONTENT_LENGTH"] = 8192
    @app.before_request
    def payment_payload_limit():
        if request.endpoint in {'payments.webhook', 'payments.connect_webhook'}:
            request.max_content_length = 262144
    if router is None:
        path = Path(cache_path) if cache_path is not None else ROOT / "data/processed/toronto.pkl"
        if cache_path is None and (os.getenv("VERCEL") or not path.exists()):
            path = ROOT / "routing-data/toronto.json.gz"
        try:
            if path.name.endswith(".json.gz"):
                bundle = load_bundle(path)
            else:
                with path.open("rb") as source:
                    bundle = pickle.load(source)
            router = Router(bundle["graph"], [Collision(**row) for row in bundle["records"]], bundle["metadata"],
                            parking=load_parking(ROOT / "static/bike-parking.json"))
        except (OSError, ValueError, KeyError, pickle.UnpicklingError, EOFError):
            logging.exception("Routing cache unavailable. Run prepare_data.py, then restart Flask.")

    if traffic is None:
        # Live traffic is an opt-in overlay. Unconfigured, it is off and makes no HERE calls.
        bounds = (getattr(router, "metadata", None) or {}).get("bounds") or DEFAULT_BOUNDS
        traffic = TrafficCache.from_env(bounds, usage_path=ROOT / "data/here_usage.json")

    places = [{"label": name, "coordinate": [lat, lng]} for name, lat, lng in LANDMARKS]
    if router:
        intersections = set()
        for node, data in router.graph.nodes(data=True):
            names = set()
            for _, _, edge in router.graph.edges(node, data=True):
                name = edge.get("name", [])
                names.update(name if isinstance(name, list) else [name])
            names.discard("")
            if len(names) < 2:
                continue
            label = " & ".join(sorted(names))
            if label not in intersections:
                intersections.add(label)
                places.append({"label": label, "coordinate": [data["y"], data["x"]]})

    @app.get("/")
    def index():
        return send_from_directory(ROOT, "index.html")

    @app.get("/static/stripe-connect-loader.js")
    def stripe_connect_loader():
        # Only Stripe's npm loading wrapper is local; Connect.js comes from Stripe.
        return send_from_directory(ROOT / "node_modules/@stripe/connect-js/dist", "pure.esm.js")

    def firebase_page(filename):
        response = send_from_directory(ROOT, filename)
        local_connect = " http://127.0.0.1:8080 http://127.0.0.1:9099" if os.getenv("FIREBASE_USE_EMULATORS") == "1" else ""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            "script-src 'self' https://www.gstatic.com https://apis.google.com https://*.stripe.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://*.googleusercontent.com https://*.stripe.com; "
            "frame-src https://*.stripe.com https://*.link.com https://blyatbike.firebaseapp.com http://127.0.0.1:9099; "
            "connect-src 'self' https://*.googleapis.com https://*.stripe.com https://*.link.com" + local_connect + ";"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    @app.get("/rentals")
    def rentals():
        return firebase_page("rentals.html")

    @app.get("/missing")
    def missing():
        return firebase_page("missing-bikes.html")

    @app.get("/api/health")
    def health():
        if not router:
            return jsonify(status="unavailable", graph_nodes=0, collision_records=0,
                           error="Run prepare_data.py, then restart the server.",
                           traffic=traffic.status()), 503
        return jsonify(**{**router.metadata, "status": "ok", "graph_nodes": len(router.graph),
                          "collision_records": len(router.records), "traffic": traffic.status()})

    @app.get("/api/config")
    def config():
        # CARTO basemap keys are browser-visible; restrict their allowed referrers.
        return jsonify(carto_key=os.environ.get("CARTO_BASEMAP_KEY", ""), firebase=public_firebase_config(), payments=public_payment_config())

    @app.get("/api/places")
    def search():
        query = request.args.get("q", "").strip().lower()[:120]
        words = query.replace("&", " ").split()
        return jsonify([p for p in places if all(word in p["label"].lower() for word in words)][:8])

    @app.post("/api/route")
    def route():
        try:
            payload = validate_payload(request.get_json(silent=True))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        if not router:
            return jsonify(error="Routing is unavailable. The collision map is still shown below."), 503
        # Live traffic joins the score only when the client asks (its Live traffic switch is on)
        # and the server already holds fresh data. This never calls HERE. The same score both
        # picks the route for beginner and intermediate riders and is shown on the cards.
        index = traffic.congestion_index() if payload.get("traffic") else None

        def score(path):
            share = index.share(path["geometry"]["coordinates"]) if index is not None else None
            return safety_score(path["ksi_total"], path["ksi_fatal"], path["distance_m"], share)

        try:
            result = router.route(payload["origin"], payload["destination"],
                                  payload["level"], payload["hour"], score_fn=score)
        except RouteError as error:
            return jsonify(error=str(error)), 422
        result["traffic_used"] = index is not None
        return jsonify(result)

    @app.get("/api/traffic")
    def traffic_overlay():
        # The only place HERE data leaves the server. May trigger a budget-capped refresh.
        body = traffic.snapshot()
        etag = body.pop("etag")
        response = jsonify(body)
        if body["flow"] is None:
            response.headers["Cache-Control"] = "no-store"
            return response
        response.set_etag(etag, weak=True)
        response.headers["Cache-Control"] = "no-cache"  # always revalidate; a 304 costs no HERE call
        return response.make_conditional(request)

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error="The request is too large."), 413

    return app


if __name__ == "__main__":
    load_env_file(ROOT / ".env")  # only the dev server reads .env; deployments set real variables
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5001)), debug=False)
elif os.getenv("VERCEL"):
    app = create_app()
