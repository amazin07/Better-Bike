# Vercel deployment

The Flask preset uses Python 3.13, `app.py`, and `vercel.json`. The build stages
the existing static files and Stripe's npm loader into `public/static` for the CDN.
`requirements.txt` contains runtime dependencies; use `requirements-dev.txt` for
data rebuilding and tests. No routing downloads run during requests or deployment.

## Routing snapshot

`routing-data/toronto.json.gz` is a 1.4 MB portable JSON export of the real local
Toronto graph, retaining direction, parallel edges, geometry and collision risk.
Collision identifiers are remapped to opaque local numbers; person fields and
raw source files are omitted. This is backend data, not a public static endpoint.
Rebuild after updating the source datasets:

```sh
.venv/bin/python prepare_data.py
.venv/bin/python scripts/export_routing_bundle.py
.venv/bin/python -m pytest
```

Local development prefers the trusted pickle; Vercel always loads the JSON bundle.
Never publish `.env`, `.projects`, service-account files, raw CSVs or pickle caches.
Both `.vercelignore` and function exclusions protect local files; provide hosted
credentials through Vercel environment variables, not uploaded credential files.

## Hosting and configuration

Hosting is managed through the repository's Stripe Projects CLI skill. Provision
Vercel's free Hobby plan and project after the account owner accepts the provider
terms. Vercel CLI also requires a valid login. Do not upgrade to a paid plan by default.

Set these variables on the intended deployment environment:

| Variable | Purpose |
| --- | --- |
| `FIREBASE_API_KEY` | Existing Firebase web configuration |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Server-only Firebase service-account JSON, or use ADC |
| `STRIPE_SECRET_KEY` | Existing sandbox `sk_test_` key only |
| `STRIPE_PUBLISHABLE_KEY` | Matching sandbox `pk_test_` key |
| `APP_BASE_URL` | Stable HTTPS deployment origin |
| `STRIPE_WEBHOOK_SECRET` | Signing secret for the hosted Checkout endpoint |
| `STRIPE_CONNECT_WEBHOOK_SECRET` | Signing secret for the hosted Accounts v2 endpoint |

Never set `FIREBASE_USE_CLI_CREDENTIALS` or emulator variables on Vercel. Grant the
server identity only the Firestore and Firebase Auth access required for payments.
Add the stable hostname to Firebase Authentication's authorized domains. Configure
the two signed Stripe endpoints and events listed in `stripe-connect-plan.md`;
the local Stripe CLI listener's secrets cannot be reused for hosted endpoints.

**Live HERE traffic needs additional hosting work:** the current in-memory cache
and filesystem quota counter are designed for one persistent server. Vercel
instances have ephemeral filesystems and can scale independently, so keep
`HERE_API_KEY` unset there until a shared quota/cache strategy is implemented.
The local laptop's live traffic remains available. Routing works without traffic.

After provisioning and environment configuration, deploy with `npx vercel --prod`.
Verify `/api/health`, route calculation, static files, Google sign-in, rental
registration, and a signed sandbox checkout webhook on the deployed origin.
Do not treat local tests as proof of a successful hosted deployment.
