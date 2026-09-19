"""Test-only Stripe Checkout. Prices and payment state are controlled by the server."""
from datetime import datetime, timezone
import logging
import os
import re
import time
import secrets
import string
from urllib.parse import urlparse
import uuid

from flask import Blueprint, jsonify, request
import stripe

from firebase_server import FirestoreRepository, verify_user


class PaymentError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise PaymentError("Choose a valid rental request.", 400)
    return value


def daily_quote(rental):
    rate = rental.get("rateDayCents")
    start, end = rental.get("startAt"), rental.get("endAt")
    if type(rate) is not int or not 0 < rate <= 100000 or not isinstance(start, datetime) or not isinstance(end, datetime):
        raise PaymentError("This rental has invalid pricing or dates.")
    days = (end.date() - start.date()).days + 1
    if not 1 <= days <= 31:
        raise PaymentError("This rental has invalid dates.")
    amount = rate * days
    if amount < 50:
        raise PaymentError("Stripe checkout requires a total of at least CAD $0.50.")
    return {"days": days, "rateDayCents": rate, "amountCents": amount, "currency": "cad"}


class StripeGateway:
    def __init__(self, key):
        self.client = stripe.StripeClient(key, max_network_retries=2)

    def create(self, params, key):
        return self.client.v1.checkout.sessions.create(params, options={"idempotency_key": key}).to_dict()

    def retrieve(self, session_id):
        return self.client.v1.checkout.sessions.retrieve(session_id).to_dict()

    def expire(self, session_id):
        return self.client.v1.checkout.sessions.expire(session_id).to_dict()


    def create_account(self, params, key):
        return self.client.v2.core.accounts.create(params, options={"idempotency_key": key}).to_dict()

    def account(self, identifier):
        return self.client.v2.core.accounts.retrieve(identifier, {"include": ["configuration.recipient", "requirements"]}).to_dict()

    def account_link(self, params):
        return self.client.v2.core.account_links.create(params).to_dict()

    def login_link(self, identifier):
        return self.client.v1.accounts.login_links.create(identifier).to_dict()

    def account_session(self, params):
        return self.client.v1.account_sessions.create(params).to_dict()


def configuration():
    key = os.getenv("STRIPE_SECRET_KEY", "")
    origin = os.getenv("APP_BASE_URL", "http://127.0.0.1:5001").rstrip("/")
    parsed = urlparse(origin)
    valid_origin = (parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}))
    valid_origin = valid_origin and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in {"", "/"}
    return key, origin, bool(key.startswith(("sk_test_", "rk_test_")) and valid_origin)


def public_payment_config():
    key = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    return {"enabled": configuration()[2] and bool(os.getenv("STRIPE_WEBHOOK_SECRET")),
            "connectEnabled": configuration()[2], "publishableKey": key if key.startswith("pk_test_") else "",
            "testMode": True, "billing": "daily-inclusive", "platformFeePercent": 0}


