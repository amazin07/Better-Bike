"""Bicycle theft points and bike parking spots: normalising and loading. No network.

scripts/build_bike_data.py downloads the sources and uses these functions to write the
small published files static/bike-thefts.json and static/bike-parking.json. The app only
reads those files: `load_parking` here for routing, and the browser loads the theft file
directly for the map layer.

Sources: Toronto Police Service "Bike Theft" records (Open Government Licence - Ontario)
and the City of Toronto Open Data bicycle parking datasets.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

PARKING_KINDS = ("ring", "rack", "large", "station")
# Only spots that exist: the street-ring dataset also lists temporarily removed rings and
# the rack dataset lists approved or proposed ones. Datasets with no status are all existing.
ACTIVE_STATUSES = {"existing", "installed"}
CAPACITY_FIELDS = ("CAPACITY", "BICYCLE_CAPACITY", "BIKE_CAPACITY")
REGION = (43.4, 44.0, -80.0, -78.8)      # south, north, west, east: greater Toronto, excludes (0, 0)
BOUNDS_MARGIN = 0.006                    # about 650 m, so parking just beyond the graph edge still counts


def _in_region(lat, lng):
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lng)):
        return False
    south, north, west, east = REGION
    return south < lat < north and west < lng < east


def normalize_thefts(records):
    """[[lat, lng, year, premises], ...] from police records. Drops records without a usable
    location and never keeps the police event id."""
    points = []
    for record in records:
        lat, lng = record.get("LAT_WGS84"), record.get("LONG_WGS84")
        if not _in_region(lat, lng):
            continue
        occurred = record.get("OCC_DATE_AGOL")
        year = (datetime.fromtimestamp(occurred / 1000, timezone.utc).year
                if isinstance(occurred, (int, float)) else None)
        premises = str(record.get("PREMISES_TYPE") or "").strip() or "Unknown"
        points.append([round(lat, 5), round(lng, 5), year, premises])
    return points


def _first_point(geometry):
    """(lat, lng) of a GeoJSON Point or MultiPoint, or None."""
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "MultiPoint" and coordinates:
        coordinates = coordinates[0]
    elif geometry.get("type") != "Point":
        return None
    try:
        lng, lat = float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError, IndexError):
        return None
    return (lat, lng) if _in_region(lat, lng) else None


def _capacity(properties):
    for field in CAPACITY_FIELDS:
        try:
            value = int(float(properties.get(field)))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def normalize_parking(datasets, bounds):
    """[[lat, lng, kind, capacity], ...] for existing spots inside `bounds` (plus a margin).

    `datasets` maps a kind in PARKING_KINDS to a GeoJSON FeatureCollection; `bounds` is
    [[south, west], [north, east]].
    """
    (south, west), (north, east) = bounds
    spots = []
    for kind in PARKING_KINDS:
        for feature in (datasets.get(kind) or {}).get("features") or []:
            properties = feature.get("properties") or {}
            status = str(properties.get("STATUS") or "").strip().lower()
            if status and status not in ACTIVE_STATUSES:
                continue
            point = _first_point(feature.get("geometry"))
            if not point:
                continue
            lat, lng = point
            if not (south - BOUNDS_MARGIN <= lat <= north + BOUNDS_MARGIN
                    and west - BOUNDS_MARGIN <= lng <= east + BOUNDS_MARGIN):
                continue
            spots.append([round(lat, 5), round(lng, 5), kind, _capacity(properties)])
    return spots


def load_parking(path):
    """Parking spots as dicts for the router, or [] if the file is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [{"lat": lat, "lng": lng, "kind": kind, "capacity": capacity}
                for lat, lng, kind, capacity in data["spots"]]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("The bike parking file is unreadable; routes will not include parking.")
        return []
