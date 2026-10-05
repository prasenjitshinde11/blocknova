from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class BlockModel(db.Model):
    __tablename__ = 'block'
    id            = db.Column(db.Integer, primary_key=True)
    index         = db.Column(db.Integer, unique=True, nullable=False)
    timestamp     = db.Column(db.Float, nullable=False)
    proof         = db.Column(db.Integer, nullable=False)
    # Bug #5 fix: use Text instead of String(64).
    # String(64) is fine for SHA-256 hex digests but silently truncates on
    # databases that enforce VARCHAR length (PostgreSQL, MySQL).  Text is
    # unbounded and portable across all SQLAlchemy-supported backends.
    previous_hash = db.Column(db.Text, nullable=False)
    transactions  = db.relationship('TransactionModel', backref='block', lazy=True)


class TransactionModel(db.Model):
    __tablename__ = 'transactions'
    id        = db.Column(db.Integer, primary_key=True)
    sender    = db.Column(db.Text, nullable=False)
    recipient = db.Column(db.Text, nullable=False)
    amount    = db.Column(db.Float, nullable=False)
    signature = db.Column(db.Text, nullable=True)
    block_id  = db.Column(db.Integer, db.ForeignKey('block.id'), nullable=True)
