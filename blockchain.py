import hashlib
import json
import math

from time import time
from urllib.parse import urlparse

from core.database import db, BlockModel, TransactionModel
from core.crypto import WalletCrypto


COINBASE_SENDER = "0"


class TransactionError(ValueError):
    """A submitted transaction was rejected."""


class Blockchain:

    def __init__(self, app):
        self.nodes = set()

        db.init_app(app)

        with app.app_context():
            db.create_all()

            if BlockModel.query.count() == 0:
                self.new_block(proof=100, previous_hash='1')

    @property
    def last_block(self):
        return BlockModel.query.order_by(
            BlockModel.index.desc()
        ).first()

    def get_chain(self):
        blocks = BlockModel.query.order_by(BlockModel.index).all()

        chain = []

        for block in blocks:
            chain.append({
                "index": block.index,
                "timestamp": block.timestamp,
                "proof": block.proof,
                "previous_hash": block.previous_hash,
                "transactions": [
                    {
                        "sender": tx.sender,
                        "recipient": tx.recipient,
                        "amount": tx.amount
                    }
                    for tx in block.transactions
                ]
            })

        return chain

    def new_block(self, proof, previous_hash=None):

        last_block = BlockModel.query.order_by(
            BlockModel.index.desc()
        ).first()

        index = 1 if not last_block else last_block.index + 1

        p_hash = previous_hash or (
            self.hash(last_block) if last_block else "1"
        )

        block = BlockModel(
            index=index,
            timestamp=time(),
            proof=proof,
            previous_hash=p_hash
        )

        db.session.add(block)
        db.session.commit()

        unmined_transactions = TransactionModel.query.filter_by(
            block_id=None
        ).all()

        for tx in unmined_transactions:
            tx.block_id = block.id

        db.session.commit()

        return block

    def new_transaction(
        self,
        sender,
        recipient,
        amount,
        signature=None,
        nonce=None
    ):
        """Add a user transaction; returns the target block index, or None
        if the transaction is rejected."""
        try:
            return self.add_transaction(
                sender, recipient, amount, signature, nonce
            )
        except TransactionError:
            return None

    def add_transaction(
        self,
        sender,
        recipient,
        amount,
        signature=None,
        nonce=None
    ):
        """Validate and store a signed user transaction.
        Raises TransactionError with the reason when rejected."""

        if sender == COINBASE_SENDER:
            raise TransactionError(
                "Coinbase transactions can only be created by mining"
            )

        amount = self.validate_transaction_fields(sender, recipient, amount)

        if not isinstance(signature, str) or not signature:
            raise TransactionError("Invalid cryptographic signature")

        if TransactionModel.query.filter_by(signature=signature).first():
            raise TransactionError("Duplicate transaction: already submitted")

        expected_nonce = self.next_nonce(sender)
        if nonce is None:
            nonce = expected_nonce
        elif (isinstance(nonce, bool) or not isinstance(nonce, int)
              or nonce != expected_nonce):
            raise TransactionError(
                f"Invalid nonce: expected {expected_nonce}"
            )

        if not WalletCrypto.verify_signature(
            sender,
            recipient,
            amount,
            signature,
            nonce
        ):
            raise TransactionError("Invalid cryptographic signature")

        if round(self.available_balance(sender) - amount, 8) < 0:
            raise TransactionError("Insufficient balance")

        return self._store_transaction(sender, recipient, amount, signature)

    def new_coinbase_transaction(self, recipient, amount):
        """Mining reward; the only way new coins are created."""
        amount = self.validate_amount(amount)
        return self._store_transaction(COINBASE_SENDER, recipient, amount, None)

    def _store_transaction(self, sender, recipient, amount, signature):
        tx = TransactionModel(
            sender=sender,
            recipient=recipient,
            amount=amount,
            signature=signature
        )

        db.session.add(tx)
        db.session.commit()

        return self.last_block.index + 1

    @staticmethod
    def validate_amount(amount):
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise TransactionError("Invalid amount: must be a number")
        try:
            amount = float(amount)
        except OverflowError:
            raise TransactionError("Invalid amount: too large")
        if not math.isfinite(amount) or amount <= 0:
            raise TransactionError(
                "Invalid amount: must be a finite number greater than 0"
            )
        return amount

    def validate_transaction_fields(self, sender, recipient, amount):
        """Check sender/recipient/amount; returns the normalised amount."""
        for name, value in (('sender', sender), ('recipient', recipient)):
            if not isinstance(value, str) or not value:
                raise TransactionError(f"Invalid {name}")
        return self.validate_amount(amount)

    def next_nonce(self, sender):
        """Per-sender sequence number expected for the next transaction."""
        return TransactionModel.query.filter_by(sender=sender).count() + 1

    @staticmethod
    def _sum_amounts(*criteria):
        return db.session.query(
            db.func.sum(TransactionModel.amount)
        ).filter(*criteria).scalar() or 0.0

    def available_balance(self, address):
        """Confirmed received minus confirmed and pending sent."""
        received = self._sum_amounts(
            TransactionModel.recipient == address,
            TransactionModel.block_id.isnot(None)
        )
        sent = self._sum_amounts(TransactionModel.sender == address)
        return round(received - sent, 8)

    @staticmethod
    def hash(block):

        txs = [
            {
                "sender": tx.sender,
                "recipient": tx.recipient,
                "amount": tx.amount
            }
            for tx in block.transactions
        ]

        block_data = {
            "index": block.index,
            "timestamp": block.timestamp,
            "proof": block.proof,
            "previous_hash": block.previous_hash,
            "transactions": txs
        }

        block_string = json.dumps(
            block_data,
            sort_keys=True
        ).encode()

        return hashlib.sha256(block_string).hexdigest()

    def proof_of_work(self, last_proof):

        proof = 0

        while not self.valid_proof(
            last_proof,
            proof
        ):
            proof += 1

        return proof

    @staticmethod
    def valid_proof(last_proof, proof):

        guess = f"{last_proof}{proof}".encode()

        guess_hash = hashlib.sha256(
            guess
        ).hexdigest()

        return guess_hash[:4] == "0000"

    def register_node(self, address):

        parsed_url = urlparse(address)

        if parsed_url.netloc:
            self.nodes.add(parsed_url.netloc)

        elif parsed_url.path:
            self.nodes.add(parsed_url.path)

    def resolve_conflicts(self):
        return False

    def is_chain_valid(self):
        """Verify the integrity of the stored blockchain."""
        blocks = BlockModel.query.order_by(BlockModel.index).all()
        for i in range(1, len(blocks)):
            prev = blocks[i - 1]
            curr = blocks[i]
            if curr.previous_hash != self.hash(prev):
                return False
            guess = f"{prev.proof}{curr.proof}".encode()
            guess_hash = hashlib.sha256(guess).hexdigest()
            if guess_hash[:4] != "0000":
                return False
        return True