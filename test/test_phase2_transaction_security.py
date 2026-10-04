"""Phase 2 security regression tests: coin creation, balances, replay,
amount validation and signature binding."""
import json
import os
from unittest import TestCase

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

from app import app as flask_app, blockchain, node_identifier  # noqa: E402
from core.crypto import WalletCrypto  # noqa: E402
from core.database import db, TransactionModel  # noqa: E402

_WALLETS = {}


def wallet(name):
    if name not in _WALLETS:
        _WALLETS[name] = WalletCrypto.generate_key_pair()
    return _WALLETS[name]


class Phase2TestCase(TestCase):

    def setUp(self):
        self.client = flask_app.test_client()
        with flask_app.app_context():
            db.drop_all()
            db.create_all()
            blockchain.new_block(proof=100, previous_hash='1')
        self.alice_priv, self.alice = wallet('alice')
        self.bob_priv, self.bob = wallet('bob')

    def fund(self, address, amount):
        """Confirm a mining reward of `amount` to `address`."""
        with flask_app.app_context():
            db.session.add(TransactionModel(sender='0', recipient=address, amount=amount))
            db.session.commit()
            last = blockchain.last_block
            blockchain.new_block(blockchain.proof_of_work(last.proof), blockchain.hash(last))

    def sign(self, priv, sender, recipient, amount):
        return self.client.post('/api/sign', json={
            'private_key': priv, 'sender': sender,
            'recipient': recipient, 'amount': amount,
        })

    def signed_tx(self, priv, sender, recipient, amount):
        """Sign via /api/sign and build the body the frontend submits."""
        resp = self.sign(priv, sender, recipient, amount)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return {'sender': sender, 'recipient': recipient,
                'amount': amount, 'signature': resp.get_json()['signature']}

    def submit(self, tx):
        return self.client.post('/wallet/transactions', json=tx)

    def submit_raw(self, path, body):
        return self.client.post(path, data=json.dumps(body),
                                content_type='application/json')

    def count_txs(self, **filters):
        with flask_app.app_context():
            return TransactionModel.query.filter_by(**filters).count()

    def balance(self, address):
        return self.client.post('/api/balance', json={'address': address}).get_json()['balance']

    def mine(self):
        resp = self.client.get('/mine')
        self.assertEqual(resp.status_code, 200)
        return resp


class TestUnauthorizedCoinCreation(Phase2TestCase):

    def test_legacy_endpoint_rejects_coinbase_sender(self):
        resp = self.client.post('/transactions/new', json={
            'sender': '0', 'recipient': self.bob, 'amount': 1000000})
        self.assertEqual(resp.status_code, 400)
        self.mine()
        self.assertEqual(self.balance(self.bob), 0)
        self.assertEqual(self.count_txs(recipient=self.bob), 0)

    def test_signed_endpoint_rejects_coinbase_sender(self):
        resp = self.submit({'sender': '0', 'recipient': self.bob,
                            'amount': 1000000, 'signature': '00'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.count_txs(recipient=self.bob), 0)

    def test_blockchain_api_rejects_coinbase_sender(self):
        with flask_app.app_context():
            self.assertIsNone(blockchain.new_transaction('0', self.bob, 5))
        self.assertEqual(self.count_txs(recipient=self.bob), 0)

    def test_legacy_endpoint_rejects_unsigned_spend(self):
        self.fund(self.alice, 10)
        resp = self.client.post('/transactions/new', json={
            'sender': self.alice, 'recipient': self.bob, 'amount': 1})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_mining_still_creates_reward(self):
        self.mine()
        with flask_app.app_context():
            block = blockchain.last_block
            rewards = [(t.sender, t.recipient, t.amount) for t in block.transactions]
            self.assertEqual(rewards, [('0', node_identifier, 1)])
            self.assertTrue(blockchain.is_chain_valid())


class TestBalanceEnforcement(Phase2TestCase):

    def test_unfunded_wallet_cannot_spend(self):
        resp = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 50))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('balance', resp.get_json()['error'].lower())
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_cannot_spend_more_than_confirmed_balance(self):
        self.fund(self.alice, 10)
        resp = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 11))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_spend_within_balance_is_accepted(self):
        self.fund(self.alice, 10)
        resp = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 4))
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self.mine()
        self.assertEqual(self.balance(self.alice), 6)
        self.assertEqual(self.balance(self.bob), 4)

    def test_exact_balance_spend_is_accepted(self):
        self.fund(self.alice, 10)
        resp = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 10))
        self.assertEqual(resp.status_code, 201, resp.get_json())

    def test_fractional_spends_up_to_balance_are_accepted(self):
        self.fund(self.alice, 0.3)
        for amount in (0.1, 0.2):
            resp = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, amount))
            self.assertEqual(resp.status_code, 201, resp.get_json())

    def test_pending_spends_count_against_balance(self):
        self.fund(self.alice, 10)
        first = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 6))
        self.assertEqual(first.status_code, 201, first.get_json())
        second = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 6))
        self.assertEqual(second.status_code, 400)
        self.mine()
        self.assertEqual(self.balance(self.alice), 4)

    def test_unconfirmed_incoming_funds_are_not_spendable(self):
        self.fund(self.alice, 10)
        self.assertEqual(
            self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 5)).status_code, 201)
        resp = self.submit(self.signed_tx(self.bob_priv, self.bob, self.alice, 1))
        self.assertEqual(resp.status_code, 400)

    def test_balance_never_goes_negative(self):
        for amount in (50, 50, 50):
            tx = self.signed_tx(self.alice_priv, self.alice, self.bob, amount)
            self.submit(tx)
        self.mine()
        self.assertGreaterEqual(self.balance(self.alice), 0)


