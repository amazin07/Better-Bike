# BikeBetter — shared agent instructions

Read this file and `docs/bikebetter-spec.md` before changing the project. This is
the shared handoff for every teammate and coding agent working in this repo.

## Product and scope

Build a **website** for Toronto cyclists: a full-viewport Leaflet map backed by
Flask and a locally cached OSMnx/NetworkX bike graph. Compare the shortest route
with the route chosen for the rider's style (Beginner, Intermediate, or Confident)
using recorded cyclist collision history and the current Toronto hour. The user
explicitly confirmed the browser website format.

Toronto routing is implemented. The user subsequently authorized Firebase
Google sign-in, private bike registration, optional rental listings, and rental
requests. This supersedes the original spec's no-accounts/database scope.
The user also authorized Stripe Connect in test mode, with 0% platform commission.
No New York, turn-by-turn navigation, or real payments.
Live HERE traffic exists only as a display-only, opt-in map overlay
(`traffic.py`, see README); it never feeds routing, and only while the switch is
on does it feed the traffic term of the safety score.
Never fabricate collision statistics.

## Decisions and data pitfalls

- Source spec: `docs/bikebetter-spec.md`. Follow its light civic design, palette,
  IBM Plex Sans, quiet flat panels, and prominent comparison counts.
- The current Toronto CSV has **changed schema**: `collision_id` replaces
  `ACCNUM`; `accdate` contains date and time; `cyclist` uses `true`/`false`;
  fatal severity is `Fatal Injury` (legacy: `Fatal`).
  Support these and the legacy uppercase fields / Yes flags. Group involved
  persons by collision ID, keep a group if any row flags cyclist involvement,
  and preserve fatal severity if any row reports it.
- A deduplicated collision is not a count of injured people. Label UI totals
  as recorded cyclist KSI collisions, explaining that KSI means killed or
  seriously injured. Do not claim a predicted probability of injury.
- The UI shows a 0-100 **safety score** per route (`scoring.py`), an explicit
  exception the user approved to the old "do not claim safety" rule. It is a
  heuristic index for comparing routes, not a prediction: keep the on-screen note
  saying so, never describe it as a probability or guarantee, and remember that no
  recorded collisions is not proof of safety. Traffic joins the score only while
  the Live traffic switch is on and fresh HERE data is cached; a route request must
  never call HERE. Constants and rationale live in `scoring.py` and the README.
- **One fixed navbar on every page** (`static/navbar.css`): BikeBetter (Toronto) on
  the left, Map / Rentals in the centre, Sign in with Google / Sign out on the right.
  The markup is copied into `index.html` and `rentals.html`; keep the copies identical
  (`tests/test_navbar.py` enforces it) and change only the `aria-current` link. Pages
  leave room for the bar with `--nav-h` (64px, 56px on phones). The map page wires the
  sign-in button in `static/nav-auth.js`; the Rentals page wires the same button ids in
  `rentals.js`. Keep the button wording ("Sign in with Google", "Sign out",
  "Unavailable") the same in both.
- **Bike thefts and the yellow parking leg.** The map plots Toronto Police bike
  thefts (`static/bike-thefts.json`, coordinates + year + premises only: never
  publish the event id) and every route response carries `parking`: the nearest bike
  parking by riding distance from the destination (`routing.py`, `_parking`),
  drawn yellow under the blue route. A spot within 30 m of the destination pin
  means no separate path. Parking must never change the blue routes, their scores,
  or the riding-style selection (`tests/test_parking.py` enforces it). Keep parking
  layers out of `state.routes`. The data files come from
  `scripts/build_bike_data.py`; keep the Open Government Licence – Ontario credit
  and the "not a theft rate" caveat visible.
- There is **no time-of-day control and no forecasting**. The website always sends
  the current Toronto hour, read fresh on every request; the API `hour` field and
  the model's hour weighting remain.
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
- **Route choice follows the rider's style; distance and speed come first.** The
  user superseded the earlier lane-priority tuning. Confident (`level` 3) always
  gets the fastest route, whatever its safety score. Beginner (1) and Intermediate
  (2) get the shortest route whose safety score is strictly above 90 or 70, which
  is the fastest route itself when it already qualifies. If nothing qualifies, show
  the highest-scoring route found and say so in the UI. Identical Confident,
  Beginner, and direct routes are expected and fine; never trade distance for
  safety for a Confident rider. The same score the cards show (including live
  traffic while that switch is on) decides qualification. Candidates come from
  `length + alpha * 600m * normalized_node_risk` with **no bike-lane discount**,
  normalized against a single maximum across all nodes and 24 hours. Detours are
  not capped. Bike-lane multipliers are still computed at data build but no longer
  affect route choice or the score. The direct route stays pure distance.
