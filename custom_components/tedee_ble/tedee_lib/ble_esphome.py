"""BLE transport via ESPHome Bluetooth Proxy using bleak-esphome.

Uses bleak-esphome + habluetooth to route standard BleakClient/BleakScanner
through an ESPHome BT proxy transparently. This handles connection slot
management, GATT caching, and auto-reconnection.
"""

import asyncio
import logging

import habluetooth
from bleak import BleakClient, BleakScanner
from bleak_esphome import APIConnectionManager, ESPHomeDeviceConfig

from .ble import (
    CHAR_API_COMMANDS,
    CHAR_NOTIFICATIONS,
    CHAR_PTLS_RX,
    CHAR_PTLS_TX,
    serial_to_service_uuid,
)

logger = logging.getLogger(__name__)

# Singleton: shared proxy manager across multiple transports
_proxy_manager: "ProxyManager | None" = None


class ProxyManager:
    """Manages ESPHome proxy connections and the habluetooth stack."""

    def __init__(self) -> None:
        self._connections: list[APIConnectionManager] = []
        self._bt_manager: habluetooth.BluetoothManager | None = None
        self._started = False

    async def start(self, devices: list[ESPHomeDeviceConfig], timeout: float = 10.0) -> None:
        """Start proxy connections and bluetooth manager."""
        if self._started:
            return

        self._bt_manager = habluetooth.BluetoothManager()
        await self._bt_manager.async_setup()

        self._connections = [APIConnectionManager(device) for device in devices]
        tasks = [asyncio.create_task(conn.start()) for conn in self._connections]
        done, pending = await asyncio.wait(tasks, timeout=timeout)

        # Check for errors in completed tasks
        for task in done:
            if task.exception():
                logger.warning("Proxy connection error: %s", task.exception())

        if not done or all(t.exception() for t in done):
            for t in pending:
                t.cancel()
            raise RuntimeError("Failed to connect to any ESPHome proxy")

        self._started = True
        logger.info("ESPHome proxy manager started (%d connections)", len(done))

    async def stop(self) -> None:
        """Stop all proxy connections."""
        if not self._started:
            return
        await asyncio.gather(*(conn.stop() for conn in self._connections), return_exceptions=True)
        self._connections.clear()
        self._started = False
        logger.info("ESPHome proxy manager stopped")


async def get_proxy_manager(
    proxy_host: str,
    port: int = 6053,
    password: str = "",
    noise_psk: str | None = None,
    timeout: float = 10.0,
) -> ProxyManager:
    """Get or create the singleton proxy manager."""
    global _proxy_manager
    if _proxy_manager is not None and _proxy_manager._started:
        return _proxy_manager

    config: ESPHomeDeviceConfig = {
        "address": proxy_host if proxy_host.endswith(".") else proxy_host + ".",
        "noise_psk": noise_psk,
    }

    _proxy_manager = ProxyManager()
    await _proxy_manager.start([config], timeout=timeout)
    return _proxy_manager


