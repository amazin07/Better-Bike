"""Payment logic and HTTP security, without network calls or real card data."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Flask
import pytest

from payments import PaymentError, PaymentService, daily_quote, payments_blueprint, public_payment_config


class MemoryRepository:
    def __init__(self):
        self.docs = {}
        self.lock = threading.Lock()

    def run(self, callback):
        with self.lock:
            docs = copy.deepcopy(self.docs)
            def read(collection, identifier):
                return copy.deepcopy(docs.get((collection, identifier)))
            def write(collection, identifier, value):
                docs[(collection, identifier)] = copy.deepcopy(value)
            result = callback(read, write)
            self.docs = docs
            return result


class FakeStripe:
    def __init__(self):
        self.sessions = {}
        self.params = {}
        self.lock = threading.Lock()
        self.accounts = {'acct_owner': {'id':'acct_owner', 'livemode':False, 'metadata':{'firebaseUid':'owner'}, 'configuration': {'recipient': {'capabilities': {'stripe_balance': {'stripe_transfers': {'status':'active'}}}}}}}

    def account(self, identifier):
        return copy.deepcopy(self.accounts[identifier])

    def create(self, params, key):
        with self.lock:
            if key in self.params:
                assert params == self.params[key]
            else:
                self.params[key] = copy.deepcopy(params)
                identifier = 'cs_test_' + str(len(self.params))
                item = params['line_items'][0]
                self.sessions[key] = dict(id=identifier, metadata=params['metadata'],
                    client_reference_id=params['client_reference_id'], mode='payment', livemode=False,
                    amount_total=item['quantity'] * item['price_data']['unit_amount'], currency='cad',
                    payment_status='unpaid', status='open', url='https://checkout.stripe.com/c/pay/'+identifier)
            return copy.deepcopy(self.sessions[key])

    def retrieve(self, identifier):
        return copy.deepcopy(next(s for s in self.sessions.values() if s['id'] == identifier))

    def expire(self, identifier):
        session = next(s for s in self.sessions.values() if s['id'] == identifier)
        session.update(status='expired', url=None)
        return copy.deepcopy(session)


@pytest.fixture
def setup():
    repo, gateway = MemoryRepository(), FakeStripe()
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)
    rental = dict(ownerUid='owner', renterUid='renter', bikeId='bike1', bikeTitle='Test bike',
                  status='accepted', rateDayCents=3000, startAt=start, endAt=start+timedelta(days=2))
    repo.docs[('rentalRequests', 'rent1')] = rental
    repo.docs[('bikes', 'bike1')] = dict(activeRequestId='rent1')
    repo.docs['stripeAccounts','owner'] = dict(ownerUid='owner', accountId='acct_owner', testMode=True)
    service = PaymentService(repo, gateway, 'http://127.0.0.1:5001')
    return repo, gateway, service


def test_daily_pricing_is_integer_cad_and_dates_are_inclusive(setup):
    repo, _, _ = setup
    quote = daily_quote(repo.docs['rentalRequests','rent1'])
    assert quote == dict(days=3, rateDayCents=3000, amountCents=9000, currency='cad')
    for price in [True, -100, 1.5, 100001, 0, 1]:
        rental = {**repo.docs['rentalRequests','rent1'], 'rateDayCents': price}
        with pytest.raises(PaymentError): daily_quote(rental)


def test_only_renter_of_accepted_active_rental_can_checkout(setup):
    repo, _, service = setup
    for uid in ['owner', 'other']:
        with pytest.raises(PaymentError): service.checkout('rent1', uid)
    repo.docs['rentalRequests','rent1']['status'] = 'pending'
    with pytest.raises(PaymentError): service.checkout('rent1', 'renter')
    assert not repo.docs.get(('rentalPayments','rent1'))
    repo.docs['rentalRequests','rent1']['status'] = 'accepted'
    repo.docs['bikes','bike1']['activeRequestId'] = 'other-request'
    with pytest.raises(PaymentError): service.checkout('rent1', 'renter')


def test_concurrent_checkout_retries_share_a_stripe_idempotency_key(setup):
    repo, gateway, service = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.checkout('rent1', 'renter'), range(4)))
    assert len({r['url'] for r in results}) == 1
    assert len(gateway.sessions) == 1
    assert repo.docs['rentalPayments','rent1']['amountCents'] == 9000


def test_retry_recovers_after_stripe_creation_before_firestore_save(setup):
    _, gateway, service = setup
    payment = service.claim('rent1', 'renter')
    created = service.session(payment)  # Simulate crash before settle.
    result = service.checkout('rent1', 'renter')
    assert result['url'] == created['url']
    assert len(gateway.sessions) == 1


def test_paid_event_is_idempotent_and_cannot_be_downgraded(setup):
    repo, gateway, service = setup
    service.checkout('rent1', 'renter')
    session = next(iter(gateway.sessions.values()))
    stale = copy.deepcopy(session)
    session.update(status='complete', payment_status='paid', url=None)
    service.settle(session)
    service.settle(session)
    service.settle(stale)
    assert repo.docs['rentalPayments','rent1']['status'] == 'paid'
    assert service.checkout('rent1','renter') == {'status':'paid'}
    assert len(gateway.sessions) == 1


@pytest.mark.parametrize('change', [{'amount_total':1},{'currency':'usd'},{'livemode':True},
                                    {'client_reference_id':'other'},{'id':'cs_test_forged'}])
def test_mismatched_sessions_never_mark_paid(setup, change):
    repo, gateway, service = setup
    service.checkout('rent1','renter')
    session = {**next(iter(gateway.sessions.values())), 'payment_status':'paid', **change}
    with pytest.raises(PaymentError): service.settle(session)
    assert repo.docs['rentalPayments','rent1']['status'] == 'open'


def test_cancel_expiry_retry_and_old_events(setup):
    repo, gateway, service = setup
    service.checkout('rent1','renter')
    old = copy.deepcopy(next(iter(gateway.sessions.values())))
    assert service.refresh('rent1','owner',cancel=True)['status'] == 'expired'
    service.settle(old)  # Late open response cannot undo cancellation.
    assert repo.docs['rentalPayments','rent1']['status'] == 'expired'
    service.checkout('rent1','renter')
    assert len(gateway.sessions) == 2
    assert service.settle(old) is None
    with pytest.raises(PaymentError): service.refresh('rent1','stranger',cancel=True)


def test_return_is_verified_against_stripe_not_browser_parameters(setup):
    _, gateway, service = setup
    service.checkout('rent1','renter')
    assert service.refresh('rent1','renter')['status'] == 'open'
    next(iter(gateway.sessions.values())).update(payment_status='paid',status='complete')
    assert service.refresh('rent1','renter')['status'] == 'paid'


def test_unresolved_old_attempt_does_not_risk_a_duplicate_payment(setup):
    repo, _, service = setup
    service.claim('rent1','renter')
    repo.docs['rentalPayments','rent1']['createdAt'] -= 24*3600
    with pytest.raises(PaymentError): service.checkout('rent1','renter')


def client_for(service):
    def verify(token):
        if token != 'valid-renter-token': raise ValueError('Bad token')
        return 'renter'
    app = Flask(__name__)
    app.register_blueprint(payments_blueprint(service, verify))
    return app.test_client()


def test_http_rejects_missing_auth_invalid_auth_and_client_prices(setup):
    client = client_for(setup[2])
    assert client.post('/api/payments/checkout', json={'requestId':'rent1'}).status_code == 401
    assert client.post('/api/payments/checkout', json={'requestId':'rent1'},headers={'Authorization':'Bearer wrong'}).status_code == 401
    headers = {'Authorization':'Bearer valid-renter-token'}
    for body in [{'requestId':'rent1','amount':1}, {'requestId':'../rent1'}, [], {}]:
        assert client.post('/api/payments/checkout',json=body,headers=headers).status_code == 400
    response = client.post('/api/payments/checkout',json={'requestId':'rent1'},headers=headers)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'


def test_missing_or_live_keys_disable_checkout(monkeypatch):
    monkeypatch.delenv('STRIPE_SECRET_KEY', raising=False)
    assert public_payment_config()['enabled'] is False
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_live_no_real_key')
    assert public_payment_config()['enabled'] is False
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_fixture')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_fixture')
    monkeypatch.setenv('APP_BASE_URL', 'https://example.com')
    assert public_payment_config()['enabled'] is True
    for origin in ['http://example.com','https://example.com?redirect=evil','https://user:pass@example.com']:
        monkeypatch.setenv('APP_BASE_URL',origin)
        assert public_payment_config()['enabled'] is False


def test_webhook_requires_valid_signature_and_handles_duplicates(setup, monkeypatch):
    repo, gateway, service = setup
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','whsec_fixture')
    client = client_for(service)
    service.checkout('rent1','renter')
    session = next(iter(gateway.sessions.values()))
    session.update(status='complete',payment_status='paid')
    event = dict(id='evt_fixture',type='checkout.session.completed',livemode=False,data={'object':session})
    payload = json.dumps(event).encode()
    assert client.post('/api/stripe/webhook',data=payload).status_code == 400
    now = str(int(time.time()))
    signature = hmac.new(b'whsec_fixture',now.encode()+b'.'+payload,hashlib.sha256).hexdigest()
    headers = {'Stripe-Signature':f't={now},v1={signature}','Content-Type':'application/json'}
    assert client.post('/api/stripe/webhook',data=payload,headers=headers).status_code == 200
    assert client.post('/api/stripe/webhook',data=payload,headers=headers).status_code == 200
    assert repo.docs['rentalPayments','rent1']['status'] == 'paid'


def test_destination_and_fee_are_server_owned(setup):
    repo, gateway, service = setup
    service.checkout('rent1', 'renter')
    params = next(iter(gateway.params.values()))
    assert params['payment_intent_data'] == {'application_fee_amount': 0, 'transfer_data': {'destination':'acct_owner'}}
    assert 'payment_method_types' not in params
    assert len(params['integration_identifier'].split('_')[-1]) == 8
    assert repo.docs['rentalPayments','rent1']['platformFeeCents'] == 0


def test_owner_must_have_current_transfer_capability(setup):
    repo, gateway, service = setup
    gateway.accounts['acct_owner']['configuration']['recipient']['capabilities']['stripe_balance']['stripe_transfers']['status'] = 'restricted'
    with pytest.raises(PaymentError, match='owner needs to finish'): service.checkout('rent1','renter')
    assert not gateway.sessions
    del repo.docs['stripeAccounts','owner']
    with pytest.raises(PaymentError): service.checkout('rent1','renter')


def test_async_failure_unlocks_retry_without_stale_event_downgrade(setup):
    repo, gateway, service = setup
    service.checkout('rent1','renter')
    session = next(iter(gateway.sessions.values()))
    session.update(status='complete', payment_status='unpaid')
    service.settle(session)
    assert service.refresh('rent1','owner',cancel=True)['status'] == 'processing'
    service.settle(session, failed=True)
    service.settle(session)
    assert repo.docs['rentalPayments','rent1']['status'] == 'failed'
    service.checkout('rent1','renter')
    assert len(gateway.sessions) == 2


def test_owner_creation_is_idempotent_and_identity_is_server_verified(setup):
    repo, gateway, service = setup
    created = {}
    def create(params, key):
        if key in created: assert created[key] == params
        created[key] = params
        return dict(id='acct_renter', livemode=False)
    gateway.create_account = create
    service.connect.email_lookup = lambda uid: uid+'@example.test'
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: service.connect.ensure('renter'), range(4)))
    assert {r['accountId'] for r in result} == {'acct_renter'}
    assert len(created) == 1
    params = next(iter(created.values()))
    assert params['contact_email'] == 'renter@example.test'
    assert params['metadata']['firebaseUid'] == 'renter'
    assert 'type' not in params
    assert 'merchant' not in params['configuration']
    client = client_for(service)
    assert client.post('/api/connect/status',json={}).status_code == 401
    headers={'Authorization':'Bearer valid-renter-token'}
    assert client.post('/api/connect/onboarding',json={'accountId':'acct_victim'},headers=headers).status_code == 400


def test_thin_webhook_verifies_signature_and_refetches_account(setup, monkeypatch):
    repo, gateway, service = setup
    monkeypatch.setenv('STRIPE_CONNECT_WEBHOOK_SECRET', 'whsec_thin_fixture')
    client = client_for(service)
    payload = json.dumps({'id':'evt_thin','type':'v2.core.account[requirements].updated',
                          'livemode':False, 'related_object':{'id':'acct_owner'}}).encode()
    assert client.post('/api/stripe/connect-webhook',data=payload).status_code == 400
    now = str(int(time.time()))
    signature = hmac.new(b'whsec_thin_fixture',now.encode()+b'.'+payload,hashlib.sha256).hexdigest()
    headers={'Stripe-Signature':f't={now},v1={signature}'}
    assert client.post('/api/stripe/connect-webhook',data=payload,headers=headers).status_code == 200
    assert repo.docs['stripeAccounts','owner']['ready'] is True
    gateway.accounts['acct_owner']['metadata']['firebaseUid'] = 'stranger'
    assert client.post('/api/stripe/connect-webhook',data=payload,headers=headers).status_code == 200
    assert ('stripeAccounts','stranger') not in repo.docs


def test_definitive_stripe_rejection_releases_attempt_but_timeout_stays_uncertain(setup):
    import stripe
    repo, gateway, service = setup
    original = gateway.create
    def rejected(params, key):
        raise stripe.InvalidRequestError('Capability changed', 'destination')
    gateway.create = rejected
    with pytest.raises(PaymentError): service.checkout('rent1', 'renter')
    assert repo.docs['rentalPayments','rent1']['status'] == 'failed'
    def timeout(params, key):
        raise stripe.APIConnectionError('Timeout')
    gateway.create = timeout
    with pytest.raises(stripe.APIConnectionError): service.checkout('rent1', 'renter')
    assert repo.docs['rentalPayments','rent1']['status'] == 'creating'
    gateway.create = original
    assert service.checkout('rent1','renter')['status'] == 'open'
