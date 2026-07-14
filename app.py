from flask import Flask, jsonify, request , render_template
from uuid import uuid4

from blockchain import Blockchain
from core.crypto import WalletCrypto

app = Flask(__name__,
           template_folder='frontend/templates',
           static_folder='frontend/static')

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blockchain.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

node_identifier = str(uuid4()).replace('-', '')

blockchain = Blockchain(app)


@app.route('/', methods=['GET'])
def home():
    return render_template("index.html")

@app.route('/mine', methods=['GET'])
def mine():

    last_block = blockchain.last_block
    last_proof = last_block.proof

    proof = blockchain.proof_of_work(
        last_proof
    )

    blockchain.new_transaction(
        sender="0",
        recipient=node_identifier,
        amount=1
    )

    previous_hash = blockchain.hash(
        last_block
    )

    block = blockchain.new_block(
        proof,
        previous_hash
    )

    return jsonify({
        "message": "New Block Forged",
        "index": block.index,
        "proof": block.proof,
        "previous_hash": block.previous_hash
    }), 200


@app.route('/chain', methods=['GET'])
def full_chain():

    chain = blockchain.get_chain()

    return jsonify({
        "chain": chain,
        "length": len(chain)
    }), 200


@app.route('/transactions/new', methods=['POST'])
def new_transaction():

    values = request.get_json()

    required = [
        'sender',
        'recipient',
        'amount'
    ]

    if not values:
        return "Invalid JSON", 400

    if not all(k in values for k in required):
        return "Missing values", 400

    index = blockchain.new_transaction(
        values['sender'],
        values['recipient'],
        values['amount']
    )

    return jsonify({
        "message": f"Transaction will be added to Block {index}"
    }), 201


@app.route('/wallet/create', methods=['GET'])
def create_wallet():

    private_key, public_key = (
        WalletCrypto.generate_key_pair()
    )

    return jsonify({
        "private_key": private_key,
        "public_key": public_key
    }), 200


@app.route('/wallet/transactions', methods=['POST'])
def wallet_transaction():

    values = request.get_json()

    required = [
        'sender',
        'recipient',
        'amount',
        'signature'
    ]

    if not values:
        return "Invalid JSON", 400

    if not all(k in values for k in required):
        return "Missing values", 400

    index = blockchain.new_transaction(
        values['sender'],
        values['recipient'],
        values['amount'],
        values['signature']
    )

    if index is None:
        return jsonify({
            "error": "Invalid cryptographic signature"
        }), 400

    return jsonify({
        "message": f"Transaction will be added to Block {index}"
    }), 201


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True
    )