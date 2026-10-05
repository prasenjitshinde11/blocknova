from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256
import binascii
import json
import re

MIN_KEY_BITS = 2048
MAX_KEY_BITS = 4096
_HEX_RE = re.compile(r'[0-9a-f]+')


def _is_lower_hex(value):
    return isinstance(value, str) and _HEX_RE.fullmatch(value) is not None


class WalletCrypto:

    @staticmethod
    def generate_key_pair():
        """Generate a fresh public/private key pair as hex strings"""
        key = RSA.generate(2048)

        private_key = binascii.hexlify(
            key.export_key(format='DER')
        ).decode('ascii')

        public_key = binascii.hexlify(
            key.publickey().export_key(format='DER')
        ).decode('ascii')

        return private_key, public_key

    @staticmethod
    def _public_key(address):
        """RSA public key for a canonical address, else None."""
        if not _is_lower_hex(address) or len(address) > MAX_KEY_BITS:
            return None
        try:
            der = binascii.unhexlify(address)
            key = RSA.import_key(der)
        except (ValueError, TypeError, IndexError):
            return None
        if key.has_private() or not MIN_KEY_BITS <= key.size_in_bits() <= MAX_KEY_BITS:
            return None
        if key.export_key(format='DER') != der:
            return None
        return key

    @staticmethod
    def canonical_address(address):
        """Return `address` if it is the canonical form of an RSA public key
        (lowercase hex of SubjectPublicKeyInfo DER), else None."""
        return address if WalletCrypto._public_key(address) is not None else None

    @staticmethod
    def transaction_payload(sender, recipient, amount, nonce, expires_at):
        """Canonical, unambiguous bytes covered by a transaction signature."""
        return json.dumps(
            {
                "type": "blocknova-tx-v2",
                "sender": sender,
                "recipient": recipient,
                "amount": float(amount),
                "nonce": int(nonce),
                "expires_at": int(expires_at),
            },
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()

    @staticmethod
    def verify_signature(public_key_hex, recipient, amount, signature_hex, nonce, expires_at):
        """Verifies if a transaction signature matches the sender's public key"""

        public_key = WalletCrypto._public_key(public_key_hex)
        if public_key is None or not _is_lower_hex(signature_hex):
            return False
        if len(signature_hex) != 2 * public_key.size_in_bytes():
            return False

        try:
            data = WalletCrypto.transaction_payload(
                public_key_hex, recipient, amount, nonce, expires_at
            )

            pkcs1_15.new(public_key).verify(
                SHA256.new(data),
                binascii.unhexlify(signature_hex)
            )

            return True

        except (ValueError, TypeError, IndexError):
            return False

    @staticmethod
    def sign_transaction(private_key_hex, sender, recipient, amount, nonce, expires_at):
        """Signs a transaction using the sender's private key.
        Returns None if the key is invalid or does not belong to `sender`."""

        try:
            private_key = RSA.import_key(
                binascii.unhexlify(private_key_hex)
            )

            own_public_key = binascii.hexlify(
                private_key.publickey().export_key(format='DER')
            ).decode('ascii')

            if sender != own_public_key:
                return None

            data = WalletCrypto.transaction_payload(
                sender, recipient, amount, nonce, expires_at
            )

            data_hash = SHA256.new(data)

            signature = pkcs1_15.new(private_key).sign(
                data_hash
            )

            return binascii.hexlify(signature).decode('ascii')

        except Exception:
            return None
