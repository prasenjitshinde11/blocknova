import os
import logging

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from uuid import uuid4
from sqlalchemy import or_

from blockchain import Blockchain
from core.crypto import WalletCrypto
from core.database import db, BlockModel, TransactionModel

logging.basicConfig(level=logging.INFO)

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
    """Return aggregate chain statistics.

    Bug #3 fix: removed the redundant `with app.app_context():` wrapper.
    Flask routes already execute inside an application context; nesting another
    one is unnecessary and can cause session-management issues with some
    Flask-SQLAlchemy versions.
    """
    total_blocks       = BlockModel.query.count()
    total_transactions = TransactionModel.query.count()
    pending_count      = TransactionModel.query.filter_by(block_id=None).count()

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

    # Average block time (seconds between consecutive blocks)
    blocks = BlockModel.query.order_by(BlockModel.index).all()
    avg_block_time = 0.0
    if len(blocks) >= 2:
        times = [blocks[i].timestamp - blocks[i - 1].timestamp for i in range(1, len(blocks))]
        avg_block_time = round(sum(times) / len(times), 2)

    return jsonify({
        "total_blocks":       total_blocks,
        "total_transactions": total_transactions,
        "unique_wallets":     len(unique_wallets),
        "chain_valid":        chain_valid,
        "pending_tx":         pending_count,
        "avg_block_time":     avg_block_time,
    }), 200



@app.route('/api/balance/<path:public_key>', methods=['GET'])
def get_balance(public_key):
    """Return the net balance for a wallet address (GET)."""
    return _balance_response(public_key)


@app.route('/api/mempool', methods=['GET'])
def mempool():
    """Return all unconfirmed (pending) transactions."""
    pending = TransactionModel.query.filter_by(block_id=None).all()
    return jsonify({
        "pending": [
            {
                "id":        tx.id,
                "sender":    tx.sender,
                "recipient": tx.recipient,
                "amount":    tx.amount,
            }
            for tx in pending
        ],
        "count": len(pending)
    }), 200


@app.route('/api/search', methods=['GET'])
def search_transactions():
    """Search confirmed transactions by sender or recipient address fragment.
    Query param: ?q=<address_fragment> (min 3 chars)
    """
    q = request.args.get('q', '').strip()
    if len(q) < 3:
        return jsonify({"error": "Query must be at least 3 characters"}), 400

    txs = TransactionModel.query.filter(
        or_(
            TransactionModel.sender.contains(q),
            TransactionModel.recipient.contains(q)
        ),
        TransactionModel.block_id.isnot(None)
    ).order_by(TransactionModel.id.desc()).limit(50).all()

    results = []
    for tx in txs:
        block = db.session.get(BlockModel, tx.block_id)
        results.append({
            "sender":      tx.sender,
            "recipient":   tx.recipient,
            "amount":      tx.amount,
            "block_index": block.index     if block else None,
            "timestamp":   block.timestamp if block else None,
        })

    return jsonify({"results": results, "count": len(results)}), 200


@app.route('/api/balance', methods=['POST'])
def get_balance_post():
    """Return the net balance for a wallet address (POST with JSON body).
    Accepts: { "address": "<public_key_hex>" }
    Using POST avoids URL-length limits with long RSA hex keys.
    """
    values = request.get_json()
    if not values or 'address' not in values:
        return jsonify({"error": "Missing 'address' field"}), 400
    return _balance_response(values['address'])


def _balance_response(public_key):
    """Shared balance calculation helper.

    Bug #3 fix: removed redundant `with app.app_context():` wrapper.
    """
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
        "address":  public_key,
        "balance":  balance,
        "received": round(received, 8),
        "sent":     round(sent, 8)
    }), 200



@app.route('/api/sign', methods=['POST'])
def sign_transaction():
    """Sign a transaction payload server-side using the provided private key.

    Accepts: { private_key, sender, recipient, amount }
    Returns: { signature }

    Bug #8 note: the private key is transmitted to the server.  This endpoint
    must be served over HTTPS in any non-localhost deployment to prevent
    interception.  A HTTPS check is enforced unless the ALLOW_HTTP_SIGN env
    variable is set (for local development only).
    """
    # Bug #8 mitigation: reject plaintext private-key transmission in production
    if not app.debug and os.getenv('ALLOW_HTTP_SIGN', '').lower() != 'true':
        is_https = request.is_secure or request.headers.get('X-Forwarded-Proto', '') == 'https'
        if not is_https:
            return jsonify({
                "error": "Private key transmission requires HTTPS. "
                         "Set ALLOW_HTTP_SIGN=true to override for local development."
            }), 403

    values   = request.get_json()
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



@app.route('/mine', methods=['GET'])
def mine():
    last_block = blockchain.last_block
    last_proof = last_block.proof

    proof = blockchain.proof_of_work(last_proof)

    # Coinbase reward: sender "0" bypasses signature verification
    blockchain.new_transaction(
        sender="0",
        recipient=node_identifier,
        amount=1
    )

    previous_hash = blockchain.hash(last_block)
    block         = blockchain.new_block(proof, previous_hash)
    block_hash    = blockchain.hash(block)

    return jsonify({
        "message": "New Block Forged",
        "block": {
            "index":         block.index,
            "proof":         block.proof,
            "previous_hash": block.previous_hash,
            "hash":          block_hash,
            "timestamp":     block.timestamp
        }
    }), 200



@app.route('/chain', methods=['GET'])
def full_chain():
    chain = blockchain.get_chain()
    return jsonify({
        "chain":  chain,
        "length": len(chain)
    }), 200


@app.route('/transactions/new', methods=['POST'])
def new_transaction():
    values   = request.get_json()
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


    if index is None:
        return jsonify({
            "error": "Transaction rejected — check amount (must be > 0) and signature"
        }), 400

    return jsonify({
        "message": f"Transaction will be added to Block {index}"
    }), 201


@app.route('/wallet/transactions', methods=['POST'])
def wallet_transaction():
    values   = request.get_json()
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


@app.route('/wallet/create', methods=['GET'])
def create_wallet():
    private_key, public_key = WalletCrypto.generate_key_pair()
    return jsonify({
        "private_key": private_key,
        "public_key":  public_key
    }), 200



@app.route('/nodes/register', methods=['POST'])
def register_nodes():
    """Register one or more peer nodes for distributed consensus.
    Accepts: { "nodes": ["http://host1:5000", "http://host2:5000"] }
    """
    values = request.get_json()

    if not values or 'nodes' not in values:
        return jsonify({"error": "Please supply a 'nodes' list"}), 400

    nodes = values.get('nodes')
    if not isinstance(nodes, list) or not nodes:
        return jsonify({"error": "'nodes' must be a non-empty list"}), 400

    for node in nodes:
        blockchain.register_node(node)

    return jsonify({
        "message":     "New nodes registered",
        "total_nodes": list(blockchain.nodes)
    }), 201


@app.route('/nodes/resolve', methods=['GET'])
def consensus():
    """Run the Nakamoto longest-chain consensus algorithm across all peers."""
    replaced = blockchain.resolve_conflicts()

    if replaced:
        return jsonify({
            "message": "Chain was replaced with the authoritative longest chain",
            "chain":   blockchain.get_chain()
        }), 200

    return jsonify({
        "message": "Local chain is authoritative",
        "chain":   blockchain.get_chain()
    }), 200



if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    port       = int(os.getenv('PORT', 5000))

    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug_mode
    )