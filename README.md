# Safer Ride · Toronto

A browser website that compares a shortest-distance bike route with a route
weighted by recorded cyclist collision history, bike infrastructure, rider
confidence, and departure hour. Built for Future Legends UofT 2026.

Read [AGENTS.md](AGENTS.md) for the shared teammate/agent handoff and
[the original specification](docs/safer-ride-spec.md) for the product brief.

## Run the website

Python 3.11 or newer is recommended. The frontend is one `index.html`: Leaflet
1.9.4 from its CDN, plain JavaScript, and CSS. There is no frontend build step.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python prepare_data.py
.venv/bin/python app.py
```

Open **http://127.0.0.1:5001**. Click two map locations, or search a local landmark
or street intersection. Change the confidence buttons or the hour slider to
recalculate. A third map click starts a new route. The sample ride is a real
calculation, never a canned response.

The first data build requires internet and can take several minutes. It downloads
the official KSI CSV, a 4 km bounding box around U of T from OpenStreetMap, and
Toronto's cycling network. Subsequent builds reuse the local source files and
graph. Route requests and place searches make **no external API calls**.

```sh
.venv/bin/python prepare_data.py --refresh       # Refresh City data, reuse graph
.venv/bin/python prepare_data.py --refresh-graph # Refresh OSM graph
.venv/bin/python -m pytest -q
```

`requirements-lock.txt` records the exact Python versions used for verification;
install that file instead of `requirements.txt` for a pinned environment.
The verified build passes 26 parser/model/API tests and the desktop/mobile browser
smoke checks described below.

Restart Flask after rebuilding data. Use `HOST=0.0.0.0 PORT=5001` before the run
command to make the development server reachable on your local network. The
repository contains a working website, but pushing to GitHub does not deploy
Flask. A public host must install requirements, run the data build, retain the
generated `data/processed/toronto.pkl`, and serve `app:create_app()` with a WSGI
server. Do not use Flask's development server for a public deployment.

## Basemap configuration

CARTO changed its service in September 2026: **Positron now requires a basemap
API key**. Without one, the website uses muted OpenStreetMap tiles so there is
no CARTO watermark. To use the specified Positron basemap, get your own key from
[CARTO](https://carto.com/basemaps/apikey/) and start the server with
`CARTO_BASEMAP_KEY` set in the environment. Never commit your key. This is a
browser-visible key; restrict its allowed website referrers in CARTO's dashboard.

Tiles, Leaflet, and Google Fonts need internet. The browser requests only its
map viewport and keeps normal HTTP caching and visible provider attribution.
No bulk or offline tile downloads. See [OSM's tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Data and API

The verified September 19, 2026 build contains:

| Measure | Count |
| --- | ---: |
| Person rows in Toronto CSV | 20,723 |
| Distinct collisions across the source | 7,598 |
| Distinct cyclist-involved collisions citywide | 923 |
| Cyclist collisions inside graph coverage | 442 |
| Collisions within 30 m of a graph node | 370 |
| Street graph nodes / directed edges | 12,294 / 29,231 |

Source years are 2006–2026, with the latest source collision dated August 29,
2026. Counts will change when sources are refreshed.

- `GET /api/health`: readiness, graph size, collision counts, coverage, build date.
- `POST /api/route`: direct/weighted route geometries, metres, seconds, distinct
  collision counts, fatal subsets, collision points, and snapped coordinates.
- `GET /api/places?q=...`: local landmark/intersection search; not arbitrary
  address geocoding and no third-party geocoder.
- `GET /api/config`: browser basemap configuration.
- `GET /static/collisions.json`: minimal published collision points. These load
  independently of the routing endpoint and remain visible if routing fails.

```sh
curl http://127.0.0.1:5001/api/health
curl http://127.0.0.1:5001/api/route \
  -H 'Content-Type: application/json' \
  -d '{"origin":[43.6629,-79.3957],"destination":[43.6487,-79.3715],"level":2,"hour":8,"city":"toronto"}'
