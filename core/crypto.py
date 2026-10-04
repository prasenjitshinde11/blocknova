from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256
import binascii
import json


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
    def transaction_payload(sender, recipient, amount, nonce):
        """Canonical, unambiguous bytes covered by a transaction signature."""
        return json.dumps(
            {
                "type": "blocknova-tx-v1",
                "sender": sender,
                "recipient": recipient,
                "amount": float(amount),
                "nonce": int(nonce),
            },
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()

    @staticmethod
    def verify_signature(public_key_hex, recipient, amount, signature_hex, nonce):
        """Verifies if a transaction signature matches the sender's public key"""

        try:
            data = WalletCrypto.transaction_payload(
                public_key_hex, recipient, amount, nonce
            )

            public_key = RSA.import_key(
                binascii.unhexlify(public_key_hex)
            )

            signature = binascii.unhexlify(signature_hex)

            data_hash = SHA256.new(data)

            pkcs1_15.new(public_key).verify(
                data_hash,
                signature
            )

            return True

        except (ValueError, TypeError, IndexError):
            return False

    @staticmethod
    def sign_transaction(private_key_hex, sender, recipient, amount, nonce):
        """Signs a transaction using the sender's private key.
        Returns None if the key is invalid or does not belong to `sender`."""

        try:
            private_key = RSA.import_key(
                binascii.unhexlify(private_key_hex)
            )

            own_public_key = binascii.hexlify(
                private_key.publickey().export_key(format='DER')
            ).decode('ascii')

            if not isinstance(sender, str) or sender.lower() != own_public_key:
                return None

            data = WalletCrypto.transaction_payload(
                sender, recipient, amount, nonce
            )

            data_hash = SHA256.new(data)

            signature = pkcs1_15.new(private_key).sign(
                data_hash
            )

            return binascii.hexlify(signature).decode('ascii')

        except Exception:
            return None