class PaymentService:
    def __init__(self, repository, gateway, origin, clock=time.time):
        self.repo, self.gateway, self.origin, self.clock = repository, gateway, origin, clock
        from stripe_connect import ConnectService
        self.connect = ConnectService(repository, gateway, origin, clock=clock)

    def payment(self, identifier, uid):
        def read_payment(read, write):
            rental = read("rentalRequests", identifier)
            if not rental or uid not in (rental["ownerUid"], rental["renterUid"]):
                raise PaymentError("Rental request not found.", 404)
            return read("rentalPayments", identifier)
        return self.repo.run(read_payment)

    def claim(self, identifier, uid):
        def owner(read, write):
            rental = read("rentalRequests", identifier)
            if not rental or rental["renterUid"] != uid:
                raise PaymentError("Only the renter can pay for this request.", 403)
            return rental["ownerUid"], read("rentalPayments", identifier)
        owner_uid, existing = self.repo.run(owner)
        if existing and existing["status"] == "paid":
            return existing
        destination = self.connect.ready_account(owner_uid)
        def claim_attempt(read, write):
            rental = read("rentalRequests", identifier)
            if not rental or rental["renterUid"] != uid:
                raise PaymentError("Only the renter can pay for this request.", 403)
            bike = read("bikes", rental["bikeId"])
            payment = read("rentalPayments", identifier)
            if payment and payment["status"] == "paid":
                return payment
            if rental["status"] != "accepted" or not bike or bike["activeRequestId"] != identifier:
                raise PaymentError("The owner must accept this rental before checkout.")
            if payment and payment["status"] in ("creating", "open", "processing"):
                return payment
            now = int(self.clock())
            payment = {
                **daily_quote(rental), "requestId": identifier,
                "renterUid": rental["renterUid"], "ownerUid": rental["ownerUid"],
                "bikeTitle": rental["bikeTitle"], "status": "creating", "testMode": True,
                "attemptId": uuid.uuid4().hex, "sessionId": "", "checkoutUrl": "",
                "createdAt": now, "updatedAt": now,
                "connectedAccountId": destination, "platformFeeCents": 0,
                "integrationIdentifier": "bikebetter_" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(8)),
                # Stripe retries must have identical parameters, including origin/expiry.
                "baseUrl": self.origin, "expiresAt": now + 86400,
            }
            write("rentalPayments", identifier, payment)
            return payment
        return self.repo.run(claim_attempt)

    def session(self, payment):
        if payment["sessionId"]:
            return self.gateway.retrieve(payment["sessionId"])
        # Never recreate an uncertain attempt after Stripe's 24-hour idempotency window.
        if self.clock() - payment["createdAt"] > 23 * 3600:
            raise PaymentError("This checkout needs review before it can be retried. Contact the site team.")
        base, identifier = payment["baseUrl"], payment["requestId"]
        params = {
            "mode": "payment", "integration_identifier": payment["integrationIdentifier"],
            "payment_intent_data": {"application_fee_amount": 0,
                                    "transfer_data": {"destination": payment["connectedAccountId"]}},
            "client_reference_id": identifier,
            "metadata": {"requestId": identifier, "attemptId": payment["attemptId"]},
            "line_items": [{"quantity": payment["days"], "price_data": {
                "currency": "cad", "unit_amount": payment["rateDayCents"],
                "product_data": {"name": payment["bikeTitle"] + " · daily rental (test)"},
            }}],
            "success_url": base + "/rentals?checkout=success&request_id=" + identifier,
            "cancel_url": base + "/rentals?checkout=cancelled&request_id=" + identifier,
        }
        try:
            return self.gateway.create(params, "rental-" + payment["attemptId"])
        except stripe.InvalidRequestError:
            # A definitive rejected create made no session. Unlike a timeout,
            # it can release the attempt safely (e.g. capability changed mid-call).
            def reject(read, write):
                saved = read("rentalPayments", identifier)
                if (saved and saved["attemptId"] == payment["attemptId"]
                    and saved["status"] == "creating" and not saved["sessionId"]):
                    write("rentalPayments", identifier, {**saved, "status": "failed", "updatedAt": int(self.clock())})
            self.repo.run(reject)
            raise PaymentError("Stripe could not start checkout. Ask the owner to check Stripe setup, then retry.", 409) from None

    def settle(self, session, failed=False):
        metadata = session.get("metadata") or {}
        identifier = request_id(metadata.get("requestId"))
        def update(read, write):
            payment = read("rentalPayments", identifier)
            if not payment or metadata.get("attemptId") != payment["attemptId"]:
                return None  # Old/replayed event for an earlier, expired attempt.
            if (session.get("livemode") is not False or session.get("mode") != "payment"
                or session.get("client_reference_id") != identifier
                or session.get("currency") != payment["currency"]
                or session.get("amount_total") != payment["amountCents"]
                or (payment["sessionId"] and session.get("id") != payment["sessionId"])):
                raise PaymentError("Stripe checkout did not match the saved rental.", 400)
            if payment["status"] == "paid":
                return payment
            paid = session.get("payment_status") == "paid"
            status = ("paid" if paid else "failed" if failed else "expired" if session.get("status") == "expired"
                      else "processing" if session.get("status") == "complete" else "open")
            if payment["status"] in ("expired", "failed") and status != "paid":
                return payment
            payment = {**payment, "status": status, "sessionId": session["id"],
                       "checkoutUrl": session.get("url") or "", "updatedAt": int(self.clock()),
                       "expiresAt": session.get("expires_at", payment["expiresAt"])}
            if paid:
                payment["paidAt"] = int(self.clock())
            write("rentalPayments", identifier, payment)
            return payment
        return self.repo.run(update)

    def checkout(self, identifier, uid):
        payment = self.claim(identifier, uid)
        if payment["status"] == "paid":
            return {"status": "paid"}
        result = self.settle(self.session(payment))
        if not result or result["status"] == "expired":
            raise PaymentError("That checkout expired. Click Pay again to start a new one.")
        if result["status"] == "open":
            url = urlparse(result["checkoutUrl"])
            if url.scheme != "https" or url.hostname != "checkout.stripe.com":
                raise PaymentError("Stripe did not return a valid checkout link.", 502)
        return {"status": result["status"], "url": result["checkoutUrl"]}

    def refresh(self, identifier, uid, cancel=False):
        payment = self.payment(identifier, uid)
        if not payment or payment["status"] in ("paid", "expired", "failed"):
            return {"status": payment["status"] if payment else "unpaid"}
        session = self.session(payment)
        if cancel and session.get("status") == "open":
            try:
                session = self.gateway.expire(session["id"])
            except stripe.InvalidRequestError:
                # Payment may have completed while the user cancelled.
                session = self.gateway.retrieve(session["id"])
        result = self.settle(session)
        return {"status": result["status"] if result else "unpaid"}


