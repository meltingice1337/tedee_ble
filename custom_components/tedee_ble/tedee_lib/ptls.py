"""PTLS (Protocol TLS) session implementation for Tedee BLE locks.

Implements the 4-stage handshake:
1. Hello exchange (client hello + server hello)
2. Server verification
3. Client verification
4. Session initialization with application key derivation

The PTLS protocol is a simplified TLS 1.3 variant. Key derivation uses direct
HMAC-SHA256 rather than the full TLS 1.3 HKDF key schedule:
  HMAC-SHA256(shared_secret, label || transcript_hash)
  → first 16 bytes = AES-GCM-128 key
  → next 12 bytes = AES-GCM-128 IV base
"""

import asyncio
import base64
import hashlib
import logging
import os
import struct
import time

from cryptography.hazmat.primitives.asymmetric import ec

from . import crypto
from .ble import TedeeBLETransport

logger = logging.getLogger(__name__)

# PTLS message headers (lower nibble only)
PTLS_HELLO = 0x03
PTLS_ALERT = 0x04
PTLS_SERVER_VERIFY = 0x05
PTLS_CLIENT_VERIFY_I = 0x06
PTLS_CLIENT_VERIFY_II = 0x07
PTLS_INITIALIZED = 0x08

# Data headers for encrypted communication
DATA_NOT_ENCRYPTED = 0x00
DATA_ENCRYPTED = 0x01

# PTLS version
PTLS_VERSION = 0x02

# Alert codes
ALERT_OK = 0x00
ALERT_GENERIC_ERROR = 0x01
ALERT_NO_TRUSTED_TIME = 0x02
ALERT_SESSION_TIMEOUT = 0x03
ALERT_DISCONNECTED = 0x04
ALERT_INVALID_CERTIFICATE = 0x05
ALERT_DEVICE_UNREGISTERED = 0x06

ALERT_NAMES = {
    0x00: "OK",
    0x01: "Generic error",
    0x02: "No trusted time",
    0x03: "Session timeout (24h)",
    0x04: "Disconnected",
    0x05: "Invalid certificate",
    0x06: "Device unregistered",
}


class PTLSError(Exception):
    pass


class PTLSAlertError(PTLSError):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"PTLS Alert: {ALERT_NAMES.get(code, f'Unknown (0x{code:02x})')}")


