# Firebase accounts, bike registration, and rentals

The user approved Google sign-in, private bike registration, and optional rental
publication after the Toronto routing website was complete. This supersedes the
original brief's account/database non-goals. Stripe Connect sandbox payments are now implemented; see [the Stripe plan](stripe-connect-plan.md). No real payments are enabled.

## Project setup

The registered web app belongs to `blyatbike`; its public identifiers are in
`firebase_config.py`. The actual web API key belongs in the ignored `.env`
(`FIREBASE_API_KEY`, see `.env.example`). The Flask dev entry point loads it;
production deployments must supply environment variables themselves.

Google sign-in has been deployed. Its display name is set to **BikeBetter** in
`firebase.json` (a changed name reaches the live project on the next
`firebase deploy --only auth --project blyatbike`) and its support email is the
CLI account, `brian.r.xiao@gmail.com`. Authorized domains
include `localhost`, `127.0.0.1`, `blyatbike.firebaseapp.com`, and
`blyatbike.web.app`. Add the eventual website hostname in Firebase Authentication
settings before using sign-in from a deployed website.

Live setup completed on 2026-09-19. The **(default)** Firestore database is in
**Standard edition**, **northamerica-northeast2 (Toronto)**. The repository's
security rules and listing index have been deployed. Google OAuth startup and
the website's live listing query succeeded; unauthenticated private-document
reads were denied. Registration and rental writes were tested in the emulators,
not with fabricated accounts or records in the live project.

To deploy subsequent configuration changes:

```sh
firebase deploy --only auth,firestore --project blyatbike
```

`firebase.json` contains the reviewed Google provider configuration, rules, and
listing index. Keep database rules deployed alongside data-model changes.
This repository does not configure Firebase Hosting: serving only these HTML
files would omit the Flask route API and local Toronto graph.

The browser Firebase key is public by design once served, and does not grant
admin access. Firestore rules enforce authorization. HERE_API_KEY is different:
it must remain server-only and is never included in `/api/config`.

## User workflow and data boundaries

1. Open `/rentals`, sign in with Google, and choose **Register a bike**.
2. Enter bike details. The serial number and bike photo are optional. Notes and
   serial number are always private; the photo is private until publication.
3. Keep the rental option unchecked to save privately. To publish, add a pickup
   neighbourhood and hourly/daily CAD rates. Avoid exact home addresses and
   personal contact details in the public description.
4. Renters choose dates and send a request. Their Google name and email are
   disclosed to that bike's owner, along with a snapshot of the quoted rates.
5. Owners accept or decline in **Rental requests**. Acceptance shares the owner's
   email with the renter and removes the bike from available listings. Only one
   request can be accepted at a time, including under concurrent writes.
6. The owner marks the bike returned to make it available again. Renters can
   cancel pending requests. Accepted rentals must be resolved with the owner.

Registration is a private account record, not ownership verification, police
registration, 529 Garage integration, insurance, or a guarantee of condition.
Pickup and disputes are arranged directly. Stripe test checkout charges the saved
daily rate for inclusive rental dates after owner acceptance and Stripe setup.
There are no real payments, email notifications, rental expiry, or date-based inventory.
Server-only `rentalPayments` and `stripeAccounts` records support the payment flow;
clients cannot write them. Active checkout prevents completing the rental.
The UI shows up to 100 documents per listing/account/request query; pagination
and abuse controls are follow-up work before opening a large public marketplace.

| Path | Read access | Contents |
| --- | --- | --- |
| `bikes/{id}` | Owner; everyone when published | Public-safe description, rates, availability, owner UID |
| `bikes/{id}/private/details` | Owner only | Serial number, private notes |
| `bikes/{id}/photos/main` | Owner; everyone when parent bike is published | One compressed JPEG |
| `rentalRequests/{id}` | Owner and renter only | Dates, messages, rate snapshot, contact details, status |

All writes require a verified account and are constrained by `firestore.rules`.
Unpublishing preserves private registration. Removing a bike removes its private
details but preserves existing rental requests for the two parties' history.
Bike deletion also deletes its photo in the same transaction.
Live listeners clear private views on sign-out. User-entered text renders via
`textContent`, never HTML. Firebase modules load only on the rentals page.

### Bike photos

Upload one JPEG, PNG, or WebP up to 15 MB from the registration/edit form. The
browser resizes it to at most 1200 pixels on the longest side, re-encodes to JPEG
without original EXIF/GPS metadata, and reduces quality/size until the data URL
is at most 220,000 characters (about 165 KB of image bytes). Preview, replacement,
and removal take effect on Save. HEIC files must first be exported as JPEG.

The project has billing disabled and no storage bucket. Since
[Firebase Storage requires Blaze](https://firebase.google.com/docs/storage/faq-and-troubleshooting),
this limited single-photo feature uses a separate Firestore document with its
`dataUrl` field excluded from indexes. Card photos load when they become visible;
listing queries do not include image data. The rules enforce size/type and parent
ownership/publication. Large galleries and original-resolution uploads require
moving images to object storage; this implementation is intended for the small
marketplace demo and uses the existing Firestore storage/read quotas.

## Local verification without live writes

Install Node 22+, Java 21, and Firebase CLI (`npm install -g firebase-tools`).

```sh
npm ci
npm run test:firebase
.venv/bin/python -m pytest -q
```

For browser testing, start `npm run emulators` in one terminal. In another:

```sh
FIREBASE_USE_EMULATORS=1 .venv/bin/python -c 'from app import create_app; from traffic import TrafficCache; create_app(traffic=TrafficCache(None)).run(port=5002)'
```

Open `http://127.0.0.1:5002/rentals`. Google popup sign-in uses fake local
accounts in this mode. Emulators use project `demo-bikebetter`, auth port 9099,
and Firestore port 8080. Never use emulator tokens or test records in the live
project. Test data is disposable and is not exported on shutdown.

With Chrome debugging on port 9224 and a tab open to port 5002, run
`node scripts/rentals_smoke.mjs`. Start with an empty emulator. This tests private
registration with no serial, photo upload/replacement/removal, editing,
publication, renter requests, acceptance, contact sharing,
return, mobile layout, and sign-out cleanup. It refuses to run without the
emulator marker. Screenshots are saved to ignored `artifacts/`.

Official references: [Google sign-in](https://firebase.google.com/docs/auth/web/google-signin),
[CLI auth configuration](https://firebase.google.com/docs/auth/configure-providers-cli),
[Firestore field rules](https://firebase.google.com/docs/firestore/security/rules-fields),
and [rules unit tests](https://firebase.google.com/docs/rules/unit-tests).
