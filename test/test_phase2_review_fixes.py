"""Regression tests for the Phase 2 security-review findings
(#1 concurrency, #2 amounts, #3 addresses, #4 replay of rejected
transactions, #7 signature uniqueness, #8 recipients, #9 malformed input,
#10 balance display)."""
import binascii
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from unittest import TestCase, mock

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

from flask import Flask  # noqa: E402
from sqlalchemy import inspect  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from Crypto.PublicKey import RSA  # noqa: E402
from Crypto.Util.asn1 import DerSequence  # noqa: E402

import blockchain as blockchain_module  # noqa: E402
from app import app as flask_app, blockchain, node_identifier  # noqa: E402
from blockchain import Blockchain  # noqa: E402
from core.crypto import WalletCrypto  # noqa: E402
from core.database import db, TransactionModel  # noqa: E402
from test.test_phase2_transaction_security import Phase2TestCase, wallet  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTL = getattr(blockchain_module, 'TRANSACTION_TTL_SECONDS', 600)


def now():
    return int(blockchain_module.time())


def local_tx(priv, sender, recipient, amount, nonce, expires_at=None):
    """Sign client-side (bypassing /api/sign) and build the request body."""
    expires_at = now() + 60 if expires_at is None else expires_at
    data = WalletCrypto.transaction_payload(sender, recipient, amount, nonce, expires_at)
    from Crypto.Signature import pkcs1_15
    from Crypto.Hash import SHA256
    key = RSA.import_key(binascii.unhexlify(priv))
    signature = binascii.hexlify(pkcs1_15.new(key).sign(SHA256.new(data))).decode()
    return {'sender': sender, 'recipient': recipient, 'amount': amount,
            'signature': signature, 'nonce': nonce, 'expires_at': expires_at}


# ── #1 concurrency ───────────────────────────────────────────────────────────

SERVER_SCRIPT = r'''
import logging, sys
sys.path.insert(0, sys.argv[1])
from app import app
from werkzeug.serving import make_server
logging.getLogger('werkzeug').setLevel(logging.ERROR)
server = make_server('127.0.0.1', 0, app, threaded=True)
print(server.server_port, flush=True)
server.serve_forever()
'''