class PTLSSession:
    """Manages a PTLS secure session with a Tedee lock."""

    def __init__(
        self,
        transport: TedeeBLETransport,
        device_private_key: ec.EllipticCurvePrivateKey,
        certificate_b64: str,
        device_public_key_b64: str,
    ):
        self.transport = transport
        self.device_key = device_private_key
        self.certificate = base64.b64decode(certificate_b64)
        self.device_pubkey = crypto.base64_to_public_key(device_public_key_b64)

        # Lock to serialize decrypt operations (prevents counter desync)
        self._crypto_lock = asyncio.Lock()

        # Handshake state
        self._transcript = hashlib.sha256()
        self._shared_secret: bytes | None = None

        # Server-reported MTU for message splitting
        self._server_mtu: int = 244

        # Saved handshake fields (needed for client verify signature)
        self._client_random_data: bytes | None = None  # 35 bytes
        self._client_ecdh_pub: bytes | None = None      # 65 bytes
        self._encrypted_random: bytes | None = None      # 48 bytes
        self._session_id_cache: bytes | None = None      # 4 bytes
        self._server_random_data: bytes | None = None    # 35 bytes
        self._server_ecdh_pub: bytes | None = None       # 65 bytes
        self._server_auth_data: bytes | None = None
        self._server_signature: bytes | None = None
        self._hello_hash: bytes | None = None

        # Session state
        self.session_id: bytes | None = None
        self.send_key: bytes | None = None
        self.send_iv: bytes | None = None
        self.recv_key: bytes | None = None
        self.recv_iv: bytes | None = None
        self.send_counter: int = 0
        self.recv_counter: int = 0

    @property
    def is_established(self) -> bool:
        return self.session_id is not None

    def _hash_snapshot(self) -> bytes:
        """Get current transcript hash without consuming state."""
        return self._transcript.copy().digest()

    def _hash_update(self, data: bytes) -> None:
        """Update running transcript hash."""
        self._transcript.update(data)

    async def handshake(self) -> None:
        """Perform the full PTLS handshake."""
        logger.info("Starting PTLS handshake...")

        # Phase 1: Hello exchange
        hello_hash = await self._hello_exchange()
        logger.info("Hello exchange complete")

        # Phase 2: Server verification
        await self._server_verify(hello_hash)
        logger.info("Server verification complete")

        # Phase 3: Client verification
        await self._client_verify(hello_hash)
        logger.info("Client verification complete")

        # Phase 4: Wait for session initialized
        await self._wait_initialized()
        logger.info("PTLS session established (ID: %s)", self.session_id.hex())

    async def _hello_exchange(self) -> bytes:
        """Exchange hello messages with the lock."""
        # Generate ephemeral ECDH key pair
        eph_key = ec.generate_private_key(ec.SECP256R1())
        eph_pub_bytes = crypto.public_key_to_bytes(eph_key.public_key())

        # Build client hello frame (152 bytes)
        mtu = min(self.transport.mtu, 255)
        random_data = os.urandom(32)
        header_bytes = bytes([PTLS_VERSION, mtu, 0x00])
        encrypted_random = bytes(48)  # zeros for new session
        session_id_cache = bytes(4)   # zeros for new session

        client_hello_payload = (
            header_bytes + random_data + eph_pub_bytes
            + encrypted_random + session_id_cache
        )
        assert len(client_hello_payload) == 152

        # Save fields for client verify signature
        self._client_random_data = client_hello_payload[0:35]
        self._client_ecdh_pub = eph_pub_bytes
        self._encrypted_random = encrypted_random
        self._session_id_cache = session_id_cache

        # Update transcript hash with client hello payload
        self._hash_update(client_hello_payload)

        # Send with PTLS_HELLO header
        await self.transport.write_ptls_rx(bytes([PTLS_HELLO]) + client_hello_payload)

        # Receive server hello
        response = await self.transport.read_ptls_tx()
        header = response[0] & 0x0F

        if header == PTLS_ALERT:
            raise PTLSAlertError(response[1] if len(response) > 1 else 0xFF)
        if header != PTLS_HELLO:
            raise PTLSError(f"Expected PTLS_HELLO (0x03), got 0x{header:02x}")

        server_hello_payload = response[1:]
        if len(server_hello_payload) < 100:
            raise PTLSError(
                f"Server hello too short: {len(server_hello_payload)} bytes"
            )

        server_version = server_hello_payload[0]
        server_mtu = server_hello_payload[1]
        server_ecdh_pub_bytes = server_hello_payload[35:100]

        logger.debug("Server version: 0x%02x, MTU: %d", server_version, server_mtu)
        self._server_mtu = server_mtu

        # Save server fields for client verify signature
        self._server_random_data = server_hello_payload[0:35]
        self._server_ecdh_pub = server_ecdh_pub_bytes

        # Update transcript hash with server hello payload
        self._hash_update(server_hello_payload)
        hello_hash = self._hash_snapshot()
        self._hello_hash = hello_hash

        # Compute ECDH shared secret
        server_ecdh_pub = crypto.bytes_to_public_key(server_ecdh_pub_bytes)
        self._shared_secret = crypto.ecdh_shared_secret(eph_key, server_ecdh_pub)
        logger.debug("ECDH shared secret computed (%d bytes)", len(self._shared_secret))

        return hello_hash

    async def _server_verify(self, hello_hash: bytes) -> None:
        """Perform server verification."""
        # Create auth data: current time in milliseconds (8 bytes, big-endian)
        auth_data = struct.pack(">Q", int(time.time() * 1000))

        # Send server verify challenge
        await self.transport.write_ptls_rx(bytes([PTLS_SERVER_VERIFY]) + auth_data)

        # Derive server handshake keys
        srv_key, srv_iv = crypto.derive_keys_from_hmac(
            self._shared_secret, "ptlss hs traffic", hello_hash
        )

        # Receive encrypted server verify response
        response = await self.transport.read_ptls_tx()
        header = response[0] & 0x0F

        if header == PTLS_ALERT:
            raise PTLSAlertError(response[1] if len(response) > 1 else 0xFF)
        if header != PTLS_SERVER_VERIFY:
            raise PTLSError(f"Expected PTLS_SERVER_VERIFY (0x05), got 0x{header:02x}")

        encrypted_data = response[1:]

        try:
            decrypted = crypto.aes_gcm_decrypt(srv_key, srv_iv, encrypted_data)
        except Exception as e:
            raise PTLSError(f"Server verify decryption failed: {e}") from e

        logger.debug("Server verify decrypted: %d bytes", len(decrypted))

        # Parse decrypted server verify
        pos = 0
        recv_auth_data_len = struct.unpack(">H", decrypted[pos:pos + 2])[0]
        pos += 2
        recv_auth_data = decrypted[pos:pos + recv_auth_data_len]
        pos += recv_auth_data_len

        sig_len = struct.unpack(">H", decrypted[pos:pos + 2])[0]
        pos += 2
        server_sig = decrypted[pos:pos + sig_len]
        pos += sig_len

        recv_hello_hash_len = struct.unpack(">H", decrypted[pos:pos + 2])[0]
        pos += 2
        recv_hello_hash = decrypted[pos:pos + recv_hello_hash_len]

        # Save server verify fields for client verify signature
        self._server_auth_data = recv_auth_data
        self._server_signature = server_sig

        # Verify auth_data matches what we sent
        if recv_auth_data != auth_data:
            raise PTLSError("Server verify: auth_data mismatch")

        # Verify hello_hash matches
        if recv_hello_hash != hello_hash:
            raise PTLSError("Server verify: hello_hash mismatch")

        # Verify server signature using the device's public key.
        # The signature is over the transcript hash (prehashed SHA-256 digest)
        # after updating it with auth_data_len + auth_data.
        # This authenticates the lock's long-term identity.
        _transcript_for_sig = self._transcript.copy()
        _transcript_for_sig.update(
            struct.pack(">H", len(recv_auth_data)) + recv_auth_data
        )
        sig_digest = _transcript_for_sig.digest()

        if not crypto.ecdsa_verify_prehashed(
            self.device_pubkey, server_sig, sig_digest
        ):
            raise PTLSError(
                "Server signature verification failed — lock identity not confirmed"
            )

        # Update transcript hash with decrypted server verify content
        self._hash_update(decrypted)
        logger.debug("Server verification passed")

    async def _client_verify(self, hello_hash: bytes) -> None:
        """Send client verification with certificate and signature."""
        hello_verify_hash = self._hash_snapshot()

        # Build signature data
        sign_data = (
            self._client_random_data
            + self._client_ecdh_pub
            + self._encrypted_random
            + self._session_id_cache
            + self._server_random_data
            + self._server_ecdh_pub
            + struct.pack(">H", len(self._server_auth_data)) + self._server_auth_data
            + struct.pack(">H", len(self._server_signature)) + self._server_signature
            + struct.pack(">H", len(hello_hash)) + hello_hash
            + struct.pack(">H", len(self.certificate)) + self.certificate
        )

        signature = crypto.ecdsa_sign(self.device_key, sign_data)

        # Build payload
        payload = (
            struct.pack(">H", len(self.certificate)) + self.certificate
            + struct.pack(">H", len(signature)) + signature
            + struct.pack(">H", len(hello_verify_hash)) + hello_verify_hash
        )

        # Update transcript hash with the payload BEFORE encryption
        self._hash_update(payload)

        # Derive client handshake keys
        cli_key, cli_iv = crypto.derive_keys_from_hmac(
            self._shared_secret, "ptlsc hs traffic", hello_hash
        )

        # Encrypt with AES-GCM
        encrypted = crypto.aes_gcm_encrypt(cli_key, cli_iv, payload)

        # Split based on lock's reported MTU
        mtu = self._server_mtu - 1

        if len(encrypted) <= mtu:
            await self.transport.write_ptls_rx(
                bytes([PTLS_CLIENT_VERIFY_I]) + encrypted
            )
            await self.transport.write_ptls_rx(bytes([PTLS_CLIENT_VERIFY_II]))
        else:
            part1 = bytes([PTLS_CLIENT_VERIFY_I]) + encrypted[:mtu]
            part2 = bytes([PTLS_CLIENT_VERIFY_II]) + encrypted[mtu:]
            await self.transport.write_ptls_rx(part1)
            await self.transport.write_ptls_rx(part2)

        logger.debug("Client verification sent (%d bytes encrypted)", len(encrypted))

    async def _wait_initialized(self) -> None:
        """Wait for PTLS_INITIALIZED response and derive application keys."""
        response = await self.transport.read_ptls_tx()
        header = response[0] & 0x0F

        if header == PTLS_ALERT:
            raise PTLSAlertError(response[1] if len(response) > 1 else 0xFF)
        if header != PTLS_INITIALIZED:
            raise PTLSError(f"Expected PTLS_INITIALIZED (0x08), got 0x{header:02x}")

        # Extract 4-byte session ID
        self.session_id = response[1:5]

        # Derive application encryption keys
        finished_hash = self._hash_snapshot()

        self.send_key, self.send_iv = crypto.derive_keys_from_hmac(
            self._shared_secret, "ptlsc ap traffic", finished_hash
        )
        self.recv_key, self.recv_iv = crypto.derive_keys_from_hmac(
            self._shared_secret, "ptlss ap traffic", finished_hash
        )

        self.send_counter = 0
        self.recv_counter = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt a message for sending to the lock."""
        if not self.is_established:
            raise PTLSError("Session not established")

        nonce = crypto.make_nonce(self.send_iv, self.send_counter)
        ciphertext = crypto.aes_gcm_encrypt(self.send_key, nonce, plaintext)
        logger.debug(
            "Encrypt: counter=%d, plaintext_len=%d, ciphertext_len=%d",
            self.send_counter, len(plaintext), len(ciphertext),
        )
        self.send_counter += 1

        return bytes([DATA_ENCRYPTED]) + ciphertext

    async def async_decrypt(self, data: bytes) -> bytes:
        """Decrypt a message from the lock (async, serialized).

        Uses a lock to ensure recv_counter stays in sync when notifications
        and command responses arrive concurrently.
        """
        async with self._crypto_lock:
            return self._decrypt_inner(data)

    def _decrypt_inner(self, data: bytes) -> bytes:
        """Inner decrypt logic (must be called under _crypto_lock)."""
        if not self.is_established:
            raise PTLSError("Session not established")

        header = data[0] & 0x0F
        if header == PTLS_ALERT:
            raise PTLSAlertError(data[1] if len(data) > 1 else 0xFF)
        if header == DATA_NOT_ENCRYPTED:
            return data[1:]
        if header != DATA_ENCRYPTED:
            raise PTLSError(f"Unexpected header: 0x{header:02x}")

        encrypted_data = data[1:]
        nonce = crypto.make_nonce(self.recv_iv, self.recv_counter)
        logger.debug(
            "Decrypt: counter=%d, encrypted_len=%d, header=0x%02x",
            self.recv_counter, len(encrypted_data), data[0],
        )
        plaintext = crypto.aes_gcm_decrypt(self.recv_key, nonce, encrypted_data)
        self.recv_counter += 1

        return plaintext
