"""BLE transport layer for Tedee lock communication using bleak."""

import asyncio
import logging
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

logger = logging.getLogger(__name__)

# Tedee Lock BLE Service
SERVICE_UUID = "00000002-4899-489f-a301-fbee544b1db0"

# Characteristics
CHAR_NOTIFICATIONS = "00000101-4899-489f-a301-fbee544b1db0"  # Lock -> Client (Notify)
CHAR_PTLS_TX = "00000301-4899-489f-a301-fbee544b1db0"       # Lock -> Client (Notify)
CHAR_PTLS_RX = "00000401-4899-489f-a301-fbee544b1db0"       # Client -> Lock (Write)
CHAR_API_COMMANDS = "00000501-4899-489f-a301-fbee544b1db0"   # Bidirectional (Indicate)


def serial_to_service_uuid(serial: str) -> str:
    """Convert serial number to BLE advertising service UUID."""
    clean = serial.replace("-", "")
    if len(clean) != 14:
        raise ValueError(f"Serial must be 14 digits (without dash), got: {serial}")
    return (
        f"{clean[0:4]}0000-{clean[4:8]}-{clean[8:12]}-{clean[12:14]}00-000000000000"
    )


class TedeeBLETransport:
    """BLE transport for communicating with a Tedee lock."""

    def __init__(
        self,
        device: BLEDevice | str,
        disconnect_callback: Callable[[], None] | None = None,
    ):
        """Initialize with a BLEDevice or address string."""
        self._device = device
        self._disconnect_callback = disconnect_callback
        self._client: BleakClient | None = None
        self._ptls_tx_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._notification_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._api_command_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._mtu: int = 200

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def mtu(self) -> int:
        return self._mtu

    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle BLE disconnection."""
        logger.warning("BLE disconnected")
        if self._disconnect_callback:
            self._disconnect_callback()

    async def connect(self) -> None:
        """Connect to the lock and subscribe to notifications."""
        logger.info("Connecting to %s...", self._device)
        self._client = BleakClient(
            self._device,
            disconnected_callback=self._on_disconnect,
        )
        await self._client.connect()
        self._mtu = self._client.mtu_size
        logger.info("Connected (MTU: %d)", self._mtu)

        # Subscribe to PTLS TX notifications
        await self._client.start_notify(CHAR_PTLS_TX, self._on_ptls_tx)
        # Subscribe to Notifications characteristic
        await self._client.start_notify(CHAR_NOTIFICATIONS, self._on_notification)
        # Subscribe to API Commands indications
        await self._client.start_notify(CHAR_API_COMMANDS, self._on_api_command)
        logger.info("Subscribed to all characteristics")

    async def disconnect(self) -> None:
        """Disconnect from the lock."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
            logger.info("Disconnected")

    def _on_ptls_tx(self, _sender: int, data: bytearray) -> None:
        """Handle PTLS TX notification (lock -> client, handshake)."""
        logger.debug("PTLS TX: %s", data.hex())
        self._ptls_tx_queue.put_nowait(bytes(data))

    def _on_notification(self, _sender: int, data: bytearray) -> None:
        """Handle Notification characteristic data."""
        logger.debug("Notification: %s", data.hex())
        self._notification_queue.put_nowait(bytes(data))

    def _on_api_command(self, _sender: int, data: bytearray) -> None:
        """Handle API Commands indication (lock -> client)."""
        logger.debug("API Command response: %s", data.hex())
        self._api_command_queue.put_nowait(bytes(data))

    async def write_ptls_rx(self, data: bytes) -> None:
        """Write data to PTLS RX characteristic (client -> lock, handshake)."""
        logger.debug("PTLS RX write (%d bytes): %s", len(data), data.hex())
        await self._client.write_gatt_char(CHAR_PTLS_RX, data, response=False)

    async def write_api_command(self, data: bytes) -> None:
        """Write data to API Commands characteristic."""
        logger.debug("API Command write: %s", data.hex())
        await self._client.write_gatt_char(CHAR_API_COMMANDS, data, response=True)

    async def read_ptls_tx(self, timeout: float = 10.0) -> bytes:
        """Read next message from PTLS TX queue."""
        return await asyncio.wait_for(self._ptls_tx_queue.get(), timeout=timeout)

    async def read_notification(self, timeout: float = 10.0) -> bytes:
        """Read next notification."""
        return await asyncio.wait_for(self._notification_queue.get(), timeout=timeout)

    async def read_api_command(self, timeout: float = 10.0) -> bytes:
        """Read next API command response."""
        return await asyncio.wait_for(self._api_command_queue.get(), timeout=timeout)

    async def read_ptls_tx_multi(self, count: int, timeout: float = 10.0) -> list[bytes]:
        """Read multiple PTLS TX messages (for multi-part responses)."""
        messages = []
        for _ in range(count):
            msg = await self.read_ptls_tx(timeout=timeout)
            messages.append(msg)
        return messages

    def drain_queues(self) -> None:
        """Clear all queues."""
        for q in (self._ptls_tx_queue, self._notification_queue, self._api_command_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
