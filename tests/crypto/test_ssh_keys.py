import base64
import hashlib

import pytest

from kajet_turbo.crypto.ssh_keys import (
    ALGORITHMS,
    ECDSA_P256,
    ED25519,
    RSA_4096,
    SshKeypair,
    generate_keypair,
)


def test_algorithms_tuple():
    assert ALGORITHMS == (ED25519, ECDSA_P256, RSA_4096)


@pytest.mark.parametrize(
    "algorithm,prefix",
    [
        (ED25519, "ssh-ed25519 "),
        (ECDSA_P256, "ecdsa-sha2-nistp256 "),
        (RSA_4096, "ssh-rsa "),
    ],
)
def test_generate_keypair_public_key_format(algorithm, prefix):
    kp = generate_keypair(algorithm)
    assert isinstance(kp, SshKeypair)
    assert kp.algorithm == algorithm
    assert kp.public_key.startswith(prefix)
    assert kp.private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert kp.private_key.rstrip().endswith("-----END OPENSSH PRIVATE KEY-----")


def test_fingerprint_matches_openssh_sha256_of_blob():
    kp = generate_keypair(ED25519)
    blob = base64.b64decode(kp.public_key.split()[1])
    expected = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    assert kp.fingerprint == expected


def test_each_call_yields_a_distinct_key():
    a = generate_keypair(ED25519)
    b = generate_keypair(ED25519)
    assert a.public_key != b.public_key
    assert a.fingerprint != b.fingerprint


def test_unsupported_algorithm_raises():
    with pytest.raises(ValueError):
        generate_keypair("dsa-1024")
