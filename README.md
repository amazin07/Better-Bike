# BikeBetter · Toronto

A browser website that compares a shortest-distance bike route with a route
weighted by recorded cyclist collision history, bike infrastructure, rider
confidence, and the current Toronto hour, and shows each route's ETA and a safety
score. Built for Future Legends UofT 2026.

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
or street intersection. Change the confidence buttons to recalculate; routes
always use the current Toronto hour (there is no time-of-day control and no
forecasting). A third map click starts a new route. The sample ride is a real
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
The verified build passes 32 parser/model/API tests and the desktop/mobile browser
smoke checks described below.

Restart Flask after rebuilding data. Use `HOST=0.0.0.0 PORT=5001` before the run
command to make the development server reachable on your local network. The
repository contains a working website, but pushing to GitHub does not deploy
Flask. A public host must install requirements, run the data build, retain the
generated `data/processed/toronto.pkl`, and serve `app:create_app()` with a WSGI
server. Do not use Flask's development server for a public deployment.

## Live traffic overlay (optional, HERE)

A **Live traffic** switch on the map draws HERE's current congestion, road
closures, and incidents over the downtown map. Routes, route costs, and the
collision model never use it, and route requests still make no external calls.
While the switch is on, the server's cached snapshot also feeds the traffic part of
the safety score (see Safety score) without calling HERE. The overlay is **off
until someone turns the switch on**.

```sh
cp .env.example .env      # then set HERE_API_KEY; .env is git-ignored
.venv/bin/python app.py   # the dev server loads .env; deployments set real variables
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERE_API_KEY` | none | Server-side HERE key. Never sent to the browser or logged. |
| `HERE_MONTHLY_BUDGET` | 2500 | Hard cap on HERE calls per month. **Placeholder:** set it to your real free allowance (HERE platform → profile icon → Billing & Usage). |
| `HERE_FIXTURE_DIR` | none | Serve saved `here_flow.json` and `here_incidents.json` instead of calling HERE. Costs no budget; for UI work. Overrides the key. |

Without a key or fixture directory the switch reports that live traffic is not
available and no HERE calls are made.

### How it protects the HERE quota

- The browser only calls `GET /api/traffic`. Flask keeps **one in-memory snapshot
  for every viewer**, and browser polling revalidates with an `ETag` (usually a
  `304`), so viewers do not multiply HERE calls.
- Feeds refresh **only when someone is viewing the overlay**: flow every 5 minutes
  and incidents every 10. The intervals double once 50% of the month's budget is
  used and quadruple at 80%. A daily cap (15% of the monthly budget) stops a
  runaway day. Only one refresh runs at a time.
- The counter lives in `data/here_usage.json` (a counter only, never HERE data).
  If that file is unreadable the cache **fails closed** and makes no calls. Failed
  attempts count against the budget, back off, and after 3 consecutive errors
  pause refreshing for 15 minutes. A rejected key (401/403) turns it off until the
  server restarts. When the budget is spent the last snapshot stays visible,
  marked stale.
- HERE data is held **in memory only**: no archive and no copy on disk.
- Use the real key on one demo instance. Teammates should set `HERE_FIXTURE_DIR`
  so UI work costs nothing. Fixture files are HERE data: keep them in the ignored
  `data/` folder and never commit them.

### Limits to keep in mind

- HERE's flow comes from connected-car probes: it describes **car traffic**, not
  cyclists. `jamFactor` measures congestion, not danger, and no HERE incident type
  identifies cyclist crashes. Only flow with `jamFactor` ≥ 2, or closed, is drawn
  (`MIN_JAM_FACTOR` in `traffic.py`).
- HERE's developer terms are reported to restrict caching results and serving one
  response to many users. We have **not verified which terms apply to this
  account**; confirm them before any public launch.
- The overlay shows "Traffic data © HERE" while on. Confirm HERE's exact
  attribution requirements before launch.
- The 2,500 default is a placeholder; the real Traffic allowance is unconfirmed.
- Coverage is the same downtown bounding box as the routing graph.
- **Run one server process.** The cache and its call counter live in one process's
  memory (threads are fine). Several worker processes would each keep their own
  snapshot and count, multiplying HERE calls and undercounting the budget.