- `scripts/evaluate_routing.py` compares the original and the previous
  lane-weighted heuristics on all 132 directed landmark pairs;
  `docs/routing-evaluation.json` records that historical snapshot. It does not
  evaluate the current selection rules. It checks behavior, not real-world safety.
- CARTO now requires a basemap API key. `CARTO_BASEMAP_KEY` enables Positron;
  without it the website uses a muted OpenStreetMap fallback. Never commit keys.
- Keep README limitations visible to developers. Current City guidance says
  serious-injury reporting can lag **six months or more**, updating the spec's
  older 2–3 month estimate.

## Intended layout and commands

`prepare_data.py`: download, clean, cache graph, match infrastructure, attach risk.
`routing.py`: parsing and routing model. `app.py`: Flask API and website.
`scoring.py`: safety score formula and constants (see README).
`traffic.py`: opt-in HERE live-traffic overlay cache; memory-only and call-budget
capped (see README). `.env.example`: template for the git-ignored local `.env`.
`index.html`: single-file, no-build Leaflet frontend. `tests/`: model/API tests.
`data/`: ignored source/cache artifacts. `static/`: shareable map data artifacts.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
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

## Firebase and bike rentals

- `rentals.html` and `static/rentals.js` are the no-build account/rental UI.
  `static/firebase-client.js` connects the Firebase web SDK;
  `static/bike-store.js` contains shared transaction and validation logic.
- Project: `blyatbike`. Google sign-in only. A registration is private by
  default; publishing a rental listing is a separate, unchecked option.
  Live Auth and the default Firestore database (Toronto / northamerica-northeast2)
  are configured, with rules and indexes deployed as of 2026-09-19.
- Public-safe fields live in `bikes/{id}` (readable publicly only if published).
  Serial numbers and private notes live in `bikes/{id}/private/details` with
  owner-only rules. Never put private data in the public document: Firestore
  rules cannot hide individual fields from an otherwise readable document.
- Serial numbers are optional; store an empty string when omitted. One optional
  bike photo lives in `bikes/{id}/photos/main`, readable by its owner or when the
  bike is published. `static/bike-photo.js` compresses JPEG/PNG/WebP uploads
  (15 MB input maximum) to a JPEG data URL capped at 220,000 characters, stripping
  original metadata. Keep photo data out of listing documents and Firestore
  indexes. Save/replace/remove photos atomically with bike edits. The current
  project has no billing enabled, so this bounded photo uses existing Firestore;
  full-resolution galleries would need a separate storage design.
- `rentalRequests/{id}` is readable only by the verified owner and renter.
  Request creation shares the renter's Google email; acceptance shares the
  owner's email. UI must disclose this before each action.
- Acceptance atomically reserves one bike for one request. Completion releases
  it. Do not replace these transactions with independent writes. This is a
  simple one-active-rental workflow, not a date-based booking calendar.
- `firestore.rules` enforces ownership, field types, money in integer cents,
  price snapshots, valid state transitions, and private/public separation.
  Test data changes with `npm ci && npm run test:firebase` (Node 22+, Java 21,
  Firebase CLI). Tests use only `demo-bikebetter` emulators, never live data.
- `FIREBASE_API_KEY` is read from the ignored `.env` by the dev server. It is
  browser configuration, not an admin credential. HERE_API_KEY stays server-only.
  Never commit actual key values or add service-account credentials to the UI.
- `firebase.json` manages Google auth, rules, and indexes; no Hosting deploy is
  configured because the website requires its Flask routing backend. See
  `docs/firebase-setup.md` for service status, setup, and browser testing.

## Current handoff

The Toronto website, local data builder, Flask API, real collision layer, rider
controls, ETA-first route cards with a safety score, local place search, and
regression tests are implemented.
The verified local build has 12,294 nodes, 29,231 edges, and 442 cyclist KSI
collisions in coverage (370 within 30 metres of nodes). Sources span 2006–2026.
See README for setup and model limitations; the public map JSON is committed,
but each teammate must run `prepare_data.py` for their local graph cache.

