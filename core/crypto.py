from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256
import binascii
import logging

logger = logging.getLogger(__name__)


def _normalise_amount(amount):
    """Convert amount to a canonical 8-decimal-place string for hashing.

    Bug #1 fix: using raw f"{amount}" causes float representation mismatches
    between signing (e.g. 1.5 → '1.5') and verification after DB round-trip
    (e.g. 1.5 → '1.5000001' or '1.50').  A fixed-precision format guarantees
    both sides always produce the identical byte string.
    """
    return f"{float(amount):.8f}"


class WalletCrypto:

    @staticmethod
    def generate_key_pair():
        """Generate a fresh public/private key pair as hex strings."""
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
        """Verify a transaction signature against the sender's public key.

        Bug #1 fix: uses _normalise_amount() so the data string is identical
        to the one produced by sign_transaction().
        Bug #9 fix: explicit exception types + debug logging instead of
        silently swallowing all errors.
        """
        # Guard against None signature before doing any crypto work
        if not signature_hex:
            logger.debug("verify_signature: no signature provided")
            return False

        try:
            amt_str = _normalise_amount(amount)
            data = f"{public_key_hex}{recipient}{amt_str}".encode()

            public_key = RSA.import_key(
                binascii.unhexlify(public_key_hex)
            )

            signature = binascii.unhexlify(signature_hex)

            data_hash = SHA256.new(data)

            pkcs1_15.new(public_key).verify(data_hash, signature)

            return True

        except (ValueError, TypeError, binascii.Error) as exc:
            logger.debug("Signature verification failed: %s", exc)
            return False

    @staticmethod
    def sign_transaction(private_key_hex, sender, recipient, amount):
        """Sign a transaction using the sender's private key.

        Bug #1 fix: uses _normalise_amount() so the signed data string
        matches what verify_signature() will reconstruct.
        Bug #9 fix: explicit exception types + structured logging.
        """
        try:
            private_key = RSA.import_key(
                binascii.unhexlify(private_key_hex)
            )

            amt_str = _normalise_amount(amount)
            data = f"{sender}{recipient}{amt_str}".encode()

            data_hash = SHA256.new(data)

            signature = pkcs1_15.new(private_key).sign(data_hash)

            return binascii.hexlify(signature).decode('ascii')

        except (ValueError, TypeError, binascii.Error) as exc:
            logger.warning("Transaction signing failed (key/encoding error): %s", exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error during signing: %s", exc)
            return None