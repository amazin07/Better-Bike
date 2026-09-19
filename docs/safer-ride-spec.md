# Build Spec — Cyclist Safety Router (Toronto)

A spec for an autonomous coding agent. Build in the order given. Ship a working
vertical slice before adding anything marked OPTIONAL.

---

## 1. What this is

A bike route planner that routes cyclists along the streets where people have
actually been seriously hurt the least — not the fastest streets, and not the
streets that merely *look* calm.

The product goal is mode shift: make riding feel safe enough that people choose
a bike over a car for short urban trips. Every avoided car trip is avoided
tailpipe carbon. This maps to UN SDG 11 (Sustainable Cities and Communities)
and SDG 3 (Good Health and Well-Being), with a secondary tie to SDG 13
(Climate Action).

Mental model: Waze, but the thing being optimized is injury exposure instead of
travel time.

---

## 2. Positioning (this drives technical choices — do not skip)

The closest existing product is **SafeCycle Toronto**, a shipped iOS app that
routes on **Level of Traffic Stress (LTS)** — a four-tier framework classifying
streets by traffic volume, speed, lane count, and physical separation.

LTS is a **design proxy**: it infers danger from what a street looks like.

This product uses **outcome data**: where cyclists were actually killed or
seriously injured. That difference produces three capabilities LTS-based routing
structurally cannot have, and the build must preserve all three:

1. **Time-awareness.** An LTS score is static forever. Collision records carry
   date, time, lighting and visibility, so risk here is a function of when you
   ride. A corridor that is fine at 8am can be bad at 10pm.
2. **Intersection-level risk.** LTS colors street segments. Cyclist injuries
   concentrate at intersections. Risk is therefore attached to graph **nodes**,
   not edges.
3. **Empirical rather than inferred.** Every number shown to a user traces back
   to a real reported injury.

---

## 3. Data sources

### Primary — Toronto KSI
City of Toronto Open Data, *Motor Vehicle Collisions Involving Killed or
Seriously Injured Persons* (2006–present). Download the CSV to disk at build
time. Do not call the API at request time.

Critical parsing rules:

- **One row = one person involved, not one collision.** Deduplicate on `ACCNUM`
  before counting anything. Failing to do this inflates every number in the UI.
- Filter to cyclist involvement using the `CYCLIST` flag (`Yes` / null).
- `ACCLASS` distinguishes `Fatal` from `Non-Fatal Injury`. Weight fatal records
  higher in the cost function.
- Fields needed: `ACCNUM`, `LATITUDE`, `LONGITUDE`, `DATE`, `TIME`, `ACCLASS`,
  `CYCLIST`, `INJURY`, `IMPACTYPE`, `LIGHT`, `VISIBILITY`, `ROAD_CLASS`.
- **Locations of criminal-case collisions are offset to the nearest
  intersection node for privacy.** Do not buffer around edge geometry — risk
  smears onto whichever segment happens to be nearest. Snap to graph nodes.

### Street graph
OSMnx, bounded — not the whole city:

```python
G = ox.graph_from_point((43.6629, -79.3957), dist=4000, network_type="bike")
```

4km around the University of Toronto covers every demo route. A full-city graph
is slow to download and slow to query.

### Bike infrastructure
Toronto Open Data cycling network dataset. Used as a cost multiplier, not as the
primary signal.

### OPTIONAL — New York
NYC Open Data *Motor Vehicle Collisions – Crashes*, dataset `h9gi-nx95`
(~2.27M rows, Socrata). Only build this after Toronto works end to end.

Differences from Toronto:
- Each row is one **crash event**, not one person — no dedup needed. Filter on
  `number_of_cyclist_injured > 0 OR number_of_cyclist_killed > 0`.
- Reporting threshold is looser (any injury, death, or ≥$1,000 damage), so NYC
  includes minor injuries and raw counts are **not comparable** to Toronto's
  killed-or-seriously-injured standard. Never show the two cities' counts side
  by side without labelling the different severity definitions.
- Coordinates are true, not intersection-offset — segment-level risk is valid.
- Query server-side rather than downloading:

```
https://data.cityofnewyork.us/resource/h9gi-nx95.json
  ?$where=number_of_cyclist_injured > 0 AND crash_date > '2022-01-01'
  &$limit=50000
```

---

## 4. Risk model

Build a NetworkX graph from OSMnx. Attach risk to nodes.

