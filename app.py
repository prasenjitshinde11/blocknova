from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from uuid import uuid4

from blockchain import Blockchain
from core.crypto import WalletCrypto
from core.database import db, BlockModel, TransactionModel

app = Flask(__name__,
            template_folder='frontend/templates',
            static_folder='frontend/static')

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blockchain.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app)

node_identifier = str(uuid4()).replace('-', '')

blockchain = Blockchain(app)


# ── Home ──────────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def home():
    return render_template("index.html")


# ── Health ────────────────────────────────────────────────────────────────────

@app.route('/api/health', methods=['GET'])
def health():
    """Simple liveness probe so the UI status panel works."""
    return jsonify({
        "status": "ok",
        "node": node_identifier
    }), 200


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.route('/api/stats', methods=['GET'])
def stats():
    """Return aggregate chain statistics."""
    with app.app_context():
        total_blocks = BlockModel.query.count()
        total_transactions = TransactionModel.query.count()

        # Unique wallet addresses (senders + recipients, excluding coinbase)
        senders = db.session.query(TransactionModel.sender).filter(
            TransactionModel.sender != "0"
        ).distinct()
        recipients = db.session.query(TransactionModel.recipient).distinct()

        unique_wallets = set()
        for (addr,) in senders:
            unique_wallets.add(addr)
        for (addr,) in recipients:
            unique_wallets.add(addr)

        chain_valid = blockchain.is_chain_valid()

    return jsonify({
        "total_blocks": total_blocks,
        "total_transactions": total_transactions,
        "unique_wallets": len(unique_wallets),
        "chain_valid": chain_valid
    }), 200


# ── Balance ───────────────────────────────────────────────────────────────────

@app.route('/api/balance/<path:public_key>', methods=['GET'])
def get_balance(public_key):
    """Return the net balance for a wallet address."""
    with app.app_context():
        sent = db.session.query(
            db.func.sum(TransactionModel.amount)
        ).filter(
            TransactionModel.sender == public_key,
            TransactionModel.block_id.isnot(None)
        ).scalar() or 0.0

        received = db.session.query(
            db.func.sum(TransactionModel.amount)
        ).filter(
            TransactionModel.recipient == public_key,
            TransactionModel.block_id.isnot(None)
        ).scalar() or 0.0

    balance = round(received - sent, 8)
    return jsonify({
        "address": public_key,
        "balance": balance,
        "received": round(received, 8),
        "sent": round(sent, 8)
    }), 200


# ── Sign ──────────────────────────────────────────────────────────────────────

@app.route('/api/sign', methods=['POST'])
def sign_transaction():
    """
    Sign a transaction payload server-side using the provided private key.
    Accepts: { private_key, sender, recipient, amount }
    Returns: { signature }
    """
    values = request.get_json()
    required = ['private_key', 'sender', 'recipient', 'amount']

    if not values:
        return jsonify({"error": "Invalid JSON"}), 400

    if not all(k in values for k in required):
        return jsonify({"error": "Missing values"}), 400

    signature = WalletCrypto.sign_transaction(
        values['private_key'],
        values['sender'],
        values['recipient'],
        values['amount']
    )

    if signature is None:
        return jsonify({"error": "Could not sign transaction — invalid private key"}), 400

    return jsonify({"signature": signature}), 200


# ── Mine ──────────────────────────────────────────────────────────────────────

@app.route('/mine', methods=['GET'])
def mine():
    last_block = blockchain.last_block
    last_proof = last_block.proof

    proof = blockchain.proof_of_work(last_proof)

    blockchain.new_transaction(
        sender="0",
        recipient=node_identifier,
        amount=1
    )

    previous_hash = blockchain.hash(last_block)
    block = blockchain.new_block(proof, previous_hash)

    block_hash = blockchain.hash(block)

    return jsonify({
        "message": "New Block Forged",
        # Wrap in `block` key so the JS can do `data.block.index` etc.
        "block": {
            "index": block.index,
            "proof": block.proof,
            "previous_hash": block.previous_hash,
            "hash": block_hash,
            "timestamp": block.timestamp
        }
    }), 200


# ── Chain ─────────────────────────────────────────────────────────────────────

@app.route('/chain', methods=['GET'])
def full_chain():
    chain = blockchain.get_chain()
    return jsonify({
        "chain": chain,
        "length": len(chain)
    }), 200


# ── Transactions (unsigned, legacy) ──────────────────────────────────────────

@app.route('/transactions/new', methods=['POST'])
def new_transaction():
    values = request.get_json()
    required = ['sender', 'recipient', 'amount']

    if not values:
        return jsonify({"error": "Invalid JSON"}), 400

    if not all(k in values for k in required):
        return jsonify({"error": "Missing values"}), 400

    index = blockchain.new_transaction(
        values['sender'],
        values['recipient'],
        values['amount']
    )

    return jsonify({
        "message": f"Transaction will be added to Block {index}"
    }), 201


# ── Transactions (signed, primary) ───────────────────────────────────────────

@app.route('/wallet/transactions', methods=['POST'])
def wallet_transaction():
    values = request.get_json()
    required = ['sender', 'recipient', 'amount', 'signature']

    if not values:
        return jsonify({"error": "Invalid JSON"}), 400

    if not all(k in values for k in required):
        return jsonify({"error": "Missing values"}), 400

    index = blockchain.new_transaction(
        values['sender'],
        values['recipient'],
        values['amount'],
        values['signature']
    )

    if index is None:
        return jsonify({"error": "Invalid cryptographic signature"}), 400

    return jsonify({
        "message": f"Transaction will be added to Block {index}"
    }), 201


# ── Wallet ────────────────────────────────────────────────────────────────────

@app.route('/wallet/create', methods=['GET'])
def create_wallet():
    private_key, public_key = WalletCrypto.generate_key_pair()
    return jsonify({
        "private_key": private_key,
        "public_key": public_key
    }), 200


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True
    )