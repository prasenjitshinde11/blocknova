import os

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from uuid import uuid4

from blockchain import Blockchain, TransactionError, UNITS_PER_COIN
from core.crypto import WalletCrypto
from core.database import db, BlockModel, TransactionModel

app = Flask(__name__,
            template_folder='frontend/templates',
            static_folder='frontend/static')

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///blockchain.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024

CORS(app)

node_identifier = str(uuid4()).replace('-', '')

blockchain = Blockchain(app)


def _json_object():
    """Request JSON body if it is an object, else None."""
    values = request.get_json(silent=True)
    return values if isinstance(values, dict) else None


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
    """Return the net balance for a wallet address (GET)."""
    return _balance_response(public_key)


@app.route('/api/balance', methods=['POST'])
def get_balance_post():
    """Return the net balance for a wallet address (POST with JSON body).
    Accepts: { "address": "<public_key_hex>" }
    Using POST avoids URL-length limits with long RSA hex keys.
    """
    values = _json_object()
    if values is None:
        return jsonify({"error": "Invalid JSON"}), 400
    if not isinstance(values.get('address'), str):
        return jsonify({"error": "Missing 'address' field"}), 400
    return _balance_response(values['address'])


def _balance_response(public_key):
    """Shared balance calculation helper.
    `balance` is spendable: confirmed balance minus pending sends."""
    with app.app_context():
        received, sent, pending_sent = blockchain.balance_units(public_key)

    unit = UNITS_PER_COIN
    return jsonify({
        "address": public_key,
        "balance": (received - sent - pending_sent) / unit,
        "confirmed_balance": (received - sent) / unit,
        "pending_sent": pending_sent / unit,
        "received": received / unit,
        "sent": sent / unit
    }), 200


# ── Sign ──────────────────────────────────────────────────────────────────────

@app.route('/api/sign', methods=['POST'])
def sign_transaction():
    """
    Sign a transaction payload server-side using the provided private key.
    Accepts: { private_key, sender, recipient, amount }
    Returns: { signature, nonce, expires_at }
    """
    values = _json_object()
    required = ['private_key', 'sender', 'recipient', 'amount']

    if not values:
        return jsonify({"error": "Invalid JSON"}), 400

    if not all(k in values for k in required):
        return jsonify({"error": "Missing values"}), 400

    if not isinstance(values['private_key'], str):
        return jsonify({"error": "Invalid private key"}), 400

    try:
        amount = blockchain.validate_transaction_fields(
            values['sender'], values['recipient'], values['amount']
        )
    except TransactionError as e:
        return jsonify({"error": str(e)}), 400

    nonce = blockchain.next_nonce(values['sender'])
    expires_at = blockchain.new_expiry()

    # Signatures are deterministic: if this exact payload was already
    # rejected (e.g. re-signing within the same second), pick a new expiry.
    for _ in range(10):
        signature = WalletCrypto.sign_transaction(
            values['private_key'],
            values['sender'],
            values['recipient'],
            amount,
            nonce,
            expires_at
        )
        if signature is None or not blockchain.signature_seen(signature):
            break
        expires_at -= 1

    if signature is None:
        return jsonify({"error": "Could not sign transaction — invalid private key or key does not match sender"}), 400

    return jsonify({
        "signature": signature,
        "nonce": nonce,
        "expires_at": expires_at
    }), 200


# ── Mine ──────────────────────────────────────────────────────────────────────

@app.route('/mine', methods=['GET'])
def mine():
    last_block = blockchain.last_block
    last_proof = last_block.proof

    proof = blockchain.proof_of_work(last_proof)

    blockchain.new_coinbase_transaction(
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
    values = _json_object()
    required = ['sender', 'recipient', 'amount']

    if not values:
        return jsonify({"error": "Invalid JSON"}), 400

    if not all(k in values for k in required):
        return jsonify({"error": "Missing values"}), 400

    try:
        index = blockchain.add_transaction(
            values['sender'],
            values['recipient'],
            values['amount']
        )
    except TransactionError as e:
        return jsonify({"error": str(e)}), e.status_code

    return jsonify({
        "message": f"Transaction will be added to Block {index}"
    }), 201


# ── Transactions (signed, primary) ───────────────────────────────────────────

@app.route('/wallet/transactions', methods=['POST'])
def wallet_transaction():
    values = _json_object()
    required = ['sender', 'recipient', 'amount', 'signature']

    if not values:
        return jsonify({"error": "Invalid JSON"}), 400

    if not all(k in values for k in required):
        return jsonify({"error": "Missing values"}), 400

    try:
        index = blockchain.add_transaction(
            values['sender'],
            values['recipient'],
            values['amount'],
            values['signature'],
            values.get('nonce'),
            values.get('expires_at')
        )
    except TransactionError as e:
        return jsonify({"error": str(e)}), e.status_code

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