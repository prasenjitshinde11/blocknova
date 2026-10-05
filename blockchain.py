import hashlib
import json
import logging

import requests
from time import time
from urllib.parse import urlparse

from core.database import db, BlockModel, TransactionModel
from core.crypto import WalletCrypto

logger = logging.getLogger(__name__)


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
                "index":         block.index,
                "timestamp":     block.timestamp,
                "proof":         block.proof,
                "previous_hash": block.previous_hash,
                "transactions": [
                    {
                        "sender":    tx.sender,
                        "recipient": tx.recipient,
                        "amount":    tx.amount
                    }
                    for tx in block.transactions
                ]
            })

        return chain


    def new_block(self, proof, previous_hash=None):
        last_block = BlockModel.query.order_by(
            BlockModel.index.desc()
        ).first()

        index  = 1 if not last_block else last_block.index + 1
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

        unmined = TransactionModel.query.filter_by(block_id=None).all()
        for tx in unmined:
            tx.block_id = block.id

        db.session.commit()

        return block

    def new_transaction(self, sender, recipient, amount, signature=None):
      
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            logger.warning("new_transaction: invalid amount %r", amount)
            return None

        if amount <= 0:
            logger.warning("new_transaction: non-positive amount %s rejected", amount)
            return None
s
        if sender != "0":
            if not WalletCrypto.verify_signature(
                sender, recipient, amount, signature
            ):
                logger.warning("new_transaction: invalid signature for sender %s…", sender[:16])
                return None

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
    def hash(block):
        """Return the SHA-256 hex digest of a BlockModel instance."""
        txs = [
            {
                "sender":    tx.sender,
                "recipient": tx.recipient,
                "amount":    tx.amount
            }
            for tx in block.transactions
        ]

        block_data = {
            "index":         block.index,
            "timestamp":     block.timestamp,
            "proof":         block.proof,
            "previous_hash": block.previous_hash,
            "transactions":  txs
        }

        block_string = json.dumps(block_data, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()

    def proof_of_work(self, last_proof):
        proof = 0
        while not self.valid_proof(last_proof, proof):
            proof += 1
        return proof

    @staticmethod
    def valid_proof(last_proof, proof):
        guess      = f"{last_proof}{proof}".encode()
        guess_hash = hashlib.sha256(guess).hexdigest()
        return guess_hash[:4] == "0000"


    def register_node(self, address):
        parsed_url = urlparse(address)
        if parsed_url.netloc:
            self.nodes.add(parsed_url.netloc)
        elif parsed_url.path:
            self.nodes.add(parsed_url.path)

    def resolve_conflicts(self):
        max_length = BlockModel.query.count()
        new_chain  = None

        for node in self.nodes:
            try:
                resp = requests.get(f'http://{node}/chain', timeout=5)
                if resp.status_code != 200:
                    continue

                data   = resp.json()
                length = data.get('length', 0)
                chain  = data.get('chain', [])

                if length > max_length and self._is_valid_chain(chain):
                    max_length = length
                    new_chain  = chain

            except requests.RequestException as exc:
                logger.warning("resolve_conflicts: could not reach node %s — %s", node, exc)
                continue

        if new_chain:
            self._replace_chain(new_chain)
            return True

        return False

    @staticmethod
    def _is_valid_chain(chain):
        if not chain:
            return False

        for i in range(1, len(chain)):
            prev = chain[i - 1]
            curr = chain[i]

            prev_data = {
                "index":         prev["index"],
                "timestamp":     prev["timestamp"],
                "proof":         prev["proof"],
                "previous_hash": prev["previous_hash"],
                "transactions":  prev["transactions"],
            }
            prev_hash = hashlib.sha256(
                json.dumps(prev_data, sort_keys=True).encode()
            ).hexdigest()

            if curr["previous_hash"] != prev_hash:
                return False


            guess = f"{prev['proof']}{curr['proof']}".encode()
            if hashlib.sha256(guess).hexdigest()[:4] != "0000":
                return False

        return True

    def _replace_chain(self, chain):
        TransactionModel.query.delete()
        BlockModel.query.delete()
        db.session.commit()

        for block_data in chain:
            block = BlockModel(
                index=block_data["index"],
                timestamp=block_data["timestamp"],
                proof=block_data["proof"],
                previous_hash=block_data["previous_hash"],
            )
            db.session.add(block)
            db.session.flush() 

            for tx_data in block_data.get("transactions", []):
                tx = TransactionModel(
                    sender=tx_data["sender"],
                    recipient=tx_data["recipient"],
                    amount=tx_data["amount"],
                    block_id=block.id,
                )
                db.session.add(tx)

        db.session.commit()
        logger.info("_replace_chain: local chain replaced with %d blocks", len(chain))

    def is_chain_valid(self):
        """Verify the integrity of the stored blockchain."""
        blocks = BlockModel.query.order_by(BlockModel.index).all()
        for i in range(1, len(blocks)):
            prev = blocks[i - 1]
            curr = blocks[i]
            if curr.previous_hash != self.hash(prev):
                return False
            guess      = f"{prev.proof}{curr.proof}".encode()
            guess_hash = hashlib.sha256(guess).hexdigest()
            if guess_hash[:4] != "0000":
                return False
        return True