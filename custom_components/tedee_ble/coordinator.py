"""Coordinator for Tedee BLE lock integration.

Manages BLE connection lifecycle, notification handling, polling,
certificate refresh, and command dispatch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from homeassistant.components.bluetooth import async_ble_device_from_address

from .const import (
    CERT_CHECK_INTERVAL_SECONDS,
    CONF_ADDRESS,
    CONF_API_KEY,
    CONF_CERT_EXPIRATION,
    CONF_CERTIFICATE,
    CONF_DEVICE_ID,
    CONF_DEVICE_PUBLIC_KEY,
    CONF_LOCK_NAME,
    CONF_MOBILE_ID,
    CONF_PRIVATE_KEY_PEM,
    CONF_SERIAL,
    CONF_SIGNED_TIME,
    CONF_USER_MAP,
    DOMAIN,
    KEEPALIVE_INTERVAL_SECONDS,
    POLL_INTERVAL_SECONDS,
    RECONNECT_DELAYS,
)
from .tedee_lib.ble import TedeeBLETransport
from .tedee_lib.cloud_api import TedeeCloudAPI, certificate_needs_refresh
from .tedee_lib.crypto import pem_to_private_key
from .tedee_lib.lock_commands import (
    DOOR_STATE_UNKNOWN,
    LOCK_STATE_LOCKED,
    LOCK_STATE_LOCKING,
    LOCK_STATE_UNLOCKED,
    LOCK_STATE_UNKNOWN,
    LOCK_STATE_UNLOCKING,
    STATUS_OK,
    TedeeLock,
)
from .tedee_lib.ptls import (
    ALERT_INVALID_CERTIFICATE,
    ALERT_NO_TRUSTED_TIME,
    PTLSAlertError,
    PTLSSession,
)

logger = logging.getLogger(__name__)


@dataclass
class TedeeState:
    """Current state of the Tedee lock."""

    lock_state: int = LOCK_STATE_UNKNOWN
    lock_status: int = STATUS_OK  # 0=ok, 1=jammed
    door_state: int = DOOR_STATE_UNKNOWN
    battery_level: int | None = None
    battery_charging: bool = False
    available: bool = False
    last_trigger: str = "unknown"  # What caused the last state change
    last_user: str = ""  # Who triggered the last action


class TedeeCoordinator(DataUpdateCoordinator[TedeeState]):
    """Coordinator for a single Tedee BLE lock."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            logger,
            name=f"Tedee {entry.data.get(CONF_LOCK_NAME, 'Lock')}",
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
        )
        self.entry = entry

        # BLE/session objects
        self._transport: TedeeBLETransport | None = None
        self._session: PTLSSession | None = None
        self._lock: TedeeLock | None = None

        # Connection management
        self._connecting_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._notification_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_attempt: int = 0
        self._shutting_down: bool = False

        # Certificate check timing
        self._last_cert_check: float = 0

        # BLE activity tracking for keep-alive
        self._last_ble_activity: float = 0.0

        # State
        self.state = TedeeState()

    @property
    def device_id(self) -> int:
        return self.entry.data[CONF_DEVICE_ID]

    @property
    def serial(self) -> str:
        return self.entry.data.get(CONF_SERIAL, "")

    @property
    def lock_name(self) -> str:
        return self.entry.data.get(CONF_LOCK_NAME, "Lock")

    @property
    def is_connected(self) -> bool:
        return (
            self._transport is not None
            and self._transport.is_connected
            and self._session is not None
            and self._session.is_established
        )

    async def async_setup(self) -> None:
        """Set up the coordinator — connect to the lock."""
        try:
            await self._connect()
        except Exception as err:
            logger.error("Failed to connect to %s: %s", self.lock_name, err)
            raise ConfigEntryNotReady(
                f"Could not connect to {self.lock_name}: {err}"
            ) from err

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        self._shutting_down = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self._notification_task and not self._notification_task.done():
            self._notification_task.cancel()
        await self._disconnect()
        await super().async_shutdown()

    def _resolve_ble_device(self, address: str) -> object:
        """Resolve BLEDevice from HA Bluetooth stack, fall back to address string."""
        ble_device = async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device:
            logger.debug("Resolved BLEDevice: %s", ble_device.name)
        else:
            logger.debug("BLEDevice not found, using address string")
        return ble_device or address

    async def _connect(self) -> None:
        """Full connection sequence: cert refresh → BLE → PTLS → init."""
        async with self._connecting_lock:
            if self.is_connected:
                logger.debug("Already connected to %s, skipping", self.lock_name)
                return

            logger.info("Connecting to %s (%s)...", self.lock_name, self.entry.data[CONF_ADDRESS])

            # Refresh certificate if needed
            await self._refresh_certificate_if_needed()

            data = self.entry.data

            # Create BLE transport
            self._transport = TedeeBLETransport(
                self._resolve_ble_device(data[CONF_ADDRESS]),
                disconnect_callback=self._on_disconnect,
            )
            await self._transport.connect()

            # Create PTLS session and handshake
            private_key = pem_to_private_key(data[CONF_PRIVATE_KEY_PEM].encode())
            self._session = PTLSSession(
                self._transport,
                private_key,
                data[CONF_CERTIFICATE],
                data[CONF_DEVICE_PUBLIC_KEY],
            )

            _needs_signed_time = False
            try:
                await self._session.handshake()
            except PTLSAlertError as err:
                if err.code == ALERT_INVALID_CERTIFICATE:
                    logger.warning("Certificate rejected, forcing refresh...")
                    await self._transport.disconnect()
                    await self._force_refresh_certificate()
                    data = self.entry.data  # re-read after update
                    self._transport = TedeeBLETransport(
                        self._resolve_ble_device(data[CONF_ADDRESS]),
                        disconnect_callback=self._on_disconnect,
                    )
                    await self._transport.connect()
                    private_key = pem_to_private_key(data[CONF_PRIVATE_KEY_PEM].encode())
                    self._session = PTLSSession(
                        self._transport,
                        private_key,
                        data[CONF_CERTIFICATE],
                        data[CONF_DEVICE_PUBLIC_KEY],
                    )
                    await self._session.handshake()
                elif err.code == ALERT_NO_TRUSTED_TIME:
                    logger.warning("Lock has no trusted time, fetching and retrying...")
                    await self._transport.disconnect()
                    await self._refresh_signed_time()
                    data = self.entry.data
                    self._transport = TedeeBLETransport(
                        self._resolve_ble_device(data[CONF_ADDRESS]),
                        disconnect_callback=self._on_disconnect,
                    )
                    await self._transport.connect()
                    private_key = pem_to_private_key(data[CONF_PRIVATE_KEY_PEM].encode())
                    self._session = PTLSSession(
                        self._transport,
                        private_key,
                        data[CONF_CERTIFICATE],
                        data[CONF_DEVICE_PUBLIC_KEY],
                    )
                    await self._session.handshake()
                    _needs_signed_time = True
                else:
                    raise

            # Create lock command interface
            self._lock = TedeeLock(
                self._transport,
                self._session,
                initial_door_state=self.state.door_state,
            )

            # Only set signed time when the lock requested it
            if _needs_signed_time:
                await self._lock.set_signed_time(data[CONF_SIGNED_TIME])

            # Drain stale notifications
            await self._lock.drain_pending_notifications()

            # Fetch initial state
            try:
                lock_state, status, door_state = await self._lock.get_state()
                self.state.lock_state = lock_state
                self.state.lock_status = status
                if door_state != DOOR_STATE_UNKNOWN:
                    self.state.door_state = door_state
            except Exception:
                logger.warning("Failed to get initial lock state", exc_info=True)

            try:
                level, charging = await self._lock.get_battery()
                self.state.battery_level = level
                self.state.battery_charging = charging
            except Exception:
                logger.warning("Failed to get initial battery", exc_info=True)

            # Mark available and notify entities
            self.state.available = True
            self._reconnect_attempt = 0
            self.async_set_updated_data(self.state)

            # Start notification listener
            self._notification_task = self.hass.async_create_background_task(
                self._notification_loop(),
                f"tedee_ble_{self.device_id}_notifications",
            )

            logger.info("Connected to %s successfully", self.lock_name)

    async def _disconnect(self) -> None:
        """Disconnect from the lock."""
        if self._notification_task and not self._notification_task.done():
            self._notification_task.cancel()
            try:
                await self._notification_task
            except asyncio.CancelledError:
                pass
            self._notification_task = None

        if self._transport:
            try:
                await self._transport.disconnect()
            except Exception:
                pass
            self._transport = None

        self._session = None
        self._lock = None

    @callback
    def _on_disconnect(self) -> None:
        """Handle BLE disconnection."""
        logger.warning("BLE disconnected from %s", self.lock_name)
        self.state.available = False
        self.async_set_updated_data(self.state)

        if not self._shutting_down:
            self._schedule_reconnect()

    @callback
    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with backoff."""
        if self._reconnect_task and not self._reconnect_task.done():
            return  # Already scheduled

        delay_idx = min(self._reconnect_attempt, len(RECONNECT_DELAYS) - 1)
        delay = RECONNECT_DELAYS[delay_idx]
        self._reconnect_attempt += 1

        logger.info(
            "Scheduling reconnect to %s in %ds (attempt %d)",
            self.lock_name, delay, self._reconnect_attempt,
        )
        self._reconnect_task = self.hass.async_create_background_task(
            self._reconnect(delay),
            f"tedee_ble_{self.device_id}_reconnect",
        )

    async def _reconnect(self, delay: float) -> None:
        """Wait and then attempt reconnection."""
        await asyncio.sleep(delay)
        try:
            await self._disconnect()
            await self._connect()
        except Exception as err:
            logger.warning("Reconnect to %s failed: %s", self.lock_name, err)
            if not self._shutting_down:
                self._schedule_reconnect()

    async def _notification_loop(self) -> None:
        """Background loop: listen for notifications + periodic keep-alive.

        The Tedee lock disconnects BLE after ~60-90s of inactivity.
        We send a get_state command every KEEPALIVE_INTERVAL_SECONDS to keep
        the connection alive and refresh state as a side-effect.
        """
        logger.debug("Notification loop started for %s", self.lock_name)
        self._last_ble_activity = time.monotonic()
        try:
            while self.is_connected:
                # Wait for notification, but only until next keep-alive is due
                elapsed = time.monotonic() - self._last_ble_activity
                wait_time = max(1.0, KEEPALIVE_INTERVAL_SECONDS - elapsed)

                try:
                    data = await self._transport.read_notification(timeout=wait_time)
                except asyncio.TimeoutError:
                    # No notification — send keep-alive get_state
                    if self._lock and self.is_connected:
                        try:
                            async with self._command_lock:
                                lock_state, status, door_state = (
                                    await self._lock.get_state()
                                )
                            self._last_ble_activity = time.monotonic()
                            self.state.lock_state = lock_state
                            self.state.lock_status = status
                            if door_state != DOOR_STATE_UNKNOWN:
                                self.state.door_state = door_state
                            self.async_set_updated_data(self.state)
                        except Exception as err:
                            logger.warning("Keep-alive failed: %s", err)
                            break
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    logger.warning("Notification read error: %s", err)
                    break

                # Notification received — connection is active
                self._last_ble_activity = time.monotonic()

                if self._lock is None:
                    continue

                notification = await self._lock.parse_notification(data)
                if notification is None:
                    continue

                logger.debug("Notification from %s: %s", self.lock_name, notification)

                if notification["type"] == "lock_state":
                    self.state.lock_state = notification["state"]
                    self.state.lock_status = notification["status"]
                    self.state.last_trigger = notification.get("trigger_name", "unknown")
                    # Resolve access_id to username
                    access_id = notification.get("access_id", 0)
                    if access_id:
                        user_map = self.entry.data.get(CONF_USER_MAP, {})
                        username = user_map.get(str(access_id))
                        if username is None:
                            # Unknown user - refresh map from cloud
                            username = await self._resolve_unknown_user(access_id)
                        self.state.last_user = username
                    else:
                        self.state.last_user = ""
                    if notification["door_state"] != DOOR_STATE_UNKNOWN:
                        self.state.door_state = notification["door_state"]
                    self.async_set_updated_data(self.state)


                elif notification["type"] == "need_datetime":
                    logger.info("Lock %s requests time sync", self.lock_name)
                    try:
                        await self._refresh_signed_time()
                        async with self._command_lock:
                            await self._lock.set_signed_time(
                                self.entry.data[CONF_SIGNED_TIME]
                            )
                        self._last_ble_activity = time.monotonic()
                    except Exception:
                        logger.warning("Failed to sync time", exc_info=True)

        except asyncio.CancelledError:
            pass
        logger.debug("Notification loop ended for %s", self.lock_name)

    async def _async_update_data(self) -> TedeeState:
        """Polling fallback — also checks certificate freshness."""
        # Check certificate periodically
        now = time.monotonic()
        if now - self._last_cert_check > CERT_CHECK_INTERVAL_SECONDS:
            self._last_cert_check = now
            try:
                await self._refresh_certificate_if_needed()
            except Exception:
                logger.warning("Certificate check failed", exc_info=True)

        # If not connected, try reconnecting (but skip if reconnect already in progress)
        if not self.is_connected:
            if self._reconnect_task and not self._reconnect_task.done():
                logger.debug("Reconnect already in progress, skipping poll reconnect")
                return self.state
            try:
                await self._disconnect()
                await self._connect()
            except Exception as err:
                logger.warning("Poll reconnect failed: %s", err)
                self.state.available = False
                return self.state

        # Fetch fresh state
        if self._lock and self.is_connected:
            try:
                async with self._command_lock:
                    lock_state, status, door_state = await self._lock.get_state()
                self.state.lock_state = lock_state
                self.state.lock_status = status
                if door_state != DOOR_STATE_UNKNOWN:
                    self.state.door_state = door_state
            except Exception:
                logger.warning("Failed to poll lock state", exc_info=True)

            try:
                async with self._command_lock:
                    level, charging = await self._lock.get_battery()
                self.state.battery_level = level
                self.state.battery_charging = charging
            except Exception:
                logger.warning("Failed to poll battery", exc_info=True)

        return self.state

    # ─── Command methods (called by entities) ────────────────────

    async def async_lock(self) -> None:
        """Lock the door."""
        await self._send_command("lock")

    async def async_unlock(self, auto_pull: bool = False) -> None:
        """Unlock the door. If auto_pull, also sends pull_spring after unlocking."""
        await self._send_command("unlock")
        if auto_pull:
            # Wait for the notification loop to report UNLOCKED (no BLE commands)
            for _ in range(30):
                await asyncio.sleep(0.5)
                if self.state.lock_state == LOCK_STATE_UNLOCKED:
                    await self._send_command("pull_spring")
                    return
                if not self.is_connected:
                    break
            logger.warning("Auto-pull: lock did not reach unlocked state within 15s")

    async def async_open(self) -> None:
        """Pull the spring (open)."""
        await self._send_command("pull_spring")


    async def _send_command(self, command: str) -> None:
        """Send a command to the lock with error handling."""
        if not self.is_connected or self._lock is None:
            raise HomeAssistantError(f"Not connected to {self.lock_name}")

        async with self._command_lock:
            try:
                method = getattr(self._lock, command)
                await method()
                self._last_ble_activity = time.monotonic()
            except Exception as err:
                logger.error("Command %s failed on %s: %s", command, self.lock_name, err)
                raise HomeAssistantError(
                    f"Command {command} failed: {err}"
                ) from err

    # ─── Certificate / signed time management ────────────────────

    async def _refresh_certificate_if_needed(self) -> None:
        """Check and refresh the certificate if it's expiring soon."""
        data = self.entry.data
        exp = data.get(CONF_CERT_EXPIRATION, "")
        if not certificate_needs_refresh(exp):
            return
        await self._force_refresh_certificate()

    async def _force_refresh_certificate(self) -> None:
        """Force refresh the certificate and user map from cloud API."""
        data = self.entry.data
        logger.info("Refreshing certificate for %s...", self.lock_name)
        async with TedeeCloudAPI(data[CONF_API_KEY]) as api:
            cert_data = await api.get_device_certificate(
                data[CONF_MOBILE_ID], data[CONF_DEVICE_ID]
            )
            user_map = await api.get_user_map(data[CONF_DEVICE_ID])
        new_data = {**data}
        new_data[CONF_CERTIFICATE] = cert_data["certificate"]
        new_data[CONF_CERT_EXPIRATION] = cert_data["expirationDate"]
        new_data[CONF_DEVICE_PUBLIC_KEY] = cert_data["devicePublicKey"]
        new_data[CONF_USER_MAP] = {str(k): v for k, v in user_map.items()}
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        logger.info("Certificate refreshed, expires %s", cert_data["expirationDate"])

    async def _resolve_unknown_user(self, access_id: int) -> str:
        """Refresh user map from cloud when an unknown access_id is seen."""
        data = self.entry.data
        try:
            async with TedeeCloudAPI(data[CONF_API_KEY]) as api:
                user_map = await api.get_user_map(data[CONF_DEVICE_ID])
            new_map = {str(k): v for k, v in user_map.items()}
            new_data = {**data}
            new_data[CONF_USER_MAP] = new_map
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            logger.debug("User map refreshed, now has %d users", len(new_map))
            return new_map.get(str(access_id), str(access_id))
        except Exception:
            logger.debug("Failed to refresh user map for access_id %d", access_id)
            return str(access_id)

    async def _refresh_signed_time(self) -> None:
        """Refresh signed time from cloud API."""
        data = self.entry.data
        async with TedeeCloudAPI(data[CONF_API_KEY]) as api:
            signed_time = await api.get_signed_time()
        new_data = {**data}
        new_data[CONF_SIGNED_TIME] = signed_time
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
