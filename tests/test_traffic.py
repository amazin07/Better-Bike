"""Live HERE traffic overlay: budget-capped in-memory cache, client, and API.

Everything here is synthetic. No test touches the network, the real .env, or
real HERE data.
"""
import gzip
import io
import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

import traffic
from app import create_app
from traffic import (FixtureClient, HereClient, HereError, TrafficCache,
                     bbox_from_bounds, load_env_file, normalize_flow, normalize_incidents)

BOUNDS = [[43.6275, -79.4454], [43.6989, -79.3460]]


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def flow_payload():
    def item(name, jam, closed=False):
        return {"location": {"description": name, "length": 100.0,
                             "shape": {"links": [{"points": [{"lat": 43.660001, "lng": -79.390001},
                                                             {"lat": 43.661, "lng": -79.391}],
                                                  "length": 100.0, "functionalClass": 4}]}},
                "currentFlow": {"speed": 5.0, "freeFlow": 10.0, "jamFactor": jam, "confidence": 0.9,
                                "traversability": "closed" if closed else "open"}}
    return {"sourceUpdated": "2026-09-19T16:00:00Z",
            "results": [item("Slow St", 5.0), item("Free St", 0.5), item("Shut St", 10.0, closed=True)]}


def incidents_payload():
    return {"sourceUpdated": "2026-09-19T16:00:05Z", "results": [
        {"location": {"length": 50.0, "shape": {"links": [{"points": [{"lat": 43.65, "lng": -79.38},
                                                                      {"lat": 43.651, "lng": -79.381}]}]}},
         "incidentDetails": {"id": "1", "type": "roadClosure", "criticality": "critical", "roadClosed": True,
                             "startTime": "2026-09-01T00:00:00Z", "endTime": "2026-12-31T00:00:00Z",
                             "summary": {"value": "Closed due to road construction"},
                             "description": {"value": "At Test St - closed"}}}]}


class FakeClient:
    counts_toward_budget = True

    def __init__(self):
        self.calls = []
        self.fail_with = None

    def fetch(self, endpoint):
        self.calls.append(endpoint)
        if self.fail_with:
            raise self.fail_with
        return json.loads(json.dumps(flow_payload() if endpoint == "flow" else incidents_payload()))


def make_cache(client=None, tmp_path=None, clock=None, **options):
    clock = clock or FakeClock()
    usage = tmp_path / "usage.json" if tmp_path else None
    return TrafficCache(client or FakeClient(), usage_path=usage, clock=clock, **options), clock


# --- refresh policy -------------------------------------------------------------------------

def test_first_snapshot_fetches_each_feed_once_then_serves_from_memory():
    client = FakeClient()
    cache, clock = make_cache(client)
    first = cache.snapshot()
    assert first["status"] == "ok"
    assert client.calls == ["flow", "incidents"]
    clock.advance(299)
    cache.snapshot()
    assert client.calls == ["flow", "incidents"]


def test_each_feed_refreshes_on_its_own_schedule():
    client = FakeClient()
    cache, clock = make_cache(client)
    cache.snapshot()
    clock.advance(301)
    cache.snapshot()
    assert client.calls == ["flow", "incidents", "flow"]
    clock.advance(299)  # flow is 299 s old (not due); incidents are 600 s old (due)
    cache.snapshot()
    assert client.calls == ["flow", "incidents", "flow", "incidents"]


def test_concurrent_requests_share_one_refresh():
    class BlockingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()

        def fetch(self, endpoint):
            self.entered.set()
            assert self.release.wait(5)
            return super().fetch(endpoint)

    client = BlockingClient()
    cache, _ = make_cache(client)
    results = []
    worker = threading.Thread(target=lambda: results.append(cache.snapshot()))
    worker.start()
    try:
        assert client.entered.wait(5)
        during = []
        second = threading.Thread(target=lambda: during.append(cache.snapshot()))
        second.start()
        second.join(2)
        assert not second.is_alive(), "a second request must not block on the refresh in progress"
        assert during[0]["status"] == "loading" and during[0]["flow"] is None
    finally:
        client.release.set()
        worker.join(5)
    assert results[0]["status"] == "ok"
    assert client.calls.count("flow") == 1 and client.calls.count("incidents") == 1


def test_disabled_without_a_client():
    snap = TrafficCache(None).snapshot()
    assert snap["status"] == "disabled" and snap["flow"] is None
    assert "HERE_API_KEY" in snap["meta"]["message"]


