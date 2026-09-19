# Safer Ride — shared agent instructions

Read this file and `docs/safer-ride-spec.md` before changing the project. This is
the shared handoff for every teammate and coding agent working in this repo.

## Product and scope

Build a **website** for Toronto cyclists: a full-viewport Leaflet map backed by
Flask and a locally cached OSMnx/NetworkX bike graph. Compare the shortest route
with a route weighted by recorded cyclist collision history, rider confidence,
and departure hour. The user explicitly confirmed the browser website format.

Ship Toronto end to end first. Rentals/Stripe and New York are optional and are
not part of the current implementation. No accounts, database, live traffic,
turn-by-turn navigation, or real payments. Never fabricate collision statistics.

## Decisions and data pitfalls

- Source spec: `docs/safer-ride-spec.md`. Follow its light civic design, palette,
  IBM Plex Sans, quiet flat panels, and prominent comparison counts.
- The current Toronto CSV has **changed schema**: `collision_id` replaces
  `ACCNUM`; `accdate` contains date and time; `cyclist` uses `true`/`false`.
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
- Keep README limitations visible to developers. Current City guidance says
  serious-injury reporting can lag **six months or more**, updating the spec's
  older 2–3 month estimate.

## Intended layout and commands

`prepare_data.py`: download, clean, cache graph, match infrastructure, attach risk.
`routing.py`: parsing and routing model. `app.py`: Flask API and website.
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

## Initial handoff

The repository started with only a README. The original build is in progress;
the commands above describe the agreed layout and will work when that build is
committed. Official datasets have been located and their current schemas
inspected. This file is being pushed early so other agents share the same plan.
