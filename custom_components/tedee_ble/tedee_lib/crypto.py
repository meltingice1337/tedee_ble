"""Cryptographic utilities for Tedee BLE PTLS protocol."""

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_ecdsa_keypair() -> ec.EllipticCurvePrivateKey:
    """Generate a new ECDSA P-256 key pair."""
    return ec.generate_private_key(ec.SECP256R1())


def private_key_to_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def pem_to_private_key(pem: bytes) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key(pem, password=None)


def public_key_to_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    """Export public key as uncompressed point (65 bytes: 0x04 + X + Y)."""
    return key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


def public_key_to_base64(key: ec.EllipticCurvePublicKey) -> str:
    """Export public key as base64 string for Cloud API."""
    return base64.b64encode(public_key_to_bytes(key)).decode()


def bytes_to_public_key(data: bytes) -> ec.EllipticCurvePublicKey:
    """Load public key from uncompressed point bytes (65 bytes)."""
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), data)


def base64_to_public_key(b64: str) -> ec.EllipticCurvePublicKey:
    """Load public key from base64-encoded uncompressed point."""
    return bytes_to_public_key(base64.b64decode(b64))


def ecdh_shared_secret(
    private_key: ec.EllipticCurvePrivateKey,
    peer_public_key: ec.EllipticCurvePublicKey,
) -> bytes:
    """Compute ECDH shared secret (raw X coordinate, 32 bytes)."""
    return private_key.exchange(ec.ECDH(), peer_public_key)


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    """Compute HMAC-SHA256."""
    return hmac.new(key, data, hashlib.sha256).digest()


def derive_keys_from_hmac(
    shared_secret: bytes, label: str, transcript_hash: bytes
) -> tuple[bytes, bytes]:
    """Derive key (16 bytes) and IV (12 bytes) using HMAC-SHA256."""
    material = hmac_sha256(shared_secret, label.encode() + transcript_hash)
    key = material[:16]
    iv = material[16:28]
    return key, iv


def aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """AES-GCM-128 encrypt. Returns ciphertext + 16-byte tag."""
    aesgcm = AESGCM(key)
    return aesgcm.encrypt(iv, plaintext, aad if aad else None)


def aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, aad: bytes = b"") -> bytes:
    """AES-GCM-128 decrypt. Input includes 16-byte tag at end."""
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(iv, ciphertext, aad if aad else None)


def make_nonce(base_iv: bytes, counter: int) -> bytes:
    """Construct per-message nonce by XORing counter into last 2 bytes of IV."""
    iv = bytearray(base_iv)
    iv[10] ^= (counter >> 8) & 0xFF
    iv[11] ^= counter & 0xFF
    return bytes(iv)


def ecdsa_sign(private_key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    """Sign data with ECDSA P-256 (SHA-256). Returns DER-encoded signature."""
    return private_key.sign(data, ec.ECDSA(hashes.SHA256()))


def ecdsa_verify(public_key: ec.EllipticCurvePublicKey, signature: bytes, data: bytes) -> bool:
    """Verify ECDSA P-256 signature."""
    try:
        public_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def sha256(data: bytes) -> bytes:
    """Compute SHA-256 hash."""
    return hashlib.sha256(data).digest()