class TestReplayProtection(Phase2TestCase):

    def test_identical_signed_transaction_rejected_second_time(self):
        self.fund(self.alice, 10)
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        self.assertEqual(self.submit(tx).status_code, 201)
        self.assertEqual(self.submit(tx).status_code, 400)
        self.assertEqual(self.submit(tx).status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 1)

    def test_replay_after_mining_rejected(self):
        self.fund(self.alice, 10)
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        self.assertEqual(self.submit(tx).status_code, 201)
        self.mine()
        self.assertEqual(self.submit(tx).status_code, 400)
        self.assertEqual(self.balance(self.alice), 9)

    def test_repeated_legitimate_payments_get_distinct_signatures(self):
        self.fund(self.alice, 10)
        first = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        self.assertEqual(self.submit(first).status_code, 201)
        second = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        self.assertNotEqual(first['signature'], second['signature'])
        self.assertEqual(self.submit(second).status_code, 201)
        self.assertEqual(self.count_txs(sender=self.alice), 2)

    def test_explicit_nonce_must_match_next_sender_nonce(self):
        self.fund(self.alice, 10)
        signed = self.sign(self.alice_priv, self.alice, self.bob, 1).get_json()
        self.assertEqual(signed.get('nonce'), 1)
        tx = {'sender': self.alice, 'recipient': self.bob, 'amount': 1,
              'signature': signed['signature']}
        self.assertEqual(self.submit({**tx, 'nonce': 2}).status_code, 400)
        self.assertEqual(self.submit({**tx, 'nonce': 1}).status_code, 201)
        self.assertEqual(self.submit({**tx, 'nonce': 1}).status_code, 400)