# --- budget ---------------------------------------------------------------------------------

def test_monthly_budget_stops_calls_and_keeps_last_data():
    client = FakeClient()
    cache, clock = make_cache(client, monthly_budget=3, daily_fraction=1.0)
    cache.snapshot()                       # 2 of 3 calls used, so refresh intervals double to 600 s
    clock.advance(601)
    cache.snapshot()                       # flow refresh = 3rd and last call
    assert client.calls == ["flow", "incidents", "flow"]
    clock.advance(301)
    assert cache.snapshot()["status"] == "ok"      # blocked, but the data is still fresh enough
    clock.advance(400)                             # flow now older than twice its base ttl
    snap = cache.snapshot()
    assert client.calls == ["flow", "incidents", "flow"]
    assert snap["status"] == "budget_exhausted"
    assert snap["flow"]["features"], "the last snapshot stays visible"
    assert snap["meta"]["budget"] == {"used": 3, "limit": 3, "month": "2026-09"}


def test_daily_cap_blocks_a_runaway_day_and_resets_next_day():
    client = FakeClient()
    cache, clock = make_cache(client, monthly_budget=20)     # daily cap = ceil(0.15 * 20) = 3
    cache.snapshot()
    clock.advance(301)
    cache.snapshot()
    assert len(client.calls) == 3
    clock.advance(301)
    cache.snapshot()
    assert len(client.calls) == 3
    clock.advance(86400)
    cache.snapshot()
    assert len(client.calls) > 3


def test_refresh_interval_stretches_as_budget_is_used(tmp_path):
    (tmp_path / "usage.json").write_text(json.dumps(
        {"month": "2026-09", "used": 20, "day": "2026-09-19", "day_used": 0}))
    client = FakeClient()
    cache, clock = make_cache(client, tmp_path, monthly_budget=40)   # 50% used -> ttl x2
    cache.snapshot()
    clock.advance(400)
    cache.snapshot()
    assert client.calls == ["flow", "incidents"]
    clock.advance(201)
    cache.snapshot()
    assert client.calls == ["flow", "incidents", "flow"]


def test_usage_counter_persists_and_rolls_over_each_month(tmp_path):
    cache, clock = make_cache(tmp_path=tmp_path)
    cache.snapshot()
    saved = json.loads((tmp_path / "usage.json").read_text())
    assert saved == {"month": "2026-09", "used": 2, "day": "2026-09-19", "day_used": 2}
    again, _ = make_cache(tmp_path=tmp_path, clock=clock)
    assert again.status()["budget"]["used"] == 2
    (tmp_path / "usage.json").write_text(json.dumps(
        {"month": "2026-08", "used": 2000, "day": "2026-08-31", "day_used": 5}))
    fresh, _ = make_cache(tmp_path=tmp_path)
    assert fresh.status()["budget"]["used"] == 0


def test_unreadable_usage_file_fails_closed(tmp_path):
    (tmp_path / "usage.json").write_text("not json")
    client = FakeClient()
    cache, _ = make_cache(client, tmp_path)
    snap = cache.snapshot()
    assert client.calls == []
    assert snap["status"] == "budget_exhausted" and "usage" in snap["meta"]["message"].lower()


def test_fixture_client_costs_no_budget(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "here_flow.json").write_text(json.dumps(flow_payload()))
    (fixtures / "here_incidents.json").write_text(json.dumps(incidents_payload()))
    cache, _ = make_cache(FixtureClient(fixtures), tmp_path)
    snap = cache.snapshot()
    assert snap["status"] == "ok" and snap["meta"]["budget"]["used"] == 0


# --- failures -------------------------------------------------------------------------------

def test_failed_calls_count_and_back_off_then_circuit_breaker_pauses_everything():
    client = FakeClient()
    client.fail_with = HereError(500, "HTTP 500")
    cache, clock = make_cache(client)
    assert cache.snapshot()["status"] == "unavailable"
    assert client.calls == ["flow", "incidents"]
    cache.snapshot()                                   # inside the 60 s backoff: no retry
    assert len(client.calls) == 2
    clock.advance(61)
    cache.snapshot()                                   # third consecutive failure opens the breaker
    assert client.calls == ["flow", "incidents", "flow"]
    clock.advance(120)
    cache.snapshot()
    assert len(client.calls) == 3, "no calls while the breaker is open"
    assert cache.status()["breaker_open"] is True
    client.fail_with = None
    clock.advance(900)
    snap = cache.snapshot()
    assert snap["status"] == "ok" and len(client.calls) == 5
    assert snap["meta"]["budget"]["used"] == 5, "failed attempts are counted"


