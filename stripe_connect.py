"""Test-only owner accounts. Account IDs always come from server-owned records."""
import time
import uuid

from payments import PaymentError


class ConnectService:
    def __init__(self, repository, gateway, origin, email_lookup=None, clock=time.time):
        self.repo, self.gateway, self.origin = repository, gateway, origin
        self.clock = clock
        if email_lookup is None:
            from firebase_server import verified_email
            email_lookup = verified_email
        self.email_lookup = email_lookup

    def record(self, uid):
        return self.repo.run(lambda read, write: read('stripeAccounts', uid))

    def ensure(self, uid):
        # Freeze creation parameters before Stripe; concurrent clicks share an attempt.
        existing = self.record(uid)
        if existing and existing.get('accountId'):
            return existing
        email = self.email_lookup(uid)
        def claim(read, write):
            record = read('stripeAccounts', uid)
            if not record:
                record = dict(ownerUid=uid, accountId='', attemptId=uuid.uuid4().hex,
                              contactEmail=email, createdAt=int(self.clock()), testMode=True)
                write('stripeAccounts', uid, record)
            return record
        record = self.repo.run(claim)
        if record.get('accountId'):
            return record
        if self.clock() - record['createdAt'] > 23 * 3600:
            raise PaymentError('Owner setup needs review before retrying. Contact the site team.')
        account = self.gateway.create_account({
            'contact_email': record['contactEmail'], 'display_name': 'BikeBetter bike owner',
            'dashboard': 'express', 'identity': {'country': 'ca'},
            'defaults': {'responsibilities': {'fees_collector': 'application', 'losses_collector': 'application'}},
            'configuration': {'recipient': {'capabilities': {'stripe_balance': {'stripe_transfers': {'requested': True}}}}},
            'metadata': {'firebaseUid': uid, 'integration': 'bikebetter-test'},
            'include': ['configuration.recipient', 'requirements'],
        }, 'owner-' + record['attemptId'])
        if account.get('livemode') is not False:
            raise PaymentError('Only test accounts are supported.')
        def bind(read, write):
            saved = read('stripeAccounts', uid)
            if saved['attemptId'] != record['attemptId'] or (saved['accountId'] and saved['accountId'] != account['id']):
                raise PaymentError('Owner account changed. Reload and try again.')
            saved = {**saved, 'accountId': account['id']}
            write('stripeAccounts', uid, saved)
            return saved
        return self.repo.run(bind)

    def snapshot(self, account):
        if account.get('livemode') is not False:
            raise PaymentError('Only test accounts are supported.')
        recipient = (account.get('configuration') or {}).get('recipient') or {}
        transfers = ((recipient.get('capabilities') or {}).get('stripe_balance') or {}).get('stripe_transfers') or {}
        status = transfers.get('status', 'unrequested')
        return {'exists': True, 'ready': status == 'active', 'transferStatus': status,
                'testMode': True, 'platformFeePercent': 0}

    def sync(self, account):
        uid = (account.get('metadata') or {}).get('firebaseUid')
        if not uid:
            return None
        status = self.snapshot(account)
        def save(read, write):
            record = read('stripeAccounts', uid)
            if not record or record.get('accountId') != account['id']:
                return None
            write('stripeAccounts', uid, {**record, **status, 'updatedAt': int(self.clock())})
            return status
        return self.repo.run(save)

    def status(self, uid):
        record = self.record(uid)
        if not record or not record.get('accountId'):
            return {'exists': False, 'ready': False, 'testMode': True, 'platformFeePercent': 0}
        account = self.gateway.account(record['accountId'])
        return self.sync(account) or self.snapshot(account)

    def ready_account(self, uid):
        record = self.record(uid)
        if not record or not record.get('accountId') or not self.status(uid)['ready']:
            raise PaymentError('The bike owner needs to finish Stripe test payment setup in My bikes first.')
        return record['accountId']

    def onboarding(self, uid):
        record = self.ensure(uid)
        result = self.gateway.account_link({
            'account': record['accountId'], 'use_case': {'type': 'account_onboarding', 'account_onboarding': {
                'configurations': ['recipient'], 'refresh_url': self.origin + '/rentals?connect=refresh',
                'return_url': self.origin + '/rentals?connect=return',
            }},
        })
        return {'url': result['url']}

    def dashboard(self, uid):
        record = self.record(uid)
        if not record or not record.get('accountId'):
            raise PaymentError('Set up your owner account first.')
        return {'url': self.gateway.login_link(record['accountId'])['url']}

    def banner(self, uid):
        record = self.record(uid)
        if not record or not record.get('accountId'):
            raise PaymentError('Set up your owner account first.')
        result = self.gateway.account_session({'account': record['accountId'], 'components': {
            'notification_banner': {'enabled': True},
        }})
        return {'clientSecret': result['client_secret']}