def payments_blueprint(service=None, verifier=verify_user):
    bp = Blueprint("payments", __name__)

    def current_service():
        if service is not None:
            return service
        key, origin, enabled = configuration()
        if not enabled:
            raise PaymentError("Test checkout is not configured yet.", 503)
        return PaymentService(FirestoreRepository(), StripeGateway(key), origin)

    def uid():
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise PaymentError("Sign in with Google before checkout.", 401)
        try:
            return verifier(header[7:])
        except Exception:
            raise PaymentError("Your sign-in expired. Sign in again.", 401) from None

    def handle(action):
        try:
            user = uid()
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or set(body) != {"requestId"}:
                raise PaymentError("Send only the rental request ID.", 400)
            result = action(current_service(), request_id(body["requestId"]), user)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PaymentError as error:
            return jsonify(error=str(error)), error.status
        except Exception as error:
            logging.error("Payment service failed (%s)", type(error).__name__)
            return jsonify(error="Checkout is temporarily unavailable. Try again; your payment status will be checked first."), 503

    @bp.post("/api/connect/<action>")
    def connect_action(action):
        try:
            user = uid()
            if request.get_json(silent=True) != {}:
                raise PaymentError("Send an empty object; your signed-in identity selects the owner account.", 400)
            if action not in {"status", "onboarding", "dashboard", "banner"}:
                raise PaymentError("Unknown owner action.", 404)
            result = getattr(current_service().connect, action)(user)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PaymentError as error:
            return jsonify(error=str(error)), error.status
        except Exception as error:
            logging.error("Connect service failed (%s)", type(error).__name__)
            return jsonify(error="Stripe owner setup is temporarily unavailable. Try again shortly."), 503

    @bp.post("/api/stripe/connect-webhook")
    def connect_webhook():
        secret = os.getenv("STRIPE_CONNECT_WEBHOOK_SECRET", "")
        if not secret:
            return jsonify(error="Connect webhook is not configured."), 503
        try:
            import json
            payload = request.get_data().decode("utf-8")
            stripe.WebhookSignature.verify_header(payload, request.headers.get("Stripe-Signature", ""), secret, tolerance=300)
            event = json.loads(payload)
            if event.get("livemode") is not False:
                raise ValueError("Test only")
        except (ValueError, stripe.SignatureVerificationError):
            return jsonify(error="Invalid test webhook."), 400
        if event.get("type") not in {"v2.core.account[requirements].updated", "v2.core.account[configuration.recipient].capability_status_updated"}:
            return jsonify(received=True)
        identifier = (event.get("related_object") or {}).get("id", "")
        if not isinstance(identifier, str) or not identifier.startswith("acct_"):
            return jsonify(error="Missing related account."), 400
        try:
            service = current_service()
            service.connect.sync(service.gateway.account(identifier))
        except Exception as error:
            logging.error("Connect webhook failed (%s)", type(error).__name__)
            return jsonify(error="Owner update could not be saved; retry delivery."), 503
        return jsonify(received=True)

    @bp.post("/api/payments/checkout")
    def checkout():
        if service is None and not public_payment_config()["enabled"]:
            return jsonify(error="Test checkout requires webhook configuration."), 503
        return handle(lambda s, identifier, user: s.checkout(identifier, user))

    @bp.post("/api/payments/refresh")
    def refresh():
        return handle(lambda s, identifier, user: s.refresh(identifier, user))

    @bp.post("/api/payments/cancel")
    def cancel():
        return handle(lambda s, identifier, user: s.refresh(identifier, user, cancel=True))

    @bp.post("/api/stripe/webhook")
    def webhook():
        secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        if not secret:
            return jsonify(error="Webhook is not configured."), 503
        try:
            event = stripe.Webhook.construct_event(request.get_data(), request.headers.get("Stripe-Signature", ""), secret).to_dict()
        except (ValueError, stripe.SignatureVerificationError):
            return jsonify(error="Invalid webhook signature."), 400
        if event.get("livemode") is not False:
            return jsonify(error="Only test events are supported."), 400
        if event["type"] not in {"checkout.session.completed", "checkout.session.expired", "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed"}:
            return jsonify(received=True)
        # Ignore unrelated Checkout integrations on the same Stripe account.
        if not event["data"]["object"].get("metadata", {}).get("requestId"):
            return jsonify(received=True)
        try:
            current_service().settle(event["data"]["object"], failed=event["type"] == "checkout.session.async_payment_failed")
        except PaymentError as error:
            return jsonify(error=str(error)), error.status
        except Exception as error:
            logging.error("Payment webhook failed (%s)", type(error).__name__)
            return jsonify(error="Payment update could not be saved; retry delivery."), 503
        return jsonify(received=True)

    return bp