class TestConcurrentDoubleSpend(TestCase):
    """Real threaded HTTP servers on a file database, as in production."""

    ROUNDS = 3
    SENDERS = 6
    PARALLEL = 6

    @classmethod
    def setUpClass(cls):
        cls.senders = [wallet(f'race-sender-{i}') for i in range(cls.SENDERS)]
        cls.recipients = [wallet(f'race-recipient-{i}')[1] for i in range(cls.PARALLEL)]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='blocknova_race_')
        self.servers = []

    def tearDown(self):
        for proc in self.servers:
            proc.kill()
            proc.wait()
            proc.stdout.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_db(self, name, balance):
        path = os.path.join(self.tmp, name)
        app = Flask(f'race-{name}')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{path}'
        chain = Blockchain(app)
        with app.app_context():
            for _, pub in self.senders:
                chain.new_coinbase_transaction(pub, balance)
            chain.new_block(proof=100)
            db.session.remove()
            db.engine.dispose()
        return path

    def start_server(self, db_path):
        env = {**os.environ, 'DATABASE_URL': f'sqlite:///{db_path}'}
        proc = subprocess.Popen([sys.executable, '-c', SERVER_SCRIPT, PROJECT_ROOT],
                                env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        self.servers.append(proc)
        return f'http://127.0.0.1:{int(proc.stdout.readline())}'

    @staticmethod
    def post(url, body):
        req = urllib.request.Request(url + '/wallet/transactions',
                                     data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            return urllib.request.urlopen(req, timeout=60).status
        except urllib.error.HTTPError as e:
            return e.code

    def fire(self, urls, bodies):
        codes = [None] * len(bodies)
        barrier = threading.Barrier(len(bodies))

        def worker(i):
            barrier.wait()
            codes[i] = self.post(urls[i % len(urls)], bodies[i])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(bodies))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return codes

    def ledger(self, db_path):
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(
                "SELECT sender, COUNT(*), SUM(amount), COUNT(DISTINCT nonce) "
                "FROM transactions WHERE sender != '0' GROUP BY sender").fetchall()
        finally:
            con.close()
        return {sender: (count, total, distinct) for sender, count, total, distinct in rows}

    def run_scenario(self, servers, balance, nonce_for):
        for round_no in range(self.ROUNDS):
            with self.subTest(round=round_no):
                db_path = self.make_db(f'round{round_no}.db', balance)
                urls = [self.start_server(db_path) for _ in range(servers)]
                all_codes = []
                for priv, pub in self.senders:
                    bodies = [local_tx(priv, pub, self.recipients[j], 1, nonce_for(j))
                              for j in range(self.PARALLEL)]
                    all_codes += self.fire(urls, bodies)
                self.assertFalse([c for c in all_codes if c not in (201, 400)], all_codes)
                ledger = self.ledger(db_path)
                for _, pub in self.senders:
                    count, total, distinct = ledger.get(pub, (0, 0, 0))
                    self.assertLessEqual(total or 0, balance)
                    self.assertEqual(count, distinct, 'duplicate nonce stored')
                self.assertEqual(all_codes.count(201), sum(v[0] for v in ledger.values()))

    def test_same_nonce_conflicting_spends_single_server(self):
        self.run_scenario(servers=1, balance=1, nonce_for=lambda j: 1)

    def test_sequential_nonces_cannot_overspend(self):
        self.run_scenario(servers=1, balance=2, nonce_for=lambda j: j + 1)

    def test_same_nonce_conflicting_spends_across_processes(self):
        self.run_scenario(servers=2, balance=1, nonce_for=lambda j: 1)


class TestDatabaseLevelUniqueness(Phase2TestCase):

    def insert(self, **fields):
        with flask_app.app_context():
            db.session.add(TransactionModel(amount=1, recipient=self.bob, **fields))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                raise

    def test_sender_nonce_unique(self):
        self.insert(sender=self.alice, nonce=1, signature='aa')
        with self.assertRaises(IntegrityError):
            self.insert(sender=self.alice, nonce=1, signature='bb')

    def test_signature_unique(self):
        self.insert(sender=self.alice, nonce=1, signature='aa')
        with self.assertRaises(IntegrityError):
            self.insert(sender=self.bob, nonce=1, signature='aa')

    def test_coinbase_rows_do_not_collide(self):
        self.insert(sender='0', nonce=None, signature=None)
        self.insert(sender='0', nonce=None, signature=None)
        self.assertEqual(self.count_txs(sender='0'), 2)


# ── #2 amounts ───────────────────────────────────────────────────────────────

class TestAmountPrecisionAndLimits(Phase2TestCase):

    def assert_amount_rejected(self, amount):
        resp = self.sign(self.alice_priv, self.alice, self.bob, amount)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('amount', resp.get_json()['error'].lower())
        tx = local_tx(self.alice_priv, self.alice, self.bob, 1, 1)
        resp = self.submit_raw('/wallet/transactions', {**tx, 'amount': amount})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('amount', resp.get_json()['error'].lower())

    def test_dust_overspend_from_unfunded_wallet_rejected(self):
        for _ in range(3):
            resp = self.sign(self.alice_priv, self.alice, self.bob, 4e-9)
            self.assertEqual(resp.status_code, 400)
        smallest = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 1e-8))
        self.assertEqual(smallest.status_code, 400)
        self.assertIn('balance', smallest.get_json()['error'].lower())
        self.mine()
        self.assertEqual(self.count_txs(sender=self.alice), 0)
        self.assertEqual(self.balance(self.alice), 0)

    def test_more_than_eight_decimals_rejected(self):
        self.fund(self.alice, 10)
        for amount in (4e-9, 1e-9, 0.123456789, 1e-300, 5e-324):
            with self.subTest(amount=amount):
                self.assert_amount_rejected(amount)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_excessive_amounts_rejected(self):
        limit = getattr(blockchain_module, 'MAX_TRANSACTION_AMOUNT', 10_000_000)
        for amount in (limit + 1, limit + 0.5, 1e15, 2 ** 63, 10 ** 30, 1e308):
            with self.subTest(amount=amount):
                self.assert_amount_rejected(amount)

    def test_invalid_types_and_values_rejected(self):
        for amount in (float('nan'), float('inf'), float('-inf'), -1, -1e-8, 0, -0.0,
                       '1', True, False, None, [], {}):
            with self.subTest(amount=amount):
                self.assert_amount_rejected(amount)

    def test_smallest_unit_and_exact_decimal_accounting(self):
        self.fund(self.alice, 0.1)
        self.fund(self.alice, 0.2)
        resp = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 0.3))
        self.assertEqual(resp.status_code, 201, resp.get_json())
        resp = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 1e-8))
        self.assertEqual(resp.status_code, 400)
        self.fund(self.alice, 1e-8)
        resp = self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 1e-8))
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self.mine()
        self.assertEqual(self.balance(self.alice), 0)