def test_auth_failure_disables_refreshing_for_good():
    client = FakeClient()
    client.fail_with = HereError(401, "HTTP 401")
    cache, clock = make_cache(client)
    snap = cache.snapshot()
    assert snap["status"] == "disabled" and "rejected" in snap["meta"]["message"]
    clock.advance(10 * 3600)
    cache.snapshot()
    assert client.calls == ["flow"]


def test_rate_limit_response_honours_retry_after():
    client = FakeClient()
    client.fail_with = HereError(429, "HTTP 429", retry_after=500)
    cache, clock = make_cache(client)
    cache.snapshot()
    client.fail_with = None
    clock.advance(400)
    cache.snapshot()
    assert client.calls == ["flow", "incidents"]
    clock.advance(101)
    cache.snapshot()
    assert client.calls[2:] == ["flow", "incidents"]


def test_failed_refresh_keeps_serving_last_data_until_it_goes_stale():
    client = FakeClient()
    cache, clock = make_cache(client)
    cache.snapshot()
    client.fail_with = HereError(500, "HTTP 500")
    clock.advance(301)
    snap = cache.snapshot()
    assert snap["status"] == "ok" and snap["flow"]["features"]
    clock.advance(400)                                  # 701 s old, more than twice the 300 s ttl
    snap = cache.snapshot()
    assert snap["status"] == "stale" and snap["flow"]["features"]


# --- normalisation --------------------------------------------------------------------------

def test_flow_is_compact_geojson_keeping_slow_and_closed_roads_only():
    collection = normalize_flow(flow_payload())
    assert collection["type"] == "FeatureCollection"
    names = [f["properties"]["name"] for f in collection["features"]]
    assert names == ["Slow St", "Shut St"]
    slow = collection["features"][0]
    assert slow["geometry"] == {"type": "MultiLineString",
                                "coordinates": [[[-79.39, 43.66], [-79.391, 43.661]]]}
    assert slow["properties"] == {"name": "Slow St", "jf": 5.0, "kmh": 18, "free_kmh": 36, "fc": 4,
                                  "closed": False}
    assert collection["features"][1]["properties"]["closed"] is True
    assert traffic.MIN_JAM_FACTOR > 0.5


def test_flow_skips_items_without_geometry():
    payload = flow_payload()
    payload["results"].append({"location": {"description": "No shape"}, "currentFlow": {"jamFactor": 9}})
    assert len(normalize_flow(payload)["features"]) == 2


def test_incidents_become_points_with_short_text():
    payload = incidents_payload()
    payload["results"].append({"location": {"length": 5}, "incidentDetails": {"type": "other"}})
    payload["results"][0]["incidentDetails"]["summary"]["value"] = "x" * 500
    collection = normalize_incidents(payload)
    assert len(collection["features"]) == 1
    feature = collection["features"][0]
    assert feature["geometry"] == {"type": "Point", "coordinates": [-79.38, 43.65]}
    props = feature["properties"]
    assert props["type"] == "roadClosure" and props["crit"] == "critical" and props["closed"] is True
    assert len(props["summary"]) <= 200 and props["start"] == "2026-09-01T00:00:00Z"


def test_bbox_uses_west_south_east_north_order():
    assert bbox_from_bounds(BOUNDS) == "bbox:-79.4454,43.6275,-79.346,43.6989"


# --- HERE client and configuration ----------------------------------------------------------