```

Request coordinates are `[latitude, longitude]`; returned GeoJSON coordinates
are `[longitude, latitude]`. Inputs more than 250 m from a graph node are
rejected. Both paths respect the cached directed graph and retain the geometry
of the selected parallel edge. Duration uses 14 km/h. Pins move to the actual
street nodes; the connection from a clicked point to that node is not routed.

### Model and counting

The current CSV uses `collision_id`, `accdate`, `cyclist=true`, and `Fatal Injury`.
The parser also supports the original `ACCNUM`, `DATE`, `TIME`, `CYCLIST=Yes`,
and `Fatal` fields. It groups person rows before filtering, keeps any cyclist
involvement, and preserves fatal severity across the whole collision.

Each deduplicated record contributes to nodes within 30 metres, using metric
coordinates (UTM zone 17N). Unmatched records remain visible on the map but do
not affect route costs. Fatal severity has weight 3, other records weight 1.
Records within two hours of departure get double weight, wrapping around
midnight. Missing times get no boost. Risk is divided by the maximum node risk
at that hour, with zero used for a graph without collisions.

The direct route minimizes distance only. The weighted route uses the spec's
`length × lane_multiplier × (1 + alpha × normalized_node_risk)`; alpha is 0.9,
0.5, or 0.2 by rider level. Toronto cycling-network geometries supply 0.55 for
separated tracks/trails, 0.8 for painted/buffered lanes, and 1 otherwise. Matching
requires at least 65% of the street edge to lie within 12 m of an infrastructure
line, so a simple perpendicular crossing is not enough. The less favorable of
the source's two directional classifications is used conservatively.

**The original weights can favor bike lanes over lower collision counts.** The
weighted route can have more recorded collisions or remain identical across
hours/levels; the app displays those outcomes honestly. U of T → St. Lawrence
Market is one such case in the current snapshot. This is a model limitation,
not a predicted reduction in injury probability. No universal reduction is
claimed, and controls never generate artificial changes for demonstration.

For a demonstration of a real time-dependent change, search **Kensington Market
→ Christie Pits Park** and compare 8am with 10pm. In this snapshot, the 8am
comfortable route has four recorded collisions versus eight on the direct route.
Results are calculated from the cache and may change after a data refresh.

`ksi_total` counts distinct collision IDs across visited nodes, including the
origin; `ksi_fatal` counts the fatal subset. A record near multiple route nodes
still counts once. The UI calls these **recorded cyclist KSI collisions**, not
individual injured people. All-time counts remain all-time when the hour changes.
Percent differences handle increases, ties, and zero baselines without division
by zero.

## Limitations

- **Reporting lag:** recent months appear artificially quiet. The original
  brief says 2–3 months; current [City guidance](https://www.toronto.ca/services-payments/streets-parking-transportation/road-safety/vision-zero/vision-zero-dashboard/seriously-injured-vision-zero/)
  says serious-injury verification can take six months or more.
- **2014 reporting change:** reporting criteria changed; pre/post-2014 counts
  are not directly comparable.
- **No exposure denominator:** counts have no cyclist-volume baseline. An
  untraveled or underreported street can have no records. Zero is not proof of
  safety. The weights are heuristic, not calibrated injury probabilities.
- **Location offset:** privacy-adjusted intersection locations underrepresent
  mid-block risk. A 30 m cutoff also leaves some records unmatched. Nearby nodes
  may share a record; route counts deduplicate it, but costs apply node by node.
- **Infrastructure matching:** spatial overlap is approximate and does not
  resolve every directional lane, parallel street, barrier, or recent removal.
  Infrastructure and graph snapshots can differ in age. No live closures or
  turn restrictions beyond the downloaded graph are modeled.
- **Coverage:** only the bounded downtown graph is supported. A route may leave
  out a better alternative outside the box. Search covers selected landmarks
  and graph intersections rather than every Toronto address.
- **Historical interpretation:** cyclist involvement does not mean every
  injured person in the collision was a cyclist. Counts describe reported KSI
  collision events involving cyclists, not cyclist casualties or present danger.

## Sources and attribution

- [Toronto KSI collision dataset](https://open.toronto.ca/dataset/motor-vehicle-collisions-involving-killed-or-seriously-injured-persons/)
- [Toronto cycling network](https://open.toronto.ca/dataset/cycling-network/)
- [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), via
  [OSMnx](https://osmnx.readthedocs.io/en/stable/)
- [Open Government Licence – Toronto](https://open.toronto.ca/open-data-license/)

Contains information licensed under the Open Government Licence – Toronto.
The public collision JSON contains only published coordinates, year, and severity;
raw person-level data and trusted local pickle caches are excluded from Git.
Only load pickle files created locally by the build script.

## Project layout

```text
AGENTS.md                   Shared teammate and agent instructions
docs/safer-ride-spec.md      Original supplied specification
index.html                 Responsive, no-build website
app.py                     Flask website, validation, and API
routing.py                 CSV parser, node risk, route calculations
prepare_data.py             Repeatable official-data download/cache build
static/collisions.json     Minimal real records for the initial map
tests/test_routing.py       Parser, model, and API regression tests
scripts/browser_smoke.mjs   Desktop/mobile interaction and failure checks
data/                      Ignored raw data and graph cache
```

Optional rentals, Stripe checkout, accounts, New York, and saved routes are not
implemented. The shipped scope is the Toronto routing website.

For the browser smoke check, start Flask, start Chrome with
`--remote-debugging-port=9224 --user-data-dir=/tmp/safer-ride-browser`, and open
`http://127.0.0.1:5001`. With Node 22 or newer, run
`node scripts/browser_smoke.mjs`. It checks clicks, local search, hour/confidence
requests, stale responses, routing failure, and mobile controls. Screenshots go
to the ignored `artifacts/` directory.