# ── #3 canonical addresses ───────────────────────────────────────────────────

class TestCanonicalAddresses(Phase2TestCase):

    def setUp(self):
        super().setUp()
        self.fund(self.alice, 5)
        key = RSA.import_key(binascii.unhexlify(self.alice))
        self.pkcs1_alias = binascii.hexlify(DerSequence([key.n, key.e]).encode()).decode()

    def aliases(self, address):
        return {'upper': address.upper(), 'mixed': address[:10] + address[10:].upper(),
                'padded': f' {address} ', 'newline': address + '\n'}

    def test_sender_aliases_rejected(self):
        for name, alias in {**self.aliases(self.alice), 'pkcs1': self.pkcs1_alias}.items():
            with self.subTest(alias=name):
                resp = self.sign(self.alice_priv, alias, self.bob, 1)
                self.assertEqual(resp.status_code, 400)
                resp = self.submit(local_tx(self.alice_priv, alias, self.bob, 1, 1))
                self.assertEqual(resp.status_code, 400)
                self.assertIn('sender', resp.get_json()['error'].lower())
        with flask_app.app_context():
            self.assertEqual(TransactionModel.query.filter(
                TransactionModel.sender != '0').count(), 0)

    def test_recipient_aliases_rejected(self):
        for name, alias in self.aliases(self.bob).items():
            with self.subTest(alias=name):
                self.assertEqual(self.sign(self.alice_priv, self.alice, alias, 1).status_code, 400)
                resp = self.submit(local_tx(self.alice_priv, self.alice, alias, 1, 1))
                self.assertEqual(resp.status_code, 400)
                self.assertIn('recipient', resp.get_json()['error'].lower())
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_non_public_key_material_rejected(self):
        small = RSA.generate(1024)
        small_pub = binascii.hexlify(small.publickey().export_key('DER')).decode()
        for name, addr in {'private-key': self.alice_priv, '1024-bit': small_pub,
                           'pem-hex': binascii.hexlify(RSA.import_key(binascii.unhexlify(self.bob)).export_key('PEM')).decode()}.items():
            with self.subTest(addr=name):
                resp = self.submit(local_tx(self.alice_priv, self.alice, addr, 1, 1))
                self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_canonical_address_round_trip(self):
        self.assertEqual(WalletCrypto.canonical_address(self.alice), self.alice)
        self.assertIsNone(WalletCrypto.canonical_address(self.alice.upper()))
        self.assertIsNone(WalletCrypto.canonical_address(self.pkcs1_alias))


# ── #4 / #7 replay of rejected transactions, signature uniqueness ───────────