```
node_risk(n, hour) = Σ over cyclist KSI records within 30m of node n:
                       severity_weight × time_weight(record, hour)

severity_weight = 3.0 if ACCLASS == "Fatal" else 1.0
time_weight     = 2.0 if record hour is within ±2h of query hour else 1.0

lane_multiplier(edge) = 0.55 if protected lane
                        0.80 if painted lane
                        1.00 otherwise

edge_cost(u, v, α, hour) =
    length_m × lane_multiplier(u,v) × (1 + α × normalized_node_risk(v, hour))
```

Normalize node risk to 0–1 across the graph so `α` behaves predictably.

Route twice on the same graph:
- **Direct route** — `α = 0`, pure distance. This is the baseline. No external
  routing API needed.
- **Safer route** — `α` from the rider level.

Return injury counts along each route by summing distinct `ACCNUM` values
attached to nodes the path traverses.

---

## 5. Rider levels

The user never sees a number called alpha. They pick how they ride, and that
sets α. Longer, calmer routes for less confident riders is the entire point.

| Level | Label | Sub-label | α |
|---|---|---|---|
| 1 | New to city riding | Quiet streets, even if it takes longer | 0.9 |
| 2 | Comfortable | Balance calm streets against time | 0.5 |
| 3 | Confident commuter | Fastest route that isn't dangerous | 0.2 |

Default to level 2. Changing level re-routes immediately — no submit button.

---

## 6. Screens

### 6.1 Map (primary screen — build this first)

Full-viewport Leaflet map. Single `index.html`, no build step, no framework.
Leaflet 1.9.x from CDN.

**Layout**

```
┌──────────────────────────────────────────────────┐
│ ┌────────────────┐              ┌──────────────┐ │
│ │ From ▸ To      │              │ COMPARISON   │ │
│ │ Rider level    │              │ panel        │ │
│ │ Time of day    │              │              │ │
│ └────────────────┘              └──────────────┘ │
│                                                  │
│                  [ MAP ]                         │
│                                                  │
│ ┌──────────┐                                     │
│ │ legend   │                                     │
│ └──────────┘                                     │
└──────────────────────────────────────────────────┘
```

**Controls panel, top left**
- From / To. Click-to-drop pins on the map is the primary interaction; a text
  geocode field is secondary. Two clicks should produce a route.
- Rider level: three segmented buttons, not a dropdown.
- Time of day: slider, 0–23, labelled with the actual hour ("8am", "10pm").
  This control is the product's signature — give it visual weight.

**Comparison panel, top right** — the headline output:

```
Direct route        4.2 km · 18 min
                    47 serious injuries on this path

Your route          4.9 km · 21 min
                    6 serious injuries on this path

+3 minutes · 87% less exposure
```

The injury counts are the largest type on the screen. Everything else is
secondary. Use tabular numerals so figures don't jitter when re-routing.

**Map layers, drawn bottom to top**
1. Basemap
2. Collision points — circle markers, radius 4px serious / 7px fatal, fill
   `#C1272D` at 0.18 opacity, no stroke. They should read as a stain, not as
   pins. Only render points within the current viewport.
3. Direct route — `#8A9299`, 4px, dashed `6 6`, opacity 0.7
4. Safer route — `#14467D`, 6px, solid, round caps, drawn last so it sits on top
5. Origin/destination markers — small filled circles with white ring, not the
   default Leaflet teardrop pin

**Interactions**
- Changing rider level or hour re-fetches and redraws. Animate the safer-route
  polyline redraw over ~250ms so the change is visible. This is the demo moment.
- Hovering a collision point shows year and severity only. No other detail.
- Map click 1 sets origin, click 2 sets destination, click 3 resets to a new
  origin.

### 6.2 Rentals (OPTIONAL — build only after the map works)

A page listing bike rental options with a paid tier.

- Grid of rental cards: bike type, hourly and daily rate, availability.
- Checkout via **Stripe Checkout in test mode** — hosted redirect, not a custom
  card form. Do not build a real payment integration. Test card `4242 4242 4242
  4242`, any future expiry, any CVC.
- Paid tier framing: rentals include routing tuned to the rider's level and
  saved routes. Keep the copy honest about what actually exists.
- Success redirect returns to the map with a confirmation state.

This screen exists to show a revenue model. It must not be able to break the
map. If Stripe fails to load, the map still works.

---

## 7. API contract

Backend is Flask. One meaningful endpoint.