class ESPHomeBLETransport:
    """BLE transport for Tedee locks via ESPHome Bluetooth Proxy.

    Uses bleak-esphome so the proxy is transparent — standard BleakClient
    handles connection, GATT operations, and notification subscriptions.
    """

    def __init__(
        self,
        proxy_host: str,
        lock_address: str,
        port: int = 6053,
        password: str = "",
        noise_psk: str | None = None,
    ):
        self._proxy_host = proxy_host
        self._port = port
        self._password = password
        self._noise_psk = noise_psk
        self._lock_address = lock_address
        self._manager: ProxyManager | None = None

        self._client: BleakClient | None = None
        self._connected = False
        self._mtu: int = 200

        # Same queue interface as TedeeBLETransport
        self._ptls_tx_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._notification_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._api_command_queue: asyncio.Queue[bytes] = asyncio.Queue()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    @property
    def mtu(self) -> int:
        return self._mtu

    async def connect(self) -> None:
        """Connect to lock through ESPHome BT proxy."""
        logger.info(
            "Connecting to %s via ESPHome proxy %s...",
            self._lock_address, self._proxy_host,
        )

        # 1. Start proxy manager (sets up habluetooth + ESPHome scanner)
        self._manager = await get_proxy_manager(
            self._proxy_host, self._port, self._password, self._noise_psk,
        )

        # 2. Wait for the lock to appear in BLE scan
        logger.info("Waiting for lock %s to appear in proxy scan...", self._lock_address)
        device = None
        for _ in range(5):
            devices = await BleakScanner.discover(timeout=3, return_adv=True)
            for d, adv in devices.values():
                if d.address.upper() == self._lock_address.upper():
                    device = d
                    logger.info("Found lock: %s (RSSI: %d)", d.name, adv.rssi)
                    break
            if device:
                break
            logger.debug("Lock not found yet, scanning again...")

        if not device:
            raise RuntimeError(
                f"Lock {self._lock_address} not found via ESPHome proxy. "
                f"Is it in range of the ESP32?"
            )

        # 3. Connect via BleakClient (routed through proxy transparently)
        self._client = BleakClient(
            device,
            disconnected_callback=self._on_disconnect,
        )

        logger.info("Connecting BLE to %s...", self._lock_address)
        await self._client.connect()

        self._mtu = self._client.mtu_size
        logger.info("BLE connected (MTU: %d)", self._mtu)

        # 4. Subscribe to notifications (same as TedeeBLETransport)
        await self._client.start_notify(CHAR_PTLS_TX, self._on_ptls_tx)
        await self._client.start_notify(CHAR_NOTIFICATIONS, self._on_notification)
        await self._client.start_notify(CHAR_API_COMMANDS, self._on_api_command)

        self._connected = True
        logger.info("Subscribed to all characteristics via ESPHome proxy")

    def _on_disconnect(self, client: BleakClient) -> None:
        logger.warning("BLE disconnected from %s", self._lock_address)
        self._connected = False

    def _on_ptls_tx(self, _sender: int, data: bytearray) -> None:
        logger.debug("PTLS TX: %s", data.hex())
        self._ptls_tx_queue.put_nowait(bytes(data))

    def _on_notification(self, _sender: int, data: bytearray) -> None:
        logger.debug("Notification received: len=%d, data=%s", len(data), data.hex())
        self._notification_queue.put_nowait(bytes(data))

    def _on_api_command(self, _sender: int, data: bytearray) -> None:
        logger.debug("API Command response: len=%d, data=%s", len(data), data.hex())
        self._api_command_queue.put_nowait(bytes(data))

    async def disconnect(self) -> None:
        """Disconnect from the lock."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._connected = False
        logger.info("Disconnected")

    async def stop_proxy(self) -> None:
        """Stop the proxy manager (call on exit)."""
        global _proxy_manager
        if self._manager:
            await self._manager.stop()
            _proxy_manager = None

    async def write_ptls_rx(self, data: bytes) -> None:
        """Write to PTLS RX characteristic (client -> lock)."""
        logger.debug("PTLS RX write (%d bytes): %s", len(data), data.hex())
        await self._client.write_gatt_char(CHAR_PTLS_RX, data, response=False)

    async def write_api_command(self, data: bytes) -> None:
        """Write to API Commands characteristic."""
        logger.debug("API Command write: %s", data.hex())
        await self._client.write_gatt_char(CHAR_API_COMMANDS, data, response=True)

    async def read_ptls_tx(self, timeout: float = 10.0) -> bytes:
        return await asyncio.wait_for(self._ptls_tx_queue.get(), timeout=timeout)

    async def read_notification(self, timeout: float = 10.0) -> bytes:
        return await asyncio.wait_for(self._notification_queue.get(), timeout=timeout)

    async def read_api_command(self, timeout: float = 10.0) -> bytes:
        return await asyncio.wait_for(self._api_command_queue.get(), timeout=timeout)

    def drain_queues(self) -> None:
        for q in (self._ptls_tx_queue, self._notification_queue, self._api_command_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break


async def esphome_scan(
    proxy_host: str,
    port: int = 6053,
    password: str = "",
    noise_psk: str | None = None,
    timeout: float = 10.0,
    serial: str | None = None,
) -> list[dict]:
    """Scan for Tedee locks via ESPHome BT proxy."""
    TEDEE_PREFIX = "0000-4899-489f-a301-"
    target_uuid = serial_to_service_uuid(serial).lower() if serial else None

    manager = await get_proxy_manager(proxy_host, port, password, noise_psk)

    # Give scanner time to collect advertisements
    await asyncio.sleep(min(timeout, 5))
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

    found = []
    for d, adv in devices.values():
        uuids = [str(u).lower() for u in (adv.service_uuids or [])]
        if target_uuid and target_uuid in uuids:
            found.append({"address": d.address, "name": d.name or "unknown", "rssi": adv.rssi})
        elif any(TEDEE_PREFIX in u for u in uuids):
            found.append({"address": d.address, "name": d.name or "unknown", "rssi": adv.rssi})

    await manager.stop()
    return found