- The monthly and daily counters roll over on UTC dates, which may not match HERE's
  billing cycle. Leaving the overlay on around the clock would use roughly 430
  calls a day at the base intervals, so the daily cap and stretched intervals are
  what keep a month within budget.

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
  collision counts, fatal subsets, collision points, snapped coordinates, and a
  `safety` score per route (see Safety score). The optional boolean `traffic` asks
  for live traffic to be included in the score; `traffic_used` reports whether it
  was. Sending `traffic` never calls HERE.
- `GET /api/places?q=...`: local landmark/intersection search; not arbitrary
  address geocoding and no third-party geocoder.
- `GET /api/config`: browser basemap configuration.
- `GET /api/traffic`: opt-in live HERE overlay (compact flow and incident GeoJSON
  plus status and call budget), served from the server's cache and `ETag`
  revalidated. `/api/health` also reports a `traffic` block without calling HERE.
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
midnight. Missing times get no boost. Risk is divided by a **single maximum
across all graph nodes and all 24 hours**, with zero risk used for a graph without
collisions. Using a common scale preserves the hour boost even if a graph has
only one node with recorded collisions.

The direct route minimizes distance only. With the user's approval, the revised
weighted route keeps strong bike-lane preference and uses:

```text
cost = length_m × lane_multiplier + alpha × 600 m × normalized_node_risk
```

Alpha is 0.9, 0.5, or 0.2 by rider level. The 600 m constant is a heuristic distance
tradeoff, not a predicted injury rate: at maximum normalized risk, the extra
cost is 540, 300, or 120 lane-weighted metres respectively. This additive
intersection penalty is independent of approach length and is **not discounted
by a bike lane**. A short approach cannot make an intersection's history nearly
disappear. Lane distance remains discounted even when there is no collision
history, so longer protected/painted alternatives can still beat shorter streets.

Toronto cycling-network geometries supply 0.55 for
separated tracks/trails, 0.8 for painted/buffered lanes, and 1 otherwise. Matching
requires at least 65% of the street edge to lie within 12 m of an infrastructure
line, so a simple perpendicular crossing is not enough. The less favorable of
the source's two directional classifications is used conservatively.

**Lane preference can still outweigh raw collision counts.** The weighted route
can have more recorded collisions or remain identical across hours/levels; the
app displays those outcomes honestly. U of T → St. Lawrence Market is one such
case in the current snapshot. No universal reduction is claimed, and controls
never generate artificial changes for demonstration.

The included example, **Kensington Market → Christie Pits Park**, has one recorded
collision on the comfortable route versus eight on the direct route at 8am in
this snapshot. The API's `hour` field still changes routes (U of T → St. Lawrence
Market gives five vs seven recorded collisions on the suggested paths at 8am vs
10pm; four on the direct route), but the website has no time control and always
sends the current Toronto hour, so it does no forecasting. Route counts are
all-time, not counts of events occurring at that hour. Results may change after a
data refresh.

### Weighting comparison

The 600 m penalty was chosen after comparing 300, 600, 900, 1200, and 1800 m:
higher penalties reduced historical counts further but sacrificed progressively
more mapped bike-lane use. The selected value keeps substantial lane preference
while making collision history and rider choice more influential.

On all 132 directed pairs of the 12 built-in landmarks, comfortable level at 8am:

| Measure | Original formula | Revised formula |
| --- | ---: | ---: |
| Trips with more recorded collisions than shortest route | 70 | 27 |
| Mean share of route distance on mapped lanes/trails | 55.5% | 46.7% |
| Median extra distance versus shortest route | 1.6% | 1.3% |
| Routes changing between 8am and 10pm | 22 | 29 |
| Routes changing between new and confident rider levels | 35 | 98 |

Shortest routes average 28.7% mapped lane/trail coverage. Summing distinct
collision counts per trip gives 1,278 originally and 855 after revision (33.1%
lower). A collision can appear on several trips, so these sums are **not counts
of unique citywide injuries**. This is a descriptive comparison used for tuning,
not independent validation or a prediction of real-world safety. The full
[snapshot and provenance](docs/routing-evaluation.json) can be reproduced with:

