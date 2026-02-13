"""Battery sensor for Tedee BLE integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, CONF_LOCK_NAME, CONF_SERIAL, DOMAIN
from .coordinator import TedeeCoordinator

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tedee battery sensor."""
    coordinator: TedeeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TedeeBatterySensor(coordinator, entry)])


class TedeeBatterySensor(CoordinatorEntity[TedeeCoordinator], SensorEntity):
    """Representation of a Tedee battery sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TedeeCoordinator, entry: ConfigEntry) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_battery"
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
            and self.coordinator.state.battery_level is not None
        )

    @property
    def native_value(self) -> int | None:
        """Return the battery level."""
        return self.coordinator.state.battery_level

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional battery attributes."""
        return {"charging": self.coordinator.state.battery_charging}
