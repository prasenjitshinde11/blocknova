# ⛓️ BlockNova

A full-stack blockchain web application built with **Python**, **Flask**, and **SQLite** — demonstrating real-world blockchain concepts including cryptographic wallets, signed transactions, Proof of Work, SHA-256 hashing, and chain validation, all accessible through a modern browser UI.

---

## 🚀 Features

### Blockchain Core
- **Block creation & management** — Genesis block auto-created on first run
- **SHA-256 hashing** — Cryptographic block hashing with sorted JSON encoding
- **Proof of Work (PoW)** — 4-leading-zero difficulty target
- **Chain validation** — `is_chain_valid()` verifies hash linkage and PoW integrity
- **Node registration & consensus** — Peer node support via REST API

### Wallet & Cryptography
- **Wallet generation** — RSA key-pair generation (`/wallet/create`)
- **Transaction signing** — Server-side PKCS#1 v1.5 signing via `/api/sign`
- **Signature verification** — Cryptographic validation before any transaction is accepted
- **Balance tracking** — Real-time balance per wallet address (`/api/balance/<address>`)

### REST API
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI dashboard |
| `GET` | `/api/health` | Node liveness probe |
| `GET` | `/api/stats` | Chain statistics (blocks, txns, wallets) |
| `GET` | `/api/balance/<address>` | Wallet balance (GET path) |
| `POST` | `/api/balance` | Wallet balance (POST JSON body `{ "address": "..." }`) |
| `GET` | `/mine` | Mine a new block |
| `GET` | `/chain` | Full blockchain |
| `POST` | `/transactions/new` | Submit unsigned transaction (legacy) |
| `POST` | `/wallet/transactions` | Submit signed transaction |
| `GET` | `/wallet/create` | Generate new wallet key pair |
| `POST` | `/api/sign` | Sign a transaction payload |

### Frontend
- Real-time **dashboard** with chain stats and node status
- **Wallet panel** — create wallets, copy keys (with execCommand fallback), check balances via POST/GET
- **Transaction panel** — step-by-step transaction guide, "Use My Wallet" quick-fill, and server-side RSA auto-signing
- **Analytics & Charts** — interactive Block Growth and Transaction Volume charts (powered by bundled local `chart.umd.min.js`)
- **Blockchain explorer** — browse mined blocks and transactions
- **Mining panel** — trigger PoW mining with live feedback

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11, Flask 3.1 |
| Database | SQLite via Flask-SQLAlchemy |
| Cryptography | PyCryptodome (RSA / SHA-256) |
| Frontend | HTML5, Vanilla CSS, Vanilla JS |
| Charts | Chart.js 4.4 (bundled locally) |
| Cross-Origin | Flask-CORS |
| HTTP client | Requests |

---

## 📁 Project Structure

```text
BlockNova/
├── app.py                        # Flask app & all API routes
├── blockchain.py                 # Blockchain logic (PoW, hashing, chain ops)
├── requirements.txt              # Python dependencies
├── .gitignore
├── README.md
├── core/
│   ├── __init__.py
│   ├── crypto.py                 # RSA wallet key generation & signing
│   └── database.py               # SQLAlchemy models (BlockModel, TransactionModel)
├── frontend/
│   ├── templates/
│   │   └── index.html            # Single-page web UI
│   └── static/
│       ├── styles.css            # Custom CSS theme & glassmorphic styling
│       └── js/
│           ├── app.js            # Frontend logic (API calls, wallet, explorer)
│           └── chart.umd.min.js  # Local Chart.js bundle (shield & offline resilient)
└── test/
    └── test_blockchain.py        # Unit tests
```

---

## ⚙️ Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/prasenjitshinde11/BlockNova.git
cd BlockNova
```

### 2. Create a virtual environment

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the server

```bash
python app.py
```

The app will be available at **http://localhost:5000**

> SQLite database (`blockchain.db`) is auto-created on first run. A genesis block is seeded automatically.

---

## 🔑 API Usage Examples

### Create a wallet
```bash
curl http://localhost:5000/wallet/create
```

### Check balance
```bash
curl http://localhost:5000/api/balance/<public_key>
```

### Sign a transaction
```bash
curl -X POST http://localhost:5000/api/sign \
  -H "Content-Type: application/json" \
  -d '{"private_key":"...","sender":"...","recipient":"...","amount":10}'
```

### Submit a signed transaction
```bash
curl -X POST http://localhost:5000/wallet/transactions \
  -H "Content-Type: application/json" \
  -d '{"sender":"...","recipient":"...","amount":10,"signature":"..."}'
```

### Mine a block
```bash
curl http://localhost:5000/mine
```

---

## 🧪 Running Tests

```bash
python -m pytest test/
```

---

## 📄 License

MIT License — feel free to use, modify, and distribute.
