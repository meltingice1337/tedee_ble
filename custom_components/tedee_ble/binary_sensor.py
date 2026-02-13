"""Binary sensor (door) for Tedee BLE integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, CONF_LOCK_NAME, CONF_SERIAL, DOMAIN
from .coordinator import TedeeCoordinator
from .tedee_lib.lock_commands import DOOR_STATE_OPEN, DOOR_STATE_UNKNOWN

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tedee door binary sensor."""
    coordinator: TedeeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TedeeDoorSensor(coordinator, entry)])


class TedeeDoorSensor(CoordinatorEntity[TedeeCoordinator], BinarySensorEntity):
    """Representation of a Tedee door sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "door"
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, coordinator: TedeeCoordinator, entry: ConfigEntry) -> None:
        """Initialize the door sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_door"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.data[CONF_DEVICE_ID]))},
            name=entry.data.get(CONF_LOCK_NAME, "Tedee Lock"),
            manufacturer="Tedee",
            model="GO 2",
            serial_number=entry.data.get(CONF_SERIAL),
        )

    @property
    def available(self) -> bool:
        """Return True if the sensor is available."""
        return (
            self.coordinator.state.available
            and self.coordinator.state.door_state != DOOR_STATE_UNKNOWN
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the door is open."""
        if self.coordinator.state.door_state == DOOR_STATE_UNKNOWN:
            return None
        return self.coordinator.state.door_state == DOOR_STATE_OPEN