class FakeResponse:
    status = 200

    def __init__(self, body, gzipped=False):
        self._body = body
        self.headers = {"Content-Encoding": "gzip"} if gzipped else {}

    def read(self, limit=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_client_asks_for_gzip_and_decodes_it(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        return FakeResponse(gzip.compress(json.dumps({"results": []}).encode()), gzipped=True)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = HereClient("SECRET-KEY-123", "bbox:-79.4,43.6,-79.3,43.7")
    assert client.fetch("flow") == {"results": []}
    assert seen["url"].startswith("https://data.traffic.hereapi.com/v7/flow?")
    assert "locationReferencing=shape" in seen["url"] and "in=bbox:-79.4,43.6,-79.3,43.7" in seen["url"]
    assert seen["headers"]["accept-encoding"] == "gzip"


@pytest.mark.parametrize("failure", [
    lambda url: urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"{}")),
    lambda url: urllib.error.URLError(f"could not reach {url}"),
    lambda url: TimeoutError(f"timed out fetching {url}"),
])
def test_client_errors_never_contain_the_api_key(monkeypatch, failure):
    def fake_urlopen(request, timeout=None):
        raise failure(request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(HereError) as info:
        HereClient("SECRET-KEY-123", "bbox:1,2,3,4").fetch("incidents")
    assert "SECRET-KEY-123" not in str(info.value) and "SECRET-KEY-123" not in repr(info.value)


def test_env_file_sets_missing_variables_only(tmp_path):
    path = tmp_path / ".env"
    path.write_text('# comment\n\nHERE_API_KEY=abc123\nHERE_MONTHLY_BUDGET = "50"\nEMPTY=\nnot a pair\n')
    environ = {"HERE_MONTHLY_BUDGET": "99"}
    load_env_file(path, environ)
    assert environ == {"HERE_MONTHLY_BUDGET": "99", "HERE_API_KEY": "abc123"}
    load_env_file(tmp_path / "missing.env", environ)      # absent file is fine


def test_from_env_chooses_a_mode_and_defaults_the_budget(tmp_path):
    assert TrafficCache.from_env(BOUNDS, environ={}).status()["mode"] == "off"
    (tmp_path / "here_flow.json").write_text(json.dumps(flow_payload()))
    (tmp_path / "here_incidents.json").write_text(json.dumps(incidents_payload()))
    fixture = TrafficCache.from_env(BOUNDS, environ={"HERE_FIXTURE_DIR": str(tmp_path)})
    assert fixture.status()["mode"] == "fixture"
    live = TrafficCache.from_env(BOUNDS, environ={"HERE_API_KEY": "k", "HERE_MONTHLY_BUDGET": "abc"})
    status = live.status()
    assert status["mode"] == "live" and status["budget"]["limit"] == 2500


# --- HTTP API -------------------------------------------------------------------------------

def app_client(cache, tmp_path):
    return create_app(cache_path=tmp_path / "missing.pkl", traffic=cache).test_client()


def test_traffic_endpoint_returns_overlay_and_supports_etag(tmp_path):
    cache, clock = make_cache()
    web = app_client(cache, tmp_path)
    response = web.get("/api/traffic")
    body = response.get_json()
    assert response.status_code == 200 and body["status"] == "ok"
    assert len(body["flow"]["features"]) == 2 and len(body["incidents"]["features"]) == 1
    assert body["meta"]["budget"]["used"] == 2
    assert body["meta"]["flow_fetched_at"] == "2026-09-19T12:00:00Z"
    assert response.headers["Cache-Control"] == "no-cache"
    etag = response.headers["ETag"]
    assert web.get("/api/traffic", headers={"If-None-Match": etag}).status_code == 304
    clock.advance(301)
    changed = web.get("/api/traffic", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["ETag"] != etag


def test_traffic_endpoint_when_not_configured_is_a_normal_response(tmp_path):
    response = app_client(TrafficCache(None), tmp_path).get("/api/traffic")
    assert response.status_code == 200
    assert response.get_json()["status"] == "disabled"
    assert response.headers["Cache-Control"] == "no-store"


def test_health_reports_traffic_state_without_calling_here(tmp_path):
    client = FakeClient()
    cache, _ = make_cache(client)
    response = app_client(cache, tmp_path).get("/api/health")
    traffic_state = response.get_json()["traffic"]
    assert traffic_state["mode"] == "live" and traffic_state["configured"] is True
    assert traffic_state["budget"]["used"] == 0
    assert client.calls == []


def test_key_never_appears_in_responses_or_logs(monkeypatch, caplog, tmp_path):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cache = TrafficCache(HereClient("SECRET-KEY-123", "bbox:1,2,3,4"), clock=FakeClock())
    with caplog.at_level(logging.DEBUG):
        response = app_client(cache, tmp_path).get("/api/traffic")
    assert response.get_json()["status"] == "unavailable"
    assert "SECRET-KEY-123" not in response.get_data(as_text=True)
    assert "SECRET-KEY-123" not in caplog.text
