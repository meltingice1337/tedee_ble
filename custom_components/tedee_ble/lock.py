"""Lock entity for Tedee BLE integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AUTO_PULL, CONF_DEVICE_ID, CONF_FIRMWARE_VERSION, CONF_LOCK_MODEL, CONF_LOCK_NAME, CONF_SERIAL, DOMAIN
from .coordinator import TedeeCoordinator
from .tedee_lib.lock_commands import (
    LOCK_STATE_LOCKED,
    LOCK_STATE_LOCKING,
    LOCK_STATE_UNLOCKING,
    LOCK_STATE_UPDATING,
    STATUS_JAMMED,
)

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tedee lock entity."""
    coordinator: TedeeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TedeeLockEntity(coordinator, entry)])


class TedeeLockEntity(CoordinatorEntity[TedeeCoordinator], LockEntity):
    """Representation of a Tedee lock."""

    _attr_has_entity_name = True
    _attr_translation_key = "lock"
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: TedeeCoordinator, entry: ConfigEntry) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_lock"

    async def async_added_to_hass(self) -> None:
        """Register entity_id with coordinator for logbook entries."""
        await super().async_added_to_hass()
        self.coordinator.lock_entity_id = self.entity_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info, reading firmware version dynamically."""
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._entry.data[CONF_DEVICE_ID]))},
            name=self._entry.data.get(CONF_LOCK_NAME, "Tedee Lock"),
            manufacturer="Tedee",
            model=self._entry.data.get(CONF_LOCK_MODEL, "Lock"),
            serial_number=self._entry.data.get(CONF_SERIAL),
            sw_version=self._entry.data.get(CONF_FIRMWARE_VERSION),
        )

    @property
    def available(self) -> bool:
        """Return True if the lock is available."""
        return self.coordinator.state.available

    @property
    def _is_updating(self) -> bool:
        return self.coordinator.state.lock_state == LOCK_STATE_UPDATING

    @property
    def is_locked(self) -> bool | None:
        """Return True if the lock is locked."""
        if not self.available or self._is_updating:
            return None
        return self.coordinator.state.lock_state == LOCK_STATE_LOCKED

    @property
    def is_locking(self) -> bool:
        """Return True if the lock is locking."""
        return self.coordinator.state.lock_state == LOCK_STATE_LOCKING

    @property
    def is_unlocking(self) -> bool:
        """Return True if the lock is unlocking."""
        return self.coordinator.state.lock_state == LOCK_STATE_UNLOCKING

    @property
    def is_jammed(self) -> bool:
        """Return True if the lock is jammed."""
        return self.coordinator.state.lock_status == STATUS_JAMMED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional lock attributes."""
        return {
            "last_action": self.coordinator.state.last_event_type or None,
            "last_trigger": self.coordinator.state.last_trigger,
            "last_user": self.coordinator.state.last_user,
            "is_updating": self._is_updating,
        }

    def _guard_updating(self) -> None:
        if self._is_updating:
            raise HomeAssistantError(
                f"{self.coordinator.lock_name} is applying a firmware update"
            )

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door."""
        self._guard_updating()
        await self.coordinator.async_lock()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door. Also pulls spring if auto_pull option is enabled."""
        self._guard_updating()
        await self.coordinator.async_unlock(
            auto_pull=self.coordinator.entry.options.get(CONF_AUTO_PULL, False),
        )

    async def async_open(self, **kwargs: Any) -> None:
        """Open the door (pull spring)."""
        self._guard_updating()
        await self.coordinator.async_open()
