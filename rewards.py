"""Test-only Stripe Checkout for missing-bike return rewards.

The reward amount is read server-side from the owner's theft report, so the
browser can never choose the price. This is pay-on-recovery: the owner pays the
reward they pledged; the finder is settled off-platform.
"""
import logging
import os
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request
import stripe

from firebase_server import FirestoreRepository, verify_user
from payments import PaymentError, configuration, request_id


def rewards_blueprint(verifier=verify_user, repository=None):
    bp = Blueprint("rewards", __name__)

    def uid():
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise PaymentError("Sign in with Google first.", 401)
        try:
            return verifier(header[7:])
        except Exception:
            raise PaymentError("Your sign-in expired. Sign in again.", 401) from None

    @bp.post("/api/reward/checkout")
    def reward_checkout():
        try:
            key, origin, enabled = configuration()
            if not enabled:
                raise PaymentError("Test checkout is not configured yet.", 503)
            user = uid()
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or set(body) != {"reportId"}:
                raise PaymentError("Send only the report ID.", 400)
            report_id = request_id(body["reportId"])
            repo = repository or FirestoreRepository()
            report = repo.db.collection("theftReports").document(report_id).get().to_dict()
            if not report:
                raise PaymentError("That missing-bike report no longer exists.", 404)
            if report.get("ownerUid") != user:
                raise PaymentError("Only the bike's owner can pay this reward.", 403)
            amount = report.get("rewardCents")
            if type(amount) is not int or amount < 50 or amount > 100000:
                raise PaymentError("Set a reward of at least CAD $0.50 before paying.", 400)
            title = str(report.get("title") or "bike")[:120]
            client = stripe.StripeClient(key, max_network_retries=2)
            session = client.v1.checkout.sessions.create({
                "mode": "payment",
                "line_items": [{
                    "quantity": 1,
                    "price_data": {
                        "currency": "cad",
                        "unit_amount": amount,
                        "product_data": {"name": f"Return reward - {title}"},
                    },
                }],
                "metadata": {"rewardReportId": report_id},
                "success_url": origin + "/missing?reward=paid&report=" + report_id,
                "cancel_url": origin + "/missing?reward=cancelled",
            }, options={"idempotency_key": f"reward_{report_id}_{amount}"}).to_dict()
            url = session.get("url") or ""
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != "checkout.stripe.com":
                raise PaymentError("Stripe did not return a valid checkout link.", 502)
            response = jsonify(url=url)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PaymentError as error:
            return jsonify(error=str(error)), error.status
        except Exception as error:
            logging.error("Reward checkout failed (%s)", type(error).__name__)
            return jsonify(error="Reward checkout is temporarily unavailable. Try again shortly."), 503

    return bp
