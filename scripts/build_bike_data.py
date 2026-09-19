"""Download bicycle thefts and bike parking and write the small published files.

Run by hand when you want fresh data (the police refresh thefts every few hours, the City
refreshes street parking daily):

    .venv/bin/python scripts/build_bike_data.py

Writes static/bike-thefts.json and static/bike-parking.json. The theft file keeps only
coordinates, year, and premises type: never the police event id. Sources and licences are
in each file's metadata. This is the only place these datasets are downloaded.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bikedata import PARKING_KINDS, normalize_parking, normalize_thefts  # noqa: E402
from traffic import DEFAULT_BOUNDS  # noqa: E402

# "TPS Crime App YTD" web map, layer "All Crimes YTD"; bicycle thefts are CRIME_TYPE = 'Bike Theft'.
TPS_LAYER = "https://services.arcgis.com/S9th0jAJ7bqgIRjw/arcgis/rest/services/YTD_CRIME_WM/FeatureServer/0"
CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id="
PARKING_PACKAGES = {
    "ring": "street-furniture-bicycle-parking",
    "rack": "bicycle-parking-racks",
    "large": "bicycle-parking-high-capacity-outdoor",
    "station": "bicycle-parking-bike-stations-indoor",
}
HEADERS = {"User-Agent": "BikeBetter-data-build (hackathon project)"}


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as response:
        return json.load(response)


def download_thefts():
    records, offset = [], 0
    while True:
        query = urllib.parse.urlencode({
            "where": "CRIME_TYPE='Bike Theft'", "returnGeometry": "false", "f": "json",
            "outFields": "OCC_DATE_AGOL,PREMISES_TYPE,LAT_WGS84,LONG_WGS84",
            "resultOffset": offset, "resultRecordCount": 2000})
        page = get_json(f"{TPS_LAYER}/query?{query}")
        records += [feature["attributes"] for feature in page.get("features", [])]
        if not page.get("exceededTransferLimit"):
            return records
        offset += 2000


def download_parking():
    datasets = {}
    for kind in PARKING_KINDS:
        package = get_json(CKAN + PARKING_PACKAGES[kind])["result"]
        resource = next(r for r in package["resources"]
                        if "4326" in r["name"] and r["format"].lower() == "geojson")
        datasets[kind] = get_json(resource["url"])
        print(f"  {kind:8s} {len(datasets[kind]['features']):6d} records ({package['title']})")
    return datasets


def write(path, payload):
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print("Bicycle thefts (Toronto Police Service)")
    raw = download_thefts()
    points = normalize_thefts(raw)
    print(f"  {len(raw)} records, {len(points)} with a usable location")
    write(ROOT / "static" / "bike-thefts.json", {
        "metadata": {
            "source": "Toronto Police Service, TPS Crime App YTD (All Crimes YTD, Bike Theft)",
            "licence": "Open Government Licence - Ontario",
            "attribution": "Contains information licensed under the Open Government Licence - Ontario. "
                           "Source: Toronto Police Service.",
            "retrieved": today, "count": len(points),
            "note": "Thefts reported year to date; occurrence dates go back to 2016. Locations are "
                    "approximate as published, and many records share a point. Fields: lat, lng, "
                    "occurrence year, premises type."},
        "points": points})
    print("Bike parking (City of Toronto Open Data)")
    spots = normalize_parking(download_parking(), DEFAULT_BOUNDS)
    print(f"  {len(spots)} existing spots inside the map area")
    write(ROOT / "static" / "bike-parking.json", {
        "metadata": {
            "source": "City of Toronto Open Data: Street Furniture - Bicycle Parking, Bicycle Parking "
                      "Racks, Bicycle Parking - High Capacity (Outdoor), Bicycle Parking - Bike Stations "
                      "(Indoor)",
            "licence": "City of Toronto Open Data (the portal does not state a licence per dataset)",
            "retrieved": today, "count": len(spots),
            "note": "Existing spots only. Kinds: ring, rack, large (corral or shelter), station (indoor). "
                    "Fields: lat, lng, kind, capacity."},
        "spots": spots})


if __name__ == "__main__":
    main()
