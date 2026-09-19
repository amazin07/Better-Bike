# Demo rental and missing-bike data

The user requested fictional content for the local presentation. The shared
`blyatbike` Firestore database now contains six public sample rental listings and
three public sample missing-bike reports (with three private backing bike records).
All sample titles/descriptions identify them as demo content. Rental rates and
rewards are examples, not offers. No collision or theft-map statistics are fabricated.

The records have deterministic `demo-rental-*`, `demo-missing-*`, and
`demo-report-*` IDs, owned by the synthetic UID `bikebetter-demo-owner-v1`.
No Firebase login or Stripe account was created for that UID. Report contact
addresses use the non-deliverable `example.invalid` domain. The UI labels demo
reports and disables booking on demo listings. The shared store also rejects
sample rental requests. Real listings retain their normal behavior.

```sh
# List the exact records without changing anything:
.venv/bin/python scripts/seed_demo_data.py

# Create missing sample documents; do not overwrite existing records:
.venv/bin/python scripts/seed_demo_data.py --apply

# Remove only these exact demo documents:
.venv/bin/python scripts/seed_demo_data.py --remove
```

The script reads ignored `.env` and uses the existing Firebase server credentials.
With `FIREBASE_USE_EMULATORS=1`, it instead targets `demo-bikebetter` locally.
Writes are atomic. Cleanup checks demo ownership and refuses reserved bikes or
changed documents. It never deletes real users, real listings, or Stripe objects.
Reapplying skips existing sample records so it does not overwrite manual edits.

Sample rental bikes: Annex City Hybrid, Kensington Cruiser, Bellwoods Road Bike,
Harbourfront Electric, Riverdale Trail Bike, and Campus Commuter. Sample missing
bikes: Blue City Commuter, Red Road Bike, and Green Step-through.
