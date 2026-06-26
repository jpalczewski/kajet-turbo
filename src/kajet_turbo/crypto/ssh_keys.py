"""SSH keypair generation, serialization, and fingerprinting.

Kept separate from the sealing cipher: generation/serialization is its own
concern. Output formats match what an SSH client and an authorized_keys file
expect, so the public key can be pasted into a host's deploy keys and the private
key handed to ``ssh -i``.
"""

import base64
import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

ED25519 = "ed25519"
ECDSA_P256 = "ecdsa-p256"
RSA_4096 = "rsa-4096"
ALGORITHMS = (ED25519, ECDSA_P256, RSA_4096)


@dataclass(frozen=True)
class SshKeypair:
    algorithm: str
    public_key: str  # one-line OpenSSH authorized_keys entry
    private_key: str  # OpenSSH-format PEM
    fingerprint: str  # "SHA256:<base64-no-pad>"


def generate_keypair(algorithm: str) -> SshKeypair:
    if algorithm == ED25519:
        priv = ed25519.Ed25519PrivateKey.generate()
    elif algorithm == ECDSA_P256:
        priv = ec.generate_private_key(ec.SECP256R1())
    elif algorithm == RSA_4096:
        priv = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    else:
        raise ValueError(f"Unsupported SSH key algorithm: {algorithm!r}")

    public_key = (
        priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode()
    )
    private_key = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return SshKeypair(algorithm, public_key, private_key, _fingerprint(public_key))


def _fingerprint(public_key: str) -> str:
    """OpenSSH SHA256 fingerprint: base64(sha256(raw key blob)), padding stripped."""
    blob = base64.b64decode(public_key.split()[1])
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
