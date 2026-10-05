import hashlib
import json
from unittest import TestCase

from app import app as flask_app
from blockchain import Blockchain
from core.crypto import WalletCrypto
from core.database import db as _db


def make_test_app():
    flask_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
    })
    return flask_app


class BlockchainTestCase(TestCase):

    def setUp(self):
        self.app = make_test_app()
        self.client = self.app.test_client()

        with self.app.app_context():
            _db.drop_all()
            _db.create_all()
            # Re-initialise blockchain so genesis block is written to the
            # fresh in-memory database.
            self.blockchain = Blockchain.__new__(Blockchain)
            self.blockchain.nodes = set()
            self.blockchain.new_block(proof=100, previous_hash='1')

    def tearDown(self):
        with self.app.app_context():
            _db.session.remove()
            _db.drop_all()



class TestRegisterNodes(BlockchainTestCase):

    def test_valid_nodes(self):
        self.blockchain.register_node('http://192.168.0.1:5000')
        self.assertIn('192.168.0.1:5000', self.blockchain.nodes)

    def test_malformed_nodes(self):
        """A malformed URL (missing '//') should not register a node."""
        self.blockchain.register_node('http//192.168.0.1:5000')
        self.assertNotIn('192.168.0.1:5000', self.blockchain.nodes)

    def test_idempotency(self):
        """Registering the same node twice should only add it once."""
        self.blockchain.register_node('http://192.168.0.1:5000')
        self.blockchain.register_node('http://192.168.0.1:5000')
        self.assertEqual(len(self.blockchain.nodes), 1)



class TestBlocksAndTransactions(BlockchainTestCase):

    def test_block_creation(self):
        with self.app.app_context():
            self.blockchain.new_block(proof=123, previous_hash='abc')
            last = self.blockchain.last_block

        # Genesis block is created in setUp, so we now have 2 blocks.
        with self.app.app_context():
            from core.database import BlockModel
            count = BlockModel.query.count()

        self.assertEqual(count, 2)
        self.assertEqual(last.index, 2)
        self.assertIsNotNone(last.timestamp)
        self.assertEqual(last.proof, 123)
        self.assertEqual(last.previous_hash, 'abc')

    def test_create_transaction_coinbase(self):
        """Coinbase transactions (sender='0') bypass signature verification."""
        with self.app.app_context():
            index = self.blockchain.new_transaction(
                sender='0', recipient='miner_address', amount=1
            )
        self.assertIsNotNone(index)
        self.assertEqual(index, 2)  # will go into block 2

    def test_create_signed_transaction(self):
        """A properly signed transaction should be accepted."""
        private_key, public_key = WalletCrypto.generate_key_pair()
        recipient_priv, recipient_pub = WalletCrypto.generate_key_pair()
        amount = 5.0
        signature = WalletCrypto.sign_transaction(private_key, public_key, recipient_pub, amount)

        with self.app.app_context():
            index = self.blockchain.new_transaction(
                sender=public_key,
                recipient=recipient_pub,
                amount=amount,
                signature=signature,
            )
        self.assertIsNotNone(index)

    def test_invalid_signature_rejected(self):
        """A transaction with a bad signature must be rejected (return None)."""
        _, public_key = WalletCrypto.generate_key_pair()
        with self.app.app_context():
            result = self.blockchain.new_transaction(
                sender=public_key,
                recipient='someone',
                amount=1.0,
                signature='deadbeef',
            )
        self.assertIsNone(result)

    def test_zero_amount_rejected(self):
        """Bug #17: zero-amount transactions must be rejected."""
        with self.app.app_context():
            result = self.blockchain.new_transaction(
                sender='0', recipient='anyone', amount=0
            )
        self.assertIsNone(result)

    def test_negative_amount_rejected(self):
        """Bug #17: negative-amount transactions must be rejected."""
        with self.app.app_context():
            result = self.blockchain.new_transaction(
                sender='0', recipient='anyone', amount=-5
            )
        self.assertIsNone(result)

    def test_block_mines_pending_transactions(self):
        """Mining a block should assign pending transactions to it."""
        with self.app.app_context():
            self.blockchain.new_transaction(sender='0', recipient='wallet_a', amount=1)
            block = self.blockchain.new_block(proof=200, previous_hash='xyz')
            # The coinbase tx should now be linked to this block
            self.assertTrue(len(block.transactions) > 0)