`POST /api/route`

```json
{
  "origin":      [43.6629, -79.3957],
  "destination": [43.6532, -79.3832],
  "level":       2,
  "hour":        8,
  "city":        "toronto"
}
```

Response:

```json
{
  "direct": {
    "geometry":   { "type": "LineString", "coordinates": [[-79.3957, 43.6629]] },
    "distance_m": 4200,
    "duration_s": 1080,
    "ksi_total":  47,
    "ksi_fatal":  3
  },
  "safer": {
    "geometry":   { "type": "LineString", "coordinates": [[-79.3957, 43.6629]] },
    "distance_m": 4900,
    "duration_s": 1260,
    "ksi_total":  6,
    "ksi_fatal":  0
  },
  "risk_points": [
    { "lat": 43.6601, "lng": -79.3901, "severity": "fatal", "year": 2019 }
  ]
}
```

Coordinates in `geometry` are GeoJSON order `[lng, lat]`. Coordinates elsewhere
are `[lat, lng]`. Be careful — this is the most common bug in this kind of app.

`GET /api/health` returns graph node count and collision record count. Useful
for confirming data actually loaded before you demo.

Duration assumes 14 km/h average. Do not model elevation.

---

## 8. Visual direction

Deliberately **not** a dark-mode map. Two reasons: this is a civic safety tool
and should read closer to a transit map or a planning document than to a gaming
dashboard, and it will be projected in a bright auditorium where dark basemaps
wash out.

- **Basemap**: CARTO Positron. Desaturated, low-contrast, lets the data carry
  all the color.
- **Palette**: paper `#F7F7F5`, ink `#1A1D21`, secondary text `#6B7178`,
  route blue `#14467D`, collision red `#C1272D`, panel white `#FFFFFF` with a
  1px `#E3E3DF` border and no drop shadow.
- **Type**: IBM Plex Sans throughout, via Google Fonts. Weight 600 for the big
  numbers, 400 for everything else. Enable `font-variant-numeric: tabular-nums`
  on all figures. No monospace. No all-caps labels.
- **Panels**: square corners or 2px radius. Flat. The map has enough visual
  complexity; the chrome should recede.
- Exactly one element is allowed to be loud: the injury-count comparison.
  Everything else is quiet.

**Copy rules**: sentence case, active voice, no exclamation marks. Say "6
serious injuries on this path", not "Safety score: 94!". Never imply a route is
safe — say it has less recorded injury history. The distinction matters both
ethically and legally.

---

## 9. States

- **Empty**: map centered on downtown Toronto, collision layer visible, panel
  reads "Click the map to set a starting point."
- **Loading**: skeleton in the comparison panel. Do not block the map.
- **No route found**: "No cycling route between those points. Try moving one
  closer to a street."
- **Backend down**: "Routing is unavailable. The collision map is still shown
  below." Never a blank screen.

---

## 10. Build order

1. Load and clean KSI CSV, dedup on `ACCNUM`, filter cyclists. Print the count.
2. Build OSMnx graph for the 4km box. Cache it to disk as a pickle.
3. Snap collisions to nodes, compute node risk.
4. Flask `/api/route` returning both paths as GeoJSON.
5. `index.html` with Leaflet, drawing both routes. **Ship this.**
6. Comparison panel with real numbers.
7. Rider level buttons.
8. Time-of-day slider.
9. OPTIONAL: collision point layer.
10. OPTIONAL: rentals + Stripe test checkout.
11. OPTIONAL: New York.

Steps 1–5 are the product. Everything after is upside.

---

## 11. Non-goals

No user accounts. No database. No turn-by-turn navigation. No live traffic. No
mobile app. No elevation modelling. No real payment processing. No dark mode.

---

## 12. Known limitations (state these, don't hide them)

- **Reporting lag**: serious-injury records take 2–3 months to be verified and
  reported, so recent months appear artificially quiet.
- **2014 reporting change**: Toronto Police tightened collision reporting
  criteria in 2014, likely reducing reported collisions. Pre- and post-2014
  counts are not directly comparable.
- **No exposure denominator**: raw injury counts have no cyclist-volume
  baseline, so a street nobody rides scores as safe. The fix is normalizing by
  cycling volume; it is not built.
- **Location offset**: intersection-snapped coordinates mean mid-block risk is
  under-represented.

Surface these in a short "Limitations" section in the README. Do not put them in
the UI.