The opt-in live HERE traffic overlay is implemented (`traffic.py`, `/api/traffic`,
the "Live traffic" switch, `tests/test_traffic.py`). It never feeds routing; it
feeds the safety score only while the switch is on (`tests/test_scoring.py`).
Protect the shared HERE quota: use `HERE_FIXTURE_DIR` for UI work,
keep the real key on one demo instance, and never commit `.env` or HERE data.
`HereClient` adds `certifi` roots to the default SSL context so macOS Python
installs without system roots work; never disable certificate/hostname verification.
Unresolved: the real Traffic free allowance (default budget 2500 is a placeholder)
and whether HERE's terms allow the shared server cache. See the README limits.

## Stripe Connect (test only)

- See `docs/stripe-connect-plan.md` for the official MCP planner result, setup,
  webhook events, responsibilities, and limitations. Test mode only; reject live keys.
- `payments.py` controls CAD daily-inclusive Checkout totals and payment state;
  `stripe_connect.py` owns Accounts v2 onboarding/readiness. Owner destination and
  price come only from server records; never accept them from browser input.
- `firebase_server.py` verifies Firebase tokens and accesses Firestore with Admin
  credentials. ADC in deployment; explicit Firebase CLI credential opt-in locally.
- Server-only writes: `rentalPayments/{requestId}`, `stripeAccounts/{ownerUid}`.
  Payment attempts use immutable Stripe idempotency keys; paid is monotonic.
  Do not release bikes while checkout is creating/open/processing.
- Use hosted onboarding + Express; v2 recipient capability, destination charges,
  `application_fee_amount=0`. The sandbox platform pays processing fees. No real
  payouts, tax calculation, deposits, refunds/dispute UI, or escrow functionality.
- Signed Checkout webhooks and Accounts v2 thin webhooks are required. Run
  `.venv/bin/python scripts/stripe-listen.py` before starting local Flask; this
  saves signing secrets to ignored `.env` without printing them.
- `npm ci` installs the official Connect.js loader; Flask serves only that module,
  and the actual embedded component code is always loaded from Stripe.
- Never commit API/signing keys, account-session secrets, or onboarding URLs.
  Prefer restricted keys and a hosted secret store for deployment.

## Fictional presentation data

- The user authorized demo rental listings and missing-bike reports in Firestore.
  See `docs/demo-data.md` and `scripts/seed_demo_data.py` for exact records and cleanup.
- `bikebetter-demo-owner-v1` is a synthetic owner, not an Auth or Stripe account.
  Demo IDs start with `demo-`; retain explicit demo labels and do not present sample
  rewards as real offers. `isDemoRecord` in `static/bike-store.js` identifies them.
- Sample listings are not bookable; real listings are unaffected. Keep all collision
  statistics and the police theft-map dataset authentic.

<!-- stripe-projects-cli managed:agents-md:start -->
## Stripe Projects CLI

This repository is initialized for the Stripe project "Future-Legends-UofT-2026".

## Tools used

- [Stripe CLI](https://docs.stripe.com/stripe-cli) with the `projects` plugin to manage third-party services, credentials, and deployments for this project. Use the stripe-projects-cli to manage deploying and access to third party services.
<!-- stripe-projects-cli managed:agents-md:end -->

## Vercel hosting

- Live site: `https://bikebetter.vercel.app`, free Hobby plan. CLI deployments;
  GitHub push does not yet trigger a build. Firebase and sandbox Stripe secrets
  are configured in Vercel, never in the repository or frontend.
- See `docs/vercel-deployment.md`. The Flask entrypoint loads a committed, minimal
  JSON routing snapshot in Vercel; trusted pickle and raw data remain local.
- Regenerate `routing-data/toronto.json.gz` using `scripts/export_routing_bundle.py`
  after data rebuilds. Preserve parallel edges, geometry, and deduplicated risk.
- Runtime dependencies are in `requirements.txt`; local data/test dependencies
  are in `requirements-dev.txt`. Hosted secrets belong in environment variables.
- HERE traffic requires a shared quota strategy before enabling it on Vercel.
  Never reuse a per-instance or temporary counter as a global spending limit.
