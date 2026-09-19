# BikeBetter Stripe Connect — test integration

The user approved Connect and **0% platform commission during testing**.
Everything here uses the supplied Stripe sandbox. Live keys are rejected; no
real funds move. Toronto routing and bike registration remain independent.

## Planner and chosen design

Installed the official Stripe plugin with
`claude plugin install stripe@claude-plugins-official` (0.9.2), connected to
[Stripe MCP](https://docs.stripe.com/mcp), confirmed and called
`stripe_implementation_planner`, and accepted its technical plan. Guide:
`iguide_61VQqwxZuqEp3cLaW41FmUF7fWrES`. The MCP EnableConnect operation enabled
Connect in the supplied sandbox. No fallback skill install was needed.

- One **Accounts v2** recipient per Firebase owner; Express dashboard, hosted
  onboarding, recipient `stripe_balance.stripe_transfers` capability. No legacy
  `type=express` account creation or unnecessary merchant capability.
- The sandbox platform takes fee/loss responsibility. This is a test configuration,
  not approval of production financial responsibilities.
- One owner per booking: platform Checkout with a **destination charge** and
  `application_fee_amount=0`. This is 0% BikeBetter commission, not a promise that
  Stripe processing is free. No delayed-release or escrow claim.
- Owner accepts a rental first. Its saved daily rate × inclusive calendar days
  determines the CAD total. Hourly prices remain informational; checkout is daily.
- Stripe-hosted onboarding collects financial/identity details. Our database stores
  only the verified Firebase email, account ID and readiness, never bank details.
  Owners can reopen Express and see Stripe's embedded notification banner.
- The server checks current v2 transfer capability before starting checkout.
  Returning from onboarding does not establish readiness.
- Hosted Checkout uses dynamic payment methods; no card data touches Flask.
  Signed snapshot webhooks handle completion, expiry, async success and failure.
  Signed v2 thin events trigger a fresh account lookup. A browser return also
  reconciles with Stripe, but is not the source of payment truth.

This follows the planner's marketplace recommendation and the plugin's
[stripe-best-practices](https://github.com/stripe/ai/tree/main/providers/agent-plugins/plugin/skills/stripe-best-practices)
guidance. Python SDK 15.6.1 uses API `2026-08-26.dahlia`.

## Review of the original Checkout draft

The initial draft charged only the platform and had no owner onboarding. The
Connect implementation adds owner destinations, fresh capability checks, hosted
setup, Express access, and v2 account events. It replaces the fixed card-only
method list with dynamic methods and handles asynchronous failure explicitly.

Server-owned Firestore payment attempts freeze price, destination, origin, and
idempotency key before Stripe calls. Concurrent/retried requests reuse a session.
Paid state is monotonic; old expired-attempt events cannot affect a new attempt.
An uncertain unbound attempt older than 23 hours requires operator review rather
than risking recreation outside Stripe's idempotency retention window. Check
Stripe request logs and the attempt metadata before any manual recovery.

Firebase rules deny all client payment/account writes and hide records from
unrelated users. A creating/open/processing payment prevents releasing the bike.
A failed/expired checkout may be retried; cancelling checkout does not cancel an
accepted rental. An owner may finish an unpaid rental if no checkout is active.
There is no automated refund, damage deposit, dispute UI, tax calculation,
notification email, or inventory calendar.

## Local setup

```sh
.venv/bin/python -m pip install -r requirements.txt
npm ci
npm install -g @stripe/cli
```

Put these names in the ignored `.env` (never copy secrets into Markdown/Git):

```dotenv
STRIPE_SECRET_KEY=your_test_secret_or_restricted_key
STRIPE_PUBLISHABLE_KEY=your_test_publishable_key
APP_BASE_URL=http://127.0.0.1:5001
FIREBASE_USE_CLI_CREDENTIALS=1
```

The supplied keys are already installed on the current machine. Teammates need
their own sandbox keys or access through Stripe's team settings. Use Firebase
emulators when using a different Stripe sandbox; the shared live ledger belongs
to one Stripe platform. Prefer a
restricted test key with the operations this integration needs. For hosted
Google Cloud deployment, use Secret Manager and Firebase Application Default
Credentials; the CLI credential adapter is an explicit local-development option.

Start the mandatory webhook forwarder **before** Flask, in another terminal:

```sh
.venv/bin/python scripts/stripe-listen.py
.venv/bin/python app.py
```

The helper reads the key without printing it, stores both signing secrets in
ignored `.env`, and forwards snapshot/thin events separately. Restart Flask after
the helper first saves or changes secrets. Keep the helper running during local
payments; configuring a secret alone cannot prove that a listener is running.

Open `/rentals`, sign in with Google, choose **My bikes → Set up test payments**,
and finish Stripe's sandbox onboarding. A renter requests a published bike, the
owner accepts it, then the renter selects **Pay … · Test**. Use Stripe's sandbox
payment methods, never real card/bank information. `4242 4242 4242 4242` with a
future expiry and any three-digit CVC is a successful test card.

For a hosted test deployment, set `APP_BASE_URL` to the exact HTTPS origin and
configure permanent Stripe event destinations instead of a local listener:

| Destination | Events |
| --- | --- |
| `/api/stripe/webhook` (snapshot, platform) | `checkout.session.completed`, `checkout.session.expired`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed` |
| `/api/stripe/connect-webhook` (thin, account events) | `v2.core.account[requirements].updated`, `v2.core.account[configuration.recipient].capability_status_updated` |

Set the respective `STRIPE_WEBHOOK_SECRET` and
`STRIPE_CONNECT_WEBHOOK_SECRET` in the deployment's secret store. Test checkout
is disabled until its signing secret is configured. Do not use local CLI
credentials or the Flask development server as production hosting.

## Verification and scope

```sh
.venv/bin/python -m pytest
npm run test:firebase
```

Verified on 2026-09-19: 129 Python tests, 20 Firestore rule/transaction tests,
registration/photo/rental browser checks, and the opt-in Stripe browser smoke
(`STRIPE_SMOKE_TEST=1 node scripts/stripe_smoke.mjs`, emulators on port 5002).
Real sandbox API checks created owner accounts, hosted onboarding links and banner
sessions. Stripe delivered signed v2 events to the local endpoint with HTTP 200.
Checkout correctly rejected an owner whose transfer capability was still restricted.
A successful sandbox charge/transfer remains to be checked after owner onboarding;
unit tests currently exercise the completed/async payment paths with Stripe fixtures.

Tests cover authoritative quotes, wrong-user access, account readiness, 0% fee
and owner destination, duplicate/concurrent calls, crash recovery, expired
attempts, stale events, async failure, payment signatures, thin-event signatures,
and Firestore ownership/booking locks. Browser checks use the separate
`demo-bikebetter` emulators; test bikes never enter live Firestore.

Before real payments, choose the actual commission and financial responsibility,
complete platform activation, establish refunds/disputes and rental terms,
confirm tax treatment, and provision permanent HTTPS webhooks and server
credentials. Switching to live mode requires an explicit implementation change;
replacing an environment key alone will not enable it.

## References

- [Accounts v2 marketplace setup](https://docs.stripe.com/connect/marketplace/tasks/create)
- [Hosted owner onboarding](https://docs.stripe.com/connect/marketplace/tasks/onboard)
- [Destination charges](https://docs.stripe.com/connect/marketplace/tasks/accept-payment/destination-charges)
- [Checkout fulfillment](https://docs.stripe.com/checkout/fulfillment)
- [Sandbox testing](https://docs.stripe.com/testing)