class TestHashingAndProofs(BlockchainTestCase):
    # Bug #16 fix: removed the extra leading space before `def` that caused
    # an indentation inconsistency warning.

    def test_hash_is_correct(self):
        """hash() must produce the same digest as a manual SHA-256 over the block data."""
        with self.app.app_context():
            block = self.blockchain.new_block(proof=999, previous_hash='testhash')

            txs = [
                {"sender": tx.sender, "recipient": tx.recipient, "amount": tx.amount}
                for tx in block.transactions
            ]
            block_data = {
                "index":         block.index,
                "timestamp":     block.timestamp,
                "proof":         block.proof,
                "previous_hash": block.previous_hash,
                "transactions":  txs,
            }
            expected = hashlib.sha256(
                json.dumps(block_data, sort_keys=True).encode()
            ).hexdigest()

            self.assertEqual(Blockchain.hash(block), expected)
            self.assertEqual(len(expected), 64)

    def test_valid_proof(self):
        """valid_proof() must return True only when the hash starts with '0000'."""
        last_proof = 100
        proof = 0
        while not Blockchain.valid_proof(last_proof, proof):
            proof += 1
        self.assertTrue(Blockchain.valid_proof(last_proof, proof))

    def test_proof_of_work_satisfies_difficulty(self):
        """proof_of_work() must return a proof that satisfies valid_proof()."""
        with self.app.app_context():
            last_proof = self.blockchain.last_block.proof
            proof = self.blockchain.proof_of_work(last_proof)
        self.assertTrue(Blockchain.valid_proof(last_proof, proof))

    def test_chain_validity(self):
        """is_chain_valid() must return True for an untampered chain."""
        with self.app.app_context():
            self.assertTrue(self.blockchain.is_chain_valid())



class TestWalletCrypto(TestCase):

    def test_generate_key_pair(self):
        priv, pub = WalletCrypto.generate_key_pair()
        self.assertIsInstance(priv, str)
        self.assertIsInstance(pub, str)
        self.assertGreater(len(priv), 0)
        self.assertGreater(len(pub), 0)

    def test_sign_and_verify_roundtrip(self):
        """A signature produced by sign_transaction must pass verify_signature."""
        priv, pub = WalletCrypto.generate_key_pair()
        _, recipient_pub = WalletCrypto.generate_key_pair()
        amount = 3.14

        sig = WalletCrypto.sign_transaction(priv, pub, recipient_pub, amount)
        self.assertIsNotNone(sig)
        self.assertTrue(WalletCrypto.verify_signature(pub, recipient_pub, amount, sig))

    def test_verify_rejects_wrong_signature(self):
        _, pub = WalletCrypto.generate_key_pair()
        self.assertFalse(WalletCrypto.verify_signature(pub, 'recipient', 1.0, 'badhex'))

    def test_verify_rejects_none_signature(self):
        """Bug #9: verify_signature must return False, not raise, for None signature."""
        _, pub = WalletCrypto.generate_key_pair()
        self.assertFalse(WalletCrypto.verify_signature(pub, 'recipient', 1.0, None))

    def test_amount_normalisation(self):
        """Bug #1: sign and verify must agree regardless of float precision."""
        priv, pub = WalletCrypto.generate_key_pair()
        _, recip = WalletCrypto.generate_key_pair()

        # Sign with an integer-like float
        sig = WalletCrypto.sign_transaction(priv, pub, recip, 10)
        # Verify with a float that has the same value but different literal form
        self.assertTrue(WalletCrypto.verify_signature(pub, recip, 10.0, sig))
        self.assertTrue(WalletCrypto.verify_signature(pub, recip, 10.00000000, sig))



class TestRoutes(BlockchainTestCase):

    def test_health(self):
        resp = self.client.get('/api/health')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'ok')

    def test_chain(self):
        resp = self.client.get('/chain')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('chain', data)
        self.assertGreaterEqual(data['length'], 1)

    def test_stats(self):
        resp = self.client.get('/api/stats')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('total_blocks', data)
        self.assertIn('chain_valid', data)

    def test_create_wallet(self):
        resp = self.client.get('/wallet/create')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('public_key', data)
        self.assertIn('private_key', data)

    def test_unsigned_transaction_missing_fields(self):
        resp = self.client.post(
            '/transactions/new',
            json={'sender': 'a', 'recipient': 'b'},
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_unsigned_transaction_zero_amount(self):
        """Bug #2: endpoint must return 400 when new_transaction() returns None."""
        resp = self.client.post(
            '/transactions/new',
            json={'sender': '0', 'recipient': 'miner', 'amount': 0},
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_mine_endpoint(self):
        resp = self.client.get('/mine')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('block', data)
        self.assertEqual(data['block']['index'], 2)

    def test_register_nodes(self):
        resp = self.client.post(
            '/nodes/register',
            json={'nodes': ['http://192.168.1.1:5000']},
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 201)

    def test_balance_post(self):
        resp = self.client.post(
            '/api/balance',
            json={'address': 'some_wallet_address'},
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('balance', data)