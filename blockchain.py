import hashlib
import json
import math
import os
import shutil
import threading

from contextlib import contextmanager
from decimal import Decimal
from time import time
from urllib.parse import urlparse

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

from core.database import db, BlockModel, TransactionModel, RejectedSignatureModel
from core.crypto import WalletCrypto


COINBASE_SENDER = "0"
AMOUNT_DECIMALS = 8
UNITS_PER_COIN = 10 ** AMOUNT_DECIMALS
# Keeps every 8-decimal amount exactly representable in the float column.
MAX_TRANSACTION_AMOUNT = 10_000_000
TRANSACTION_TTL_SECONDS = 600
MAX_STORED_EXPIRY = 2 ** 62

_write_lock = threading.Lock()


class TransactionError(ValueError):
    """A submitted transaction was rejected."""
    status_code = 400


class TransactionBusyError(TransactionError):
    """The database was locked by another writer; the client may retry."""
    status_code = 503


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class Blockchain:

    def __init__(self, app):
        self.nodes = set()

        db.init_app(app)

        with app.app_context():
            self._upgrade_schema()
            db.create_all()
            for index in TransactionModel.__table__.indexes:
                index.create(bind=db.engine, checkfirst=True)

            if BlockModel.query.count() == 0:
                self.new_block(proof=100, previous_hash='1')

    @staticmethod
    def _upgrade_schema():
        """Add the nonce column to databases created before it existed,
        backing up a SQLite database file first."""
        engine = db.engine
        inspector = inspect(engine)
        if not inspector.has_table('transactions'):
            return
        if 'nonce' in {c['name'] for c in inspector.get_columns('transactions')}:
            return
        path = engine.url.database
        if engine.dialect.name == 'sqlite' and path and os.path.isfile(path):
            shutil.copy2(path, f"{path}.{int(time())}.bak")
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE transactions ADD COLUMN nonce INTEGER'))

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
        nonce=None,
        expires_at=None
    ):
        """Add a user transaction; returns the target block index, or None
        if the transaction is rejected."""
        try:
            return self.add_transaction(
                sender, recipient, amount, signature, nonce, expires_at
            )
        except TransactionError:
            return None

    def add_transaction(
        self,
        sender,
        recipient,
        amount,
        signature=None,
        nonce=None,
        expires_at=None
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

        if nonce is not None and not _is_int(nonce):
            raise TransactionError("Invalid nonce: must be an integer")

        if not _is_int(expires_at):
            raise TransactionError(
                "Invalid expires_at: must be an integer Unix timestamp"
            )

        with self._write_transaction():
            if self.signature_seen(signature):
                raise TransactionError(
                    "Duplicate transaction: already submitted"
                )

            expected_nonce = self.next_nonce(sender)
            if nonce is None:
                nonce = expected_nonce

            if not WalletCrypto.verify_signature(
                sender,
                recipient,
                amount,
                signature,
                nonce,
                expires_at
            ):
                raise TransactionError("Invalid cryptographic signature")

            reason = self._rejection_reason(
                sender, amount, nonce, expected_nonce, expires_at
            )
            if reason:
                self._burn_signature(signature, expires_at)
                raise TransactionError(reason)

            return self._store_transaction(
                sender, recipient, amount, signature, nonce, commit=False
            )

    def _rejection_reason(self, sender, amount, nonce, expected_nonce, expires_at):
        if nonce != expected_nonce:
            return f"Invalid nonce: expected {expected_nonce}"
        now = int(time())
        if expires_at <= now:
            return "Transaction expired"
        if expires_at > now + TRANSACTION_TTL_SECONDS:
            return (
                "Invalid expires_at: must be within "
                f"{TRANSACTION_TTL_SECONDS} seconds"
            )
        if self.to_units(amount) > self.available_units(sender):
            return "Insufficient balance"
        return None

    @staticmethod
    def _burn_signature(signature, expires_at):
        """Ensure an authentic but rejected transaction can never become
        valid later (e.g. once the sender is funded)."""
        now = int(time())
        RejectedSignatureModel.query.filter(
            RejectedSignatureModel.expires_at <= now
        ).delete(synchronize_session=False)
        if expires_at > now:
            db.session.add(RejectedSignatureModel(
                signature=signature,
                expires_at=min(expires_at, MAX_STORED_EXPIRY)
            ))
        db.session.commit()

    @contextmanager
    def _write_transaction(self):
        """Run validation and the write as one serialised DB transaction."""
        with _write_lock:
            try:
                conn = db.session.connection()
                if (conn.dialect.name == 'sqlite'
                        and not conn.connection.dbapi_connection.in_transaction):
                    conn.exec_driver_sql('BEGIN IMMEDIATE')
                yield
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                raise TransactionError(
                    "Duplicate transaction: already submitted"
                )
            except OperationalError as e:
                db.session.rollback()
                if 'locked' in str(e):
                    raise TransactionBusyError("Database busy, please retry")
                raise
            except BaseException:
                db.session.rollback()
                raise

    @staticmethod
    def signature_seen(signature):
        return (
            TransactionModel.query.filter_by(signature=signature).first()
            is not None
            or RejectedSignatureModel.query.filter_by(signature=signature).first()
            is not None
        )

    def new_coinbase_transaction(self, recipient, amount):
        """Mining reward; the only way new coins are created."""
        amount = self.validate_amount(amount)
        return self._store_transaction(COINBASE_SENDER, recipient, amount, None)

    def _store_transaction(self, sender, recipient, amount, signature,
                           nonce=None, commit=True):
        tx = TransactionModel(
            sender=sender,
            recipient=recipient,
            amount=amount,
            signature=signature,
            nonce=nonce
        )

        db.session.add(tx)
        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return self.last_block.index + 1

    @staticmethod
    def validate_amount(amount):
        invalid = "Invalid amount: must be a finite number greater than 0"
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise TransactionError("Invalid amount: must be a number")
        if isinstance(amount, float) and not math.isfinite(amount):
            raise TransactionError(invalid)
        if amount <= 0:
            raise TransactionError(invalid)
        if amount > MAX_TRANSACTION_AMOUNT:
            raise TransactionError(
                f"Invalid amount: must not exceed {MAX_TRANSACTION_AMOUNT}"
            )
        amount = float(amount)
        if Decimal(repr(amount)).as_tuple().exponent < -AMOUNT_DECIMALS:
            raise TransactionError(
                f"Invalid amount: at most {AMOUNT_DECIMALS} decimal places"
            )
        return amount

    @staticmethod
    def to_units(amount):
        """Exact integer number of 10^-8 units in a validated amount."""
        return int(Decimal(repr(float(amount))) * UNITS_PER_COIN)

    def validate_transaction_fields(self, sender, recipient, amount):
        """Check sender/recipient/amount; returns the normalised amount."""
        for name, value in (('sender', sender), ('recipient', recipient)):
            if WalletCrypto.canonical_address(value) is None:
                raise TransactionError(
                    f"Invalid {name}: must be a canonical public-key address"
                )
        return self.validate_amount(amount)

    @staticmethod
    def new_expiry():
        return int(time()) + TRANSACTION_TTL_SECONDS

    def next_nonce(self, sender):
        """Per-sender sequence number expected for the next transaction."""
        current = db.session.query(
            db.func.max(TransactionModel.nonce)
        ).filter(TransactionModel.sender == sender).scalar()
        return (current or 0) + 1

    @staticmethod
    def _sum_units(*criteria):
        units = db.cast(
            db.func.round(TransactionModel.amount * UNITS_PER_COIN), db.Integer
        )
        return int(
            db.session.query(db.func.sum(units)).filter(*criteria).scalar() or 0
        )

    def balance_units(self, address):
        """(confirmed received, confirmed sent, pending sent) in units."""
        confirmed = TransactionModel.block_id.isnot(None)
        pending = TransactionModel.block_id.is_(None)
        return (
            self._sum_units(TransactionModel.recipient == address, confirmed),
            self._sum_units(TransactionModel.sender == address, confirmed),
            self._sum_units(TransactionModel.sender == address, pending),
        )

    def available_units(self, address):
        received, sent, pending_sent = self.balance_units(address)
        return received - sent - pending_sent

    def available_balance(self, address):
        """Confirmed received minus confirmed and pending sent."""
        return self.available_units(address) / UNITS_PER_COIN

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