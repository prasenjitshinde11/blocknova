import hashlib
import json
from unittest import TestCase

from flask import Flask

from blockchain import Blockchain
from core.crypto import WalletCrypto
from core.database import BlockModel, TransactionModel

TEST_DATABASE_URI = 'sqlite:///:memory:'


def make_test_app():
    """Flask app bound to a private in-memory database, configured before
    Blockchain(app) initialises Flask-SQLAlchemy."""
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = TEST_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    return app


class BlockchainTestCase(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.private_key, cls.public_key = WalletCrypto.generate_key_pair()

    def setUp(self):
        self.app = make_test_app()
        self.blockchain = Blockchain(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def new_blockchain(self):
        return Blockchain(make_test_app())

    def create_block(self, proof=123, previous_hash='abc'):
        self.blockchain.new_block(proof, previous_hash)

    def create_transaction(self, recipient='b', amount=1):
        # Senders need confirmed funds and a nonce-bound signature.
        self.blockchain.new_coinbase_transaction(self.public_key, amount)
        self.blockchain.new_block(proof=100)
        nonce = self.blockchain.next_nonce(self.public_key)
        signature = WalletCrypto.sign_transaction(
            self.private_key, self.public_key, recipient, amount, nonce
        )
        return self.blockchain.new_transaction(
            sender=self.public_key,
            recipient=recipient,
            amount=amount,
            signature=signature
        )

    def pending_transactions(self):
        return TransactionModel.query.filter_by(block_id=None).all()


class TestRegisterNodes(BlockchainTestCase):

    def test_valid_nodes(self):
        blockchain = self.new_blockchain()

        blockchain.register_node('http://192.168.0.1:5000')

        self.assertIn('192.168.0.1:5000', blockchain.nodes)

    def test_malformed_nodes(self):
        blockchain = self.new_blockchain()

        blockchain.register_node('http//192.168.0.1:5000')

        self.assertNotIn('192.168.0.1:5000', blockchain.nodes)

    def test_idempotency(self):
        blockchain = self.new_blockchain()

        blockchain.register_node('http://192.168.0.1:5000')
        blockchain.register_node('http://192.168.0.1:5000')

        self.assertEqual(len(blockchain.nodes), 1)


class TestBlocksAndTransactions(BlockchainTestCase):

    def test_block_creation(self):
        self.create_block()

        latest_block = self.blockchain.last_block

        # The genesis block is create at initialization, so the length should be 2
        assert len(self.blockchain.get_chain()) == 2
        assert latest_block.index == 2
        assert latest_block.timestamp is not None
        assert latest_block.proof == 123
        assert latest_block.previous_hash == 'abc'

    def test_create_transaction(self):
        self.create_transaction()

        transaction = self.pending_transactions()[-1]

        assert transaction
        assert transaction.sender == self.public_key
        assert transaction.recipient == 'b'
        assert transaction.amount == 1

    def test_block_resets_transactions(self):
        self.create_transaction()

        initial_length = len(self.pending_transactions())

        self.create_block()

        current_length = len(self.pending_transactions())

        assert initial_length == 1
        assert current_length == 0

    def test_return_last_block(self):
        self.create_block()

        created_block = self.blockchain.last_block
        stored_blocks = BlockModel.query.order_by(BlockModel.index).all()

        self.assertEqual(len(self.blockchain.get_chain()), 2)
        assert created_block is stored_blocks[-1]


class TestHashingAndProofs(BlockchainTestCase):

    def test_hash_is_correct(self):
        self.create_block()

        new_block = self.blockchain.get_chain()[-1]

        new_block_json = json.dumps(
            new_block,
            sort_keys=True
        ).encode()

        new_hash = hashlib.sha256(
            new_block_json
        ).hexdigest()

        self.assertEqual(
            new_hash,
            self.blockchain.hash(self.blockchain.last_block)
        )

        self.assertEqual(len(new_hash), 64)