```sh
.venv/bin/python scripts/evaluate_routing.py --output docs/routing-evaluation.json
```

The original specification is kept unchanged for reference; the approved tuning
above and the shared `AGENTS.md` describe the current model.

`ksi_total` counts distinct collision IDs across visited nodes, including the
origin; `ksi_fatal` counts the fatal subset. A record near multiple route nodes
still counts once. The UI calls these **recorded cyclist KSI collisions**, not
individual injured people. All-time counts remain all-time when the hour changes.
Percent differences handle increases, ties, and zero baselines without division
by zero.

### Safety score

Each route card shows the ETA as its largest text, then a **safety score out of
100** (`scoring.py`) and the recorded-collision count:

```text
score = 100 − collision penalty − traffic penalty, clamped to 0–100
```

- **Collision penalty:** distinct recorded cyclist KSI collisions on the path, a
  fatal collision counting 3× (the routing model's weights), divided by the
  route's length in km (at least 1 km, so a very short trip is not blown up),
  times 10, capped at 70. The scale was chosen on all 132 landmark-to-landmark
  routes so scores spread out: medians of about 73 for the direct route and 82
  for the suggested one, ranging from about 30 to 92.
- **Traffic penalty:** only when the **Live traffic** switch is on and the server
  already holds fresh HERE data. The share of the route that runs along roads HERE
  reports as congested (`jamFactor` ≥ 4, matched within 15 m, ignoring crossings
  shorter than 40 m) times 30. Closed roads are ignored: a closure to cars says
  nothing about a bike. A route request never calls HERE; without traffic data the
  score uses collisions only, and the results panel says which it used.
- The score **compares routes; it is not a prediction.** It inherits every limit
  below (KSI only, no exposure denominator, reporting lag), and congested car
  traffic is not the same as danger to a cyclist: the suggested route often follows
  busier bike-lane streets and can lose more traffic points than the direct one.
  Hover a score to see the points lost to each input.
- There is no time control and no forecasting. The website requests the current
  **Toronto** hour, read fresh on every route request.

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
scoring.py                 Safety score (collisions + optional live traffic)
traffic.py                 Opt-in HERE live-traffic cache (budget-capped, in-memory)
.env.example               Template for the git-ignored local .env
prepare_data.py             Repeatable official-data download/cache build
static/collisions.json     Minimal real records for the initial map
tests/test_routing.py       Parser, model, and API regression tests
tests/test_traffic.py       Traffic cache, HERE client, and overlay API tests (no network)
tests/test_scoring.py       Score formula, congestion matching, and score API tests
scripts/browser_smoke.mjs   Desktop/mobile interaction and failure checks
scripts/evaluate_routing.py Original/current real-route comparison
docs/routing-evaluation.json Recorded comparison with source provenance
data/                      Ignored raw data and graph cache
```

## Bike registration and rentals

Open **http://127.0.0.1:5001/rentals** for Google sign-in, private bike
registration, optional rental listings, and rental requests. Serial numbers and
owner notes stay private. Owners accept or decline requests and mark bikes
returned. Payments and pickup are arranged directly; Stripe and saved routes
are not implemented.

See [Firebase setup and teammate handoff](docs/firebase-setup.md) for the current
cloud provisioning status, data permissions, setup commands, and emulator tests.
Copy the Firebase web key into the ignored `.env` as `FIREBASE_API_KEY` before
starting Flask. The map remains independent of Firebase.

```sh
npm ci
npm run test:firebase
```

Firebase tests require Java 21 and Firebase CLI and use a separate local demo
project. No test data is written to the live database.

For the browser smoke check, start Flask, start Chrome with
`--remote-debugging-port=9224 --user-data-dir=/tmp/safer-ride-browser`, and open
`http://127.0.0.1:5001`. With Node 22 or newer, run
`node scripts/browser_smoke.mjs`. It checks clicks, local search, the ETA-first
cards and scores, confidence requests, stale responses, routing failure, and mobile
controls. Screenshots go
to the ignored `artifacts/` directory.
