from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256
import binascii


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
    def verify_signature(public_key_hex, recipient, amount, signature_hex):
        """Verifies if a transaction signature matches the sender's public key"""

        try:
            data = f"{public_key_hex}{recipient}{amount}".encode()

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

        except (ValueError, TypeError):
            return False

    @staticmethod
    def sign_transaction(private_key_hex, sender, recipient, amount):
        """Signs a transaction using the sender's private key"""

        try:
            private_key = RSA.import_key(
                binascii.unhexlify(private_key_hex)
            )

            data = f"{sender}{recipient}{amount}".encode()

            data_hash = SHA256.new(data)

            signature = pkcs1_15.new(private_key).sign(
                data_hash
            )

            return binascii.hexlify(signature).decode('ascii')

        except Exception:
            return None