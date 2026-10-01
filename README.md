# BlockNova

A full-stack blockchain web application built with Python and Flask, demonstrating real-world blockchain concepts including cryptographic wallets, signed transactions, Proof of Work, SHA-256 hashing, and chain validation.

## Problem

Understanding blockchain fundamentals requires hands-on implementation. Most tutorials only cover theory. BlockNova implements a working blockchain with cryptographic wallets, RSA transaction signing, and a browser UI — making the concepts tangible and interactive.

## Solution

BlockNova runs a local blockchain node as a Flask web server. Users can generate RSA wallets, submit signed transactions, mine blocks using Proof of Work, and view the full chain — all from a browser dashboard with real-time charts.

## Features

- Genesis block auto-created on first run
- SHA-256 block hashing with sorted JSON encoding
- Proof of Work with 4-leading-zero difficulty target
- Chain validation via `is_chain_valid()` — verifies hash linkage and PoW integrity
- RSA key-pair wallet generation (`pycryptodome`)
- Server-side PKCS#1 v1.5 transaction signing
- Cryptographic signature verification before transactions are accepted
- Real-time wallet balance tracking per address
- Peer node registration via REST API
- Dashboard with chain stats, block growth chart, and transaction volume chart
- Wallet panel — create wallets, copy keys, check balances
- Transaction panel with server-side RSA auto-signing
- SQLite persistence for blocks and transactions

## Architecture

```
Browser (Frontend)
   ↓
Flask App (app.py)
   ↓
Blockchain logic (blockchain.py)
   ├── core/crypto.py      (RSA wallet + signing)
   └── core/database.py    (SQLAlchemy models)
   ↓
SQLite (blockchain.db)
```

**Modules:**
- `app.py` — Flask routes and REST API
- `blockchain.py` — Block creation, PoW, chain validation, consensus
- `core/crypto.py` — RSA wallet generation, signing, verification
- `core/database.py` — SQLAlchemy models (BlockModel, TransactionModel)
- `frontend/` — HTML templates and static assets

## Tech Stack

**Backend**
- Python
- Flask 3.1.2
- Flask-SQLAlchemy 3.1.1
- Flask-CORS 4.0.1
- pycryptodome 3.20.0 (RSA cryptography, SHA-256)
- requests 2.32.5

**Database**
- SQLite (`blockchain.db`)

**Frontend**
- HTML / CSS / JavaScript (served via Flask templates)
- Dashboard charts (bundled JS)

## Project Structure

```
BlockNova/
├── app.py              # Flask entry point and all API routes
├── blockchain.py       # Blockchain core logic
├── requirements.txt
├── core/
│   ├── crypto.py       # RSA wallet generation and signing
│   └── database.py     # SQLAlchemy models
├── frontend/
│   ├── templates/      # HTML templates
│   └── static/         # CSS, JS, assets
└── test/               # Directory exists — no tests implemented yet
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI dashboard |
| `GET` | `/api/health` | Node liveness probe |
| `GET` | `/api/stats` | Chain statistics (blocks, txns, wallets) |
| `GET` | `/api/balance/<address>` | Wallet balance |
| `POST` | `/api/balance` | Wallet balance (JSON body) |
| `GET` | `/mine` | Mine a new block |
| `GET` | `/chain` | Full blockchain |
| `POST` | `/transactions/new` | Submit unsigned transaction |
| `POST` | `/wallet/transactions` | Submit signed transaction |
| `GET` | `/wallet/create` | Generate new RSA wallet key pair |
| `POST` | `/api/sign` | Sign a transaction payload |

## Getting Started

### Prerequisites

- Python 3.10+

### Installation

```bash
git clone https://github.com/prasenjitshinde11/BlockNova.git
cd BlockNova
pip install -r requirements.txt
```

## Running Locally

```bash
python app.py
```

Open `http://localhost:5000` in your browser.

The SQLite database (`blockchain.db`) and genesis block are created automatically on first run.

## Testing

No automated tests are currently implemented. A `test/` directory exists but is empty.

## Deployment

No deployment configuration is currently included. The app runs locally via Flask's built-in server.

## Future Improvements

- Add automated tests for blockchain logic and API endpoints
- Add a Dockerfile for containerised deployment
- Deploy to a public server so the node is accessible over the network
- Implement actual peer-to-peer consensus between multiple running nodes
- Add transaction mempool before mining
- Add a proper production WSGI server (Gunicorn)

## Author

**Prasenjit Shinde**
