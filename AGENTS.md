# Safer Ride — shared agent instructions

Read this file and `docs/safer-ride-spec.md` before changing the project. This is
the shared handoff for every teammate and coding agent working in this repo.

## Product and scope

Build a **website** for Toronto cyclists: a full-viewport Leaflet map backed by
Flask and a locally cached OSMnx/NetworkX bike graph. Compare the shortest route
with a route weighted by recorded cyclist collision history, rider confidence,
and departure hour. The user explicitly confirmed the browser website format.

Ship Toronto end to end first. Rentals/Stripe and New York are optional and are
not part of the current implementation. No accounts, database, turn-by-turn
navigation, or real payments. Live HERE traffic exists only as a display-only,
opt-in map overlay (`traffic.py`, see README); it never feeds routing.
Never fabricate collision statistics.

## Decisions and data pitfalls

- Source spec: `docs/safer-ride-spec.md`. Follow its light civic design, palette,
  IBM Plex Sans, quiet flat panels, and prominent comparison counts.
- The current Toronto CSV has **changed schema**: `collision_id` replaces
  `ACCNUM`; `accdate` contains date and time; `cyclist` uses `true`/`false`;
  fatal severity is `Fatal Injury` (legacy: `Fatal`).
  Support these and the legacy uppercase fields / Yes flags. Group involved
  persons by collision ID, keep a group if any row flags cyclist involvement,
  and preserve fatal severity if any row reports it.
- A deduplicated collision is not a count of injured people. Label UI totals
  as recorded cyclist KSI collisions, explaining that KSI means killed or
  seriously injured. Do not claim a predicted probability of injury or safety.
- Direct route is **pure distance**, without lane multipliers. This resolves
  a contradiction between the spec's alpha=0 formula and its stated baseline.
- Risk attaches to graph nodes within 30 metres, never to nearest road edges.
  Route totals use distinct collision IDs, even if several nodes share a record.
- Hour comparisons wrap around midnight. Missing times receive no hour boost.
- GeoJSON uses `[longitude, latitude]`; API input uses `[latitude, longitude]`.
- Download official KSI CSV and Toronto cycling infrastructure only at data
  build time. Keep raw data and the trusted local graph pickle out of Git.
- Preserve actual OSM edge geometry and direction, including parallel edges.
- Do not promise the weighted route always reduces raw counts; show actual
  differences honestly, including ties, increases, and zero baselines.
- The user authorized tuning while keeping bike lanes strongly prioritized.
  Keep the 0.55 protected / 0.80 painted lane distance multipliers. The revised
  cost is `length * lane_multiplier + alpha * 600m * normalized_node_risk`.
  Do not apply lane discounts or approach-edge length to the collision penalty.
  Normalize against a single maximum across all graph nodes and 24 hours, so a
  time boost cannot cancel itself through per-hour normalization. The direct
  baseline remains pure distance. This decision supersedes the original spec.
- `scripts/evaluate_routing.py` compares the original and current heuristics on
  all 132 directed landmark pairs; `docs/routing-evaluation.json` records the
  current snapshot. This checks behavior, not predicted real-world safety.
- CARTO now requires a basemap API key. `CARTO_BASEMAP_KEY` enables Positron;
  without it the website uses a muted OpenStreetMap fallback. Never commit keys.
- Keep README limitations visible to developers. Current City guidance says
  serious-injury reporting can lag **six months or more**, updating the spec's
  older 2–3 month estimate.

## Intended layout and commands

`prepare_data.py`: download, clean, cache graph, match infrastructure, attach risk.
`routing.py`: parsing and routing model. `app.py`: Flask API and website.
`traffic.py`: opt-in HERE live-traffic overlay cache; memory-only and call-budget
capped (see README). `.env.example`: template for the git-ignored local `.env`.
`index.html`: single-file, no-build Leaflet frontend. `tests/`: model/API tests.
`data/`: ignored source/cache artifacts. `static/`: shareable map data artifacts.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python prepare_data.py
.venv/bin/python app.py
# Open http://127.0.0.1:5001
.venv/bin/python -m pytest
```

The setup command downloads real data and needs internet. Normal route requests
must work from disk without external routing/data APIs. Map tiles, font, and
Leaflet CDN assets still need internet in the browser.

## Collaboration

- Read `README.md` for current setup, verification, and limitations.
- Check `git status` before editing; preserve teammates' work.
- Keep this file current when behavior, commands, or responsibilities change.
- Do not commit credentials, `.venv`, raw person-level CSVs, or pickle caches.
- Test meaningful data/model/API changes, especially deduplication, midnight,
  geometry coordinate order, and multigraph edge selection.
- Do not add optional product scope until the Toronto routing website works.

## Current handoff

The Toronto website, local data builder, Flask API, real collision layer, rider
controls, hour slider, local place search, and regression tests are implemented.
The verified local build has 12,294 nodes, 29,231 edges, and 442 cyclist KSI
collisions in coverage (370 within 30 metres of nodes). Sources span 2006–2026.
See README for setup and model limitations; the public map JSON is committed,
but each teammate must run `prepare_data.py` for their local graph cache.

The opt-in live HERE traffic overlay is implemented (`traffic.py`, `/api/traffic`,
the "Live traffic" switch, `tests/test_traffic.py`). It is display-only and never
feeds routing. Protect the shared HERE quota: use `HERE_FIXTURE_DIR` for UI work,
keep the real key on one demo instance, and never commit `.env` or HERE data.
Unresolved: the real Traffic free allowance (default budget 2500 is a placeholder)
and whether HERE's terms allow the shared server cache. See the README limits.
