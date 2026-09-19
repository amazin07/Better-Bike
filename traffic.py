"""Live HERE traffic overlay for the Safer Ride map.

The browser never talks to HERE. Flask holds one in-memory snapshot of HERE's
flow and incident feeds and serves it to every viewer, so usage is bounded by
this module, not by the number of people looking at the map:

* Feeds refresh only when someone asks for the overlay (no background polling).
* Each feed has a time-to-live; the interval stretches as the month's call
  budget is used up, and a daily cap stops a runaway day.
* The call counter is saved in ``data/here_usage.json`` (a counter only, never
  HERE content). If that file is unreadable the cache fails closed.
* Failed attempts count against the budget, back off, and open a circuit
  breaker after repeated errors. A rejected key switches refreshing off.
* HERE data is held in memory only: no archive and no copy on disk.

The API key is used only inside ``HereClient`` and is never logged or returned.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

API_BASE = "https://data.traffic.hereapi.com/v7"
DEFAULT_BOUNDS = [[43.6275, -79.4454], [43.6989, -79.3460]]  # [[south, west], [north, east]]
DEFAULT_MONTHLY_BUDGET = 2500  # deliberately conservative until the real allowance is confirmed
MIN_JAM_FACTOR = 2.0           # flow items calmer than this are not sent to the browser
MIN_INTERVAL = 30              # floor on any single feed's refresh interval, in seconds
BACKOFF_START = 60
BACKOFF_MAX = 900
MAX_RESPONSE_BYTES = 15_000_000
MAX_TEXT = 200
NOT_CONFIGURED = ("Live traffic is not configured. Set HERE_API_KEY in .env "
                  "(or HERE_FIXTURE_DIR for offline development).")


class HereError(Exception):
    """A failed HERE call. The message never contains the request URL or key."""

    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


# --- clients --------------------------------------------------------------------------------

def _gunzip(body):
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
        data = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise HereError(None, "response too large")
    return data


def _retry_after(headers):
    raw = headers.get("Retry-After") if headers else None
    try:
        return max(0, int(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


class HereClient:
    counts_toward_budget = True

    def __init__(self, api_key, bbox, timeout=20):
        self._api_key = api_key
        self._bbox = bbox
        self._timeout = timeout

    def __repr__(self):
        return f"HereClient(bbox={self._bbox!r})"

    def fetch(self, endpoint):
        query = urllib.parse.urlencode({"locationReferencing": "shape", "in": self._bbox,
                                        "apiKey": self._api_key}, safe=":,")
        request = urllib.request.Request(f"{API_BASE}/{endpoint}?{query}",
                                         headers={"Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise HereError(None, "response too large")
                if response.headers.get("Content-Encoding") == "gzip":
                    body = _gunzip(body)
                return json.loads(body)
        except HereError:
            raise
        except urllib.error.HTTPError as err:
            retry = _retry_after(getattr(err, "headers", None))
            try:
                err.close()
            except Exception:
                pass
            raise HereError(err.code, f"HTTP {err.code}", retry) from None
        except (OSError, ValueError) as err:  # URLError, timeouts, bad JSON
            raise HereError(None, type(err).__name__) from None


class FixtureClient:
    """Serves saved responses for offline development. Costs no budget."""

    counts_toward_budget = False

    def __init__(self, directory):
        self._directory = Path(directory)

    def fetch(self, endpoint):
        path = self._directory / f"here_{endpoint}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HereError(None, f"fixture unreadable: {path.name}") from None


def bbox_from_bounds(bounds):
    (south, west), (north, east) = bounds

    def fmt(value):
        return f"{value:.6f}".rstrip("0").rstrip(".")

    return f"bbox:{fmt(west)},{fmt(south)},{fmt(east)},{fmt(north)}"


# --- normalisation: HERE JSON -> compact GeoJSON for the browser -----------------------------

def _is_point(point):
    return (isinstance(point, dict)
            and all(isinstance(point.get(k), (int, float)) and math.isfinite(point[k]) for k in ("lat", "lng")))


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _kmh(metres_per_second):
    value = _number(metres_per_second)
    return None if value is None else round(value * 3.6)


def _text(value, limit=MAX_TEXT):
    text = value.strip() if isinstance(value, str) else ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _localized(value):
    return _text(value.get("value")) if isinstance(value, dict) else ""


def _links(location):
    shape = location.get("shape") if isinstance(location, dict) else None
    links = shape.get("links") if isinstance(shape, dict) else None
    return [link for link in links or [] if isinstance(link, dict)]


def normalize_flow(payload):
    features = []
    for item in (payload or {}).get("results") or []:
        flow = item.get("currentFlow") or {}
        jam = _number(flow.get("jamFactor")) or 0.0
        closed = flow.get("traversability") == "closed"
        if jam < MIN_JAM_FACTOR and not closed:
            continue
        location = item.get("location") or {}
        links = _links(location)
        lines = []
        for link in links:
            line = [[round(p["lng"], 5), round(p["lat"], 5)] for p in link.get("points") or [] if _is_point(p)]
            if len(line) >= 2:
                lines.append(line)
        if not lines:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "MultiLineString", "coordinates": lines},
            "properties": {
                "name": _text(location.get("description"), 80),
                "jf": round(jam, 1),
                "kmh": _kmh(flow.get("speed")),
                "free_kmh": _kmh(flow.get("freeFlow")),
                "fc": next((l["functionalClass"] for l in links if l.get("functionalClass") is not None), None),
                "closed": closed,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def normalize_incidents(payload):
    features = []
    for item in (payload or {}).get("results") or []:
        details = item.get("incidentDetails") or {}
        point = next((p for link in _links(item.get("location")) for p in link.get("points") or []
                      if _is_point(p)), None)
        if point is None:
            continue
        summary = (_localized(details.get("summary")) or _localized(details.get("description"))
                   or _localized(details.get("typeDescription")))
        detail = _localized(details.get("description"))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(point["lng"], 5), round(point["lat"], 5)]},
            "properties": {
                "type": details.get("type"),
                "crit": details.get("criticality"),
                "closed": bool(details.get("roadClosed")),
                "summary": summary,
                "detail": detail if detail != summary else "",
                "start": details.get("startTime"),
                "end": details.get("endTime"),
            },
        })
    return {"type": "FeatureCollection", "features": features}


# --- configuration --------------------------------------------------------------------------

def load_env_file(path, environ=None):
    """Load KEY=VALUE lines from a .env file without overriding real variables."""
    env = os.environ if environ is None else environ
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip("\"'")
        if name and value and name not in env:
            env[name] = value


def _iso(timestamp):
    return None if timestamp is None else datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Feed:
    def __init__(self, name, base_ttl, normalize):
        self.name = name
        self.base_ttl = base_ttl
        self.normalize = normalize
        self.data = None
        self.fetched_at = None
        self.source_updated = None
        self.failures = 0
        self.next_try = 0.0


class TrafficCache:
    def __init__(self, client, usage_path=None, *, monthly_budget=DEFAULT_MONTHLY_BUDGET, flow_ttl=300,
                 incidents_ttl=600, daily_fraction=0.15, clock=time.time, breaker_failures=3,
                 breaker_pause=900):
        self._client = client
        self._counts = getattr(client, "counts_toward_budget", True)
        self._usage_path = Path(usage_path) if usage_path else None
        self._budget = max(0, int(monthly_budget))
        self._daily_cap = math.ceil(daily_fraction * self._budget)
        self._clock = clock
        self._breaker_failures = breaker_failures
        self._breaker_pause = breaker_pause
        self._feeds = {"flow": _Feed("flow", flow_ttl, normalize_flow),
                       "incidents": _Feed("incidents", incidents_ttl, normalize_incidents)}
        self._refresh_lock = threading.Lock()   # single flight: one refresher at a time
        self._state = threading.RLock()         # guards everything below, held only briefly
        self._usage = {"month": None, "used": 0, "day": None, "day_used": 0}
        self._usage_ok = True
        self._consecutive_failures = 0
        self._breaker_until = 0.0
        self._rejected = False
        self._version = 0
        self._load_usage()

    @classmethod
    def from_env(cls, bounds, environ=None, usage_path=None, clock=time.time):
        env = os.environ if environ is None else environ
        budget = DEFAULT_MONTHLY_BUDGET
        raw = (env.get("HERE_MONTHLY_BUDGET") or "").strip()
        if raw:
            try:
                budget = int(raw)
                if budget < 0:
                    raise ValueError
            except ValueError:
                budget = DEFAULT_MONTHLY_BUDGET
                log.warning("HERE_MONTHLY_BUDGET is not a non-negative integer; using %d", budget)
        fixture = (env.get("HERE_FIXTURE_DIR") or "").strip()
        key = (env.get("HERE_API_KEY") or "").strip().strip("\"'")
        client = None
        if fixture:
            client = FixtureClient(fixture)          # offline development wins over a live key
        elif key:
            client = HereClient(key, bbox_from_bounds(bounds or DEFAULT_BOUNDS))
        return cls(client, usage_path, monthly_budget=budget, clock=clock)

    # -- usage counter -----------------------------------------------------------------------

    def _load_usage(self):
        if not self._usage_path:
            return
        try:
            raw = self._usage_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            raw = None
        try:
            data = json.loads(raw)
            fields = {"month": data["month"], "used": data["used"], "day": data["day"],
                      "day_used": data["day_used"]}
            if not (isinstance(fields["month"], str) and isinstance(fields["day"], str)
                    and all(isinstance(fields[k], int) and fields[k] >= 0 for k in ("used", "day_used"))):
                raise ValueError
            self._usage = fields
        except (TypeError, KeyError, ValueError):
            self._usage_ok = False
            log.warning("The HERE usage counter file is unreadable; live calls stay off until it is "
                        "fixed or deleted.")

    def _save_usage(self):
        if not self._usage_path:
            return
        try:
            self._usage_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._usage_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._usage), encoding="utf-8")
            os.replace(temporary, self._usage_path)
        except OSError:
            log.warning("Could not save the HERE usage counter.")

    def _roll(self, now):
        moment = datetime.fromtimestamp(now, timezone.utc)
        month, day = moment.strftime("%Y-%m"), moment.strftime("%Y-%m-%d")
        if self._usage["month"] != month:
            self._usage.update(month=month, used=0, day=day, day_used=0)
        elif self._usage["day"] != day:
            self._usage.update(day=day, day_used=0)

    def _spend(self, now):
        with self._state:
            self._roll(now)
            self._usage["used"] += 1
            self._usage["day_used"] += 1
            self._save_usage()

    # -- policy ------------------------------------------------------------------------------

    def _blocked(self, now):
        """Return (status, message) if no call may be made right now, else None."""
        with self._state:
            if self._client is None:
                return "disabled", NOT_CONFIGURED
            if self._rejected:
                return "disabled", ("HERE rejected the API key. Live traffic is off until the server "
                                    "restarts with a valid key.")
            if self._counts:
                if not self._usage_ok:
                    return "budget_exhausted", ("The HERE usage counter is unreadable, so live calls are "
                                                "paused. Fix or delete data/here_usage.json.")
                self._roll(now)
                if self._usage["used"] >= self._budget:
                    return "budget_exhausted", "The monthly HERE call budget has been reached."
                if self._usage["day_used"] >= self._daily_cap:
                    return "budget_exhausted", "The daily HERE call limit has been reached."
            if now < self._breaker_until:
                return "unavailable", "Refreshing is paused after repeated HERE errors."
            return None

    def _ttl(self, feed):
        with self._state:
            fraction = self._usage["used"] / self._budget if self._budget else 1.0
        factor = 4 if fraction >= 0.8 else 2 if fraction >= 0.5 else 1
        return max(MIN_INTERVAL, feed.base_ttl * factor)

    def _refresh_due(self):
        for feed in self._feeds.values():
            now = self._clock()
            if self._blocked(now):
                return
            if feed.data is not None and now - feed.fetched_at < self._ttl(feed):
                continue
            if now < feed.next_try:
                continue
            self._fetch(feed, now)

    def _fetch(self, feed, now):
        if self._counts:
            self._spend(now)          # counted before the call so a crash cannot undercount
        try:
            payload = self._client.fetch(feed.name)
            data = feed.normalize(payload)
            source_updated = payload.get("sourceUpdated") if isinstance(payload, dict) else None
        except HereError as err:
            self._record_failure(feed, now, err)
            return
        except (AttributeError, KeyError, TypeError, ValueError):
            self._record_failure(feed, now, HereError(None, "unexpected response shape"))
            return
        with self._state:
            feed.data, feed.source_updated = data, source_updated
            feed.fetched_at = self._clock()
            feed.failures = 0
            feed.next_try = 0.0
            self._consecutive_failures = 0
            self._version += 1

    def _record_failure(self, feed, now, err):
        log.warning("HERE %s refresh failed: %s", feed.name, err)
        with self._state:
            feed.failures += 1
            self._consecutive_failures += 1
            backoff = min(BACKOFF_START * 2 ** (feed.failures - 1), BACKOFF_MAX)
            feed.next_try = now + max(backoff, err.retry_after or 0)
            if err.status in (401, 403):
                self._rejected = True
            if self._consecutive_failures >= self._breaker_failures:
                self._breaker_until = now + self._breaker_pause
                self._consecutive_failures = 0
                log.warning("HERE refreshes paused for %d seconds after repeated errors.", self._breaker_pause)

    # -- public ------------------------------------------------------------------------------

    def snapshot(self):
        """Return the overlay payload, refreshing due feeds first if nobody else is."""
        loading = True
        if self._refresh_lock.acquire(blocking=False):
            try:
                self._refresh_due()
                loading = False
            finally:
                self._refresh_lock.release()
        return self._payload(loading)

    def status(self):
        with self._state:
            now = self._clock()
            self._roll(now)
            mode = "off" if self._client is None else "live" if self._counts else "fixture"
            return {"configured": self._client is not None, "mode": mode,
                    "budget": self._budget_view(),
                    "daily": {"used": self._usage["day_used"], "limit": self._daily_cap},
                    "breaker_open": now < self._breaker_until,
                    "key_rejected": self._rejected}

    def _budget_view(self):
        return {"used": self._usage["used"], "limit": self._budget, "month": self._usage["month"]}

    def _status_and_message(self, now, loading):
        flow = self._feeds["flow"]
        blocked = self._blocked(now)
        if self._client is None:
            return "disabled", NOT_CONFIGURED
        if flow.data is None:
            if blocked and blocked[0] in ("budget_exhausted", "disabled"):
                return blocked
            if loading:
                return "loading", "Traffic data is loading."
            return "unavailable", "Traffic data is temporarily unavailable."
        age = now - flow.fetched_at
        if age <= 2 * flow.base_ttl:
            return "ok", None
        if blocked and blocked[0] in ("budget_exhausted", "disabled"):
            return blocked
        return "stale", f"Showing traffic data that is {int(age // 60)} minutes old."

    def _payload(self, loading):
        with self._state:
            now = self._clock()
            self._roll(now)
            status, message = self._status_and_message(now, loading)
            flow, incidents = self._feeds["flow"], self._feeds["incidents"]
            budget = self._budget_view()
            return {
                "status": status,
                "flow": flow.data,
                "incidents": incidents.data,
                "version": self._version,
                "etag": f"{self._version}-{status}-{budget['used']}",
                "meta": {
                    "flow_fetched_at": _iso(flow.fetched_at),
                    "flow_source_updated": flow.source_updated,
                    "incidents_fetched_at": _iso(incidents.fetched_at),
                    "incidents_source_updated": incidents.source_updated,
                    "budget": budget,
                    "message": message,
                },
            }
