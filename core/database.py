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

