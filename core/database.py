from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class BlockModel(db.Model):
    __tablename__ = 'block'
    id = db.Column(db.Integer, primary_key=True)
    index = db.Column(db.Integer, unique=True, nullable=False)
    timestamp = db.Column(db.Float, nullable=False)
    proof = db.Column(db.Integer, nullable=False)
    previous_hash = db.Column(db.String(64), nullable=False)
    transactions = db.relationship('TransactionModel', backref='block', lazy=True)

class TransactionModel(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.Text, nullable=False)
    recipient = db.Column(db.Text, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    signature = db.Column(db.Text, nullable=True)
    block_id = db.Column(db.Integer, db.ForeignKey('block.id'), nullable=True)
    # NULL for coinbase rewards and rows created before nonces existed.
    nonce = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.Index('uq_transactions_sender_nonce', 'sender', 'nonce', unique=True),
        db.Index('uq_transactions_signature', 'signature', unique=True,
                 sqlite_where=db.text('nonce IS NOT NULL'),
                 postgresql_where=db.text('nonce IS NOT NULL')),
    )

class RejectedSignatureModel(db.Model):
    """Authentic signatures that were rejected; they can never be accepted later."""
    __tablename__ = 'rejected_signatures'
    id = db.Column(db.Integer, primary_key=True)
    signature = db.Column(db.Text, unique=True, nullable=False)
    # Kept until the signed expiry passes; after that the signature is
    # rejected as expired anyway, so the row can be pruned.
    expires_at = db.Column(db.Integer, nullable=False, index=True)