class TestRejectedTransactionsStayRejected(Phase2TestCase):

    def test_insufficient_balance_rejection_is_final(self):
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        self.assertEqual(self.submit(tx).status_code, 400)
        self.fund(self.alice, 5)
        resp = self.submit(tx)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already submitted', resp.get_json()['error'].lower())
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_future_nonce_rejection_is_final(self):
        self.fund(self.alice, 5)
        early = local_tx(self.alice_priv, self.alice, self.bob, 1, 2)
        self.assertEqual(self.submit(early).status_code, 400)
        self.assertEqual(self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 1)).status_code, 201)
        self.assertEqual(self.submit(early).status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 1)

    def test_expired_transaction_rejected(self):
        self.fund(self.alice, 5)
        resp = self.submit(local_tx(self.alice_priv, self.alice, self.bob, 1, 1, expires_at=now() - 1))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('expired', resp.get_json()['error'].lower())

    def test_unsubmitted_signature_expires(self):
        self.fund(self.alice, 5)
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        later = now() + TTL + 1
        with mock.patch('blockchain.time', return_value=later):
            resp = self.submit(tx)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('expired', resp.get_json()['error'].lower())
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_far_future_expiry_rejected_and_never_becomes_valid(self):
        self.fund(self.alice, 5)
        tx = local_tx(self.alice_priv, self.alice, self.bob, 1, 1, expires_at=now() + TTL * 10)
        self.assertEqual(self.submit(tx).status_code, 400)
        with mock.patch('blockchain.time', return_value=tx['expires_at'] - 5):
            self.assertEqual(self.submit(tx).status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_rejection_records_are_bounded(self):
        from core.database import RejectedSignatureModel
        self.fund(self.alice, 5)
        stale = local_tx(self.alice_priv, self.alice, self.bob, 1, 1, expires_at=now() - 1)
        self.assertEqual(self.submit(stale).status_code, 400)
        burned = local_tx(self.alice_priv, self.alice, self.bob, 1, 2, expires_at=now() + 30)
        huge = local_tx(self.alice_priv, self.alice, self.bob, 1, 2, expires_at=10 ** 40)
        self.assertEqual(self.submit(burned).status_code, 400)
        self.assertEqual(self.submit(huge).status_code, 400)
        with flask_app.app_context():
            self.assertEqual(RejectedSignatureModel.query.count(), 2)
        with mock.patch('blockchain.time', return_value=now() + 31):
            self.assertEqual(self.submit(burned).status_code, 400)
            self.assertEqual(self.submit(local_tx(
                self.alice_priv, self.alice, self.bob, 1, 3, expires_at=now() + 32)).status_code, 400)
        with flask_app.app_context():
            self.assertEqual(RejectedSignatureModel.query.filter_by(
                signature=burned['signature']).count(), 0)
            self.assertEqual(RejectedSignatureModel.query.count(), 2)

    def test_sign_endpoint_returns_bounded_expiry(self):
        signed = self.sign(self.alice_priv, self.alice, self.bob, 1).get_json()
        self.assertIsInstance(signed['expires_at'], int)
        self.assertGreater(signed['expires_at'], now())
        self.assertLessEqual(signed['expires_at'], now() + TTL)

    def test_missing_or_invalid_expiry_rejected(self):
        self.fund(self.alice, 5)
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        for value in (None, '1', 1.5, True, [], {}):
            with self.subTest(expires_at=value):
                self.assertEqual(self.submit({**tx, 'expires_at': value}).status_code, 400)
        missing = {k: v for k, v in tx.items() if k != 'expires_at'}
        self.assertEqual(self.submit(missing).status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_signature_encoding_variants_rejected(self):
        self.fund(self.alice, 5)
        tx = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        sig = tx['signature']
        for name, variant in {'upper': sig.upper(), 'prefixed': '00' + sig,
                              'odd-length': sig[:-1], 'padded': sig + ' '}.items():
            with self.subTest(variant=name):
                resp = self.submit({**tx, 'signature': variant})
                self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.submit(tx).status_code, 201)
        self.assertEqual(self.submit({**tx, 'signature': sig.upper()}).status_code, 400)
        self.assertEqual(self.count_txs(sender=self.alice), 1)


# ── #8 recipients ────────────────────────────────────────────────────────────

class TestRecipientValidation(Phase2TestCase):

    def test_invalid_recipients_rejected(self):
        self.fund(self.alice, 5)
        for recipient in ('0', ' ', 'bob', '<img src=x onerror=alert(1)>',
                          node_identifier, 'ab' * 400, 'a' * 100_000):
            with self.subTest(recipient=recipient[:20]):
                self.assertEqual(self.sign(self.alice_priv, self.alice, recipient, 1).status_code // 100, 4)
                resp = self.submit(local_tx(self.alice_priv, self.alice, recipient, 1, 1))
                self.assertEqual(resp.status_code // 100, 4)
        self.assertEqual(self.count_txs(sender=self.alice), 0)

    def test_oversized_request_body_rejected(self):
        resp = self.submit_raw('/wallet/transactions', {
            'sender': self.alice, 'recipient': 'a' * 1_000_000,
            'amount': 1, 'signature': '00'})
        self.assertEqual(resp.status_code, 413)


# ── #9 malformed requests ────────────────────────────────────────────────────

class TestMalformedRequests(Phase2TestCase):

    ENDPOINTS = ('/api/sign', '/wallet/transactions', '/transactions/new', '/api/balance')
    BODIES = ('"private_key sender recipient amount signature address"',
              '["sender", "recipient", "amount", "signature"]', '42', 'null', 'true',
              '{bad json', '')

    def test_non_object_bodies_return_4xx(self):
        for path in self.ENDPOINTS:
            for body in self.BODIES:
                with self.subTest(path=path, body=body):
                    resp = self.client.post(path, data=body, content_type='application/json')
                    self.assertEqual(resp.status_code, 400)
                    self.assertIn('error', resp.get_json())
            with self.subTest(path=path, body='form'):
                resp = self.client.post(path, data='a=b', content_type='application/x-www-form-urlencoded')
                self.assertIn(resp.status_code, (400, 415))

    def test_invalid_field_types_return_400(self):
        good = self.signed_tx(self.alice_priv, self.alice, self.bob, 1)
        for field in ('sender', 'recipient', 'signature', 'nonce', 'expires_at'):
            for value in ([], {}, 1.5, None):
                with self.subTest(field=field, value=value):
                    resp = self.submit({**good, field: value})
                    self.assertEqual(resp.status_code, 400)
        for value in ([], {}, 1, None):
            with self.subTest(address=value):
                resp = self.client.post('/api/balance', json={'address': value})
                self.assertEqual(resp.status_code, 400)
            with self.subTest(private_key=value):
                resp = self.client.post('/api/sign', json={
                    'private_key': value, 'sender': self.alice, 'recipient': self.bob, 'amount': 1})
                self.assertEqual(resp.status_code, 400)


# ── #10 balance display ──────────────────────────────────────────────────────

class TestBalanceMatchesSpendable(Phase2TestCase):

    def test_pending_spend_reflected_in_balance(self):
        self.fund(self.alice, 1)
        self.assertEqual(self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 1)).status_code, 201)
        for resp in (self.client.post('/api/balance', json={'address': self.alice}),
                     self.client.get('/api/balance/' + self.alice)):
            data = resp.get_json()
            self.assertEqual(data['balance'], 0)
            self.assertEqual(data['confirmed_balance'], 1)
            self.assertEqual(data['pending_sent'], 1)
            with flask_app.app_context():
                self.assertEqual(data['balance'], blockchain.available_balance(self.alice))
        self.assertEqual(self.submit(self.signed_tx(self.alice_priv, self.alice, self.bob, 1)).status_code, 400)
        self.mine()
        data = self.client.get('/api/balance/' + self.alice).get_json()
        self.assertEqual((data['balance'], data['confirmed_balance'], data['pending_sent']), (0, 0, 0))


# ── schema upgrade of an existing database ───────────────────────────────────

class TestLegacyDatabaseUpgrade(TestCase):

    def test_existing_database_is_backed_up_and_upgraded(self):
        tmp = tempfile.mkdtemp(prefix='blocknova_upgrade_')
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, 'legacy.db')
        con = sqlite3.connect(path)
        con.executescript('''
            CREATE TABLE block (id INTEGER PRIMARY KEY, "index" INTEGER NOT NULL UNIQUE,
                timestamp FLOAT NOT NULL, proof INTEGER NOT NULL, previous_hash VARCHAR(64) NOT NULL);
            CREATE TABLE transactions (id INTEGER PRIMARY KEY, sender TEXT NOT NULL,
                recipient TEXT NOT NULL, amount FLOAT NOT NULL, signature TEXT,
                block_id INTEGER REFERENCES block(id));
            INSERT INTO block VALUES (1, 1, 1.0, 100, '1');
            INSERT INTO transactions VALUES (1, '0', 'miner', 1.0, NULL, 1);
            INSERT INTO transactions VALUES (2, 'a', 'b', 1.0, 'dup', 1);
            INSERT INTO transactions VALUES (3, 'a', 'b', 1.0, 'dup', 1);
        ''')
        con.commit()
        con.close()
        with open(path, 'rb') as f:
            original = f.read()

        app = Flask('upgrade')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{path}'
        Blockchain(app)
        with app.app_context():
            columns = {c['name'] for c in inspect(db.engine).get_columns('transactions')}
            self.assertIn('nonce', columns)
            self.assertEqual(TransactionModel.query.count(), 3)
            db.session.remove()
            db.engine.dispose()

        backups = [n for n in os.listdir(tmp) if n.startswith('legacy.db.') and n.endswith('.bak')]
        self.assertEqual(len(backups), 1, os.listdir(tmp))
        with open(os.path.join(tmp, backups[0]), 'rb') as f:
            self.assertEqual(f.read(), original)

        restart = Flask('upgrade-restart')
        restart.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{path}'
        Blockchain(restart)  # second start: nothing left to migrate, no new backup
        self.assertEqual(len([n for n in os.listdir(tmp) if n.endswith('.bak')]), 1)
        with restart.app_context():
            db.engine.dispose()