class TestAmountValidation(Phase2TestCase):

    BAD_AMOUNTS = [float('nan'), float('inf'), float('-inf'), -5, -0.01, 0, -0.0,
                   '10', True, None, [], {}]

    def signature_or_placeholder(self, amount):
        """Real signature from /api/sign if the server will sign `amount`,
        so rejection cannot come from a bad signature alone."""
        signed = self.submit_raw('/api/sign', {
            'private_key': self.alice_priv, 'sender': self.alice,
            'recipient': self.bob, 'amount': amount})
        return (signed.get_json() or {}).get('signature') or '00'

    def test_signed_endpoint_rejects_invalid_amounts(self):
        self.fund(self.alice, 100)
        for amount in self.BAD_AMOUNTS:
            with self.subTest(amount=amount):
                signature = self.signature_or_placeholder(amount)
                resp = self.submit_raw('/wallet/transactions', {
                    'sender': self.alice, 'recipient': self.bob,
                    'amount': amount, 'signature': signature})
                self.assertEqual(resp.status_code, 400)
                self.assertIn('amount', resp.get_json()['error'].lower())
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_sign_endpoint_rejects_invalid_amounts(self):
        for amount in self.BAD_AMOUNTS:
            with self.subTest(amount=amount):
                resp = self.submit_raw('/api/sign', {
                    'private_key': self.alice_priv, 'sender': self.alice,
                    'recipient': self.bob, 'amount': amount})
                self.assertEqual(resp.status_code, 400)

    def test_blockchain_api_rejects_invalid_amounts(self):
        self.fund(self.alice, 100)
        for amount in self.BAD_AMOUNTS:
            with self.subTest(amount=amount):
                signature = self.signature_or_placeholder(amount)
                with flask_app.app_context():
                    self.assertIsNone(blockchain.new_transaction(
                        self.alice, self.bob, amount, signature=signature))
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_chain_stays_json_serialisable(self):
        self.fund(self.alice, 100)
        for amount in (float('nan'), float('inf')):
            self.submit_raw('/wallet/transactions', {
                'sender': self.alice, 'recipient': self.bob,
                'amount': amount, 'signature': self.signature_or_placeholder(amount)})
        self.mine()
        body = self.client.get('/chain').get_data(as_text=True)
        json.loads(body, parse_constant=lambda c: self.fail(f'non-JSON constant {c}'))


class TestSignatureBinding(Phase2TestCase):

    def setUp(self):
        super().setUp()
        self.fund(self.alice, 20)
        self.fund(self.bob, 20)

    def assert_rejected_signature(self, tx):
        resp = self.submit(tx)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()['error'], 'Invalid cryptographic signature')

    def test_tampered_amount_rejected(self):
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        self.assert_rejected_signature({**tx, 'amount': 5})

    def test_tampered_recipient_rejected(self):
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        self.assert_rejected_signature({**tx, 'recipient': 'attacker'})

    def test_sender_substitution_rejected(self):
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        self.assert_rejected_signature({**tx, 'sender': self.bob, 'recipient': self.alice})
        self.assertEqual(self.count_txs(sender=self.bob), 0)

    def test_ambiguous_field_boundary_rejected(self):
        tx = self.signed_tx(self.alice_priv, self.alice, 'ab', 12)
        self.assert_rejected_signature({**tx, 'recipient': 'ab1', 'amount': 2})
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_sign_endpoint_rejects_key_not_matching_sender(self):
        resp = self.sign(self.alice_priv, self.bob, self.alice, 1)
        self.assertEqual(resp.status_code, 400)

    def test_malformed_signatures_rejected(self):
        for signature in ['', 'zz', '00', 'abc', 123, None, [], {}]:
            with self.subTest(signature=signature):
                resp = self.submit({'sender': self.alice, 'recipient': self.bob,
                                    'amount': 1, 'signature': signature})
                self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_malformed_senders_rejected(self):
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        for sender in ['', 'not-a-key', 'zz', 123, None, [], {}]:
            with self.subTest(sender=sender):
                self.assertEqual(self.submit({**tx, 'sender': sender}).status_code, 400)

    def test_malformed_recipients_rejected(self):
        for recipient in ['', 123, None, [], {}]:
            with self.subTest(recipient=recipient):
                resp = self.submit({'sender': self.alice, 'recipient': recipient,
                                    'amount': 1, 'signature': '00'})
                self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_frontend_flow_still_works(self):
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 2.5)
        resp = self.submit(tx)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self.assertIn('Transaction will be added to Block', resp.get_json()['message'])
        self.mine()
        self.assertEqual(self.balance(self.alice), 17.5)
        self.assertEqual(self.balance(self.bob), 22.5)
