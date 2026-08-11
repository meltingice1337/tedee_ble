"""Battery sensor for Tedee BLE integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
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
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_ID,
    CONF_HAS_DOOR_SENSOR,
    CONF_LOCK_MODEL,
    CONF_LOCK_NAME,
    CONF_SERIAL,
    DOMAIN,
)
from .coordinator import TedeeCoordinator

logger = logging.getLogger(__name__)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Device info shared by this platform's entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, str(entry.data[CONF_DEVICE_ID]))},
        name=entry.data.get(CONF_LOCK_NAME, "Tedee Lock"),
        manufacturer="Tedee",
        model=entry.data.get(CONF_LOCK_MODEL, "Lock"),
        serial_number=entry.data.get(CONF_SERIAL),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tedee sensors."""
    coordinator: TedeeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [TedeeBatterySensor(coordinator, entry)]
    # Only locks with a door sensor paired can ever report its battery, and the
    # coordinator has already probed for one by the time platforms are set up.
    # Without this the entity would sit unavailable forever on most locks.
    if entry.data.get(CONF_HAS_DOOR_SENSOR):
        entities.append(TedeeDoorSensorBatterySensor(coordinator, entry))
    async_add_entities(entities)


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
        self._attr_device_info = _device_info(entry)

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


class TedeeDoorSensorBatterySensor(CoordinatorEntity[TedeeCoordinator], RestoreSensor):
    """Battery level of the paired door sensor.

    The lock only ever volunteers this (notification 0xD5) — there is no command
    to ask for it, and in practice it arrives rarely. So the last value is
    restored across restarts rather than showing unknown until the next push.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "door_sensor_battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TedeeCoordinator, entry: ConfigEntry) -> None:
        """Initialize the door sensor battery sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_door_sensor_battery"
        self._attr_device_info = _device_info(entry)
        self._restored_level: int | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last known level, since a fresh push may be hours away."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._restored_level = int(last.native_value)
            except (TypeError, ValueError):
                self._restored_level = None

    @property
    def _level(self) -> int | None:
        # Explicit None check: 0% is a valid (and important) reading.
        live = self.coordinator.state.accessory_battery_level
        return live if live is not None else self._restored_level

    @property
    def available(self) -> bool:
        """Available once a level is known; a stale level still beats nothing."""
        return self._level is not None

    @property
    def native_value(self) -> int | None:
        """Return the door sensor battery level."""
        return self._level

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose provenance so a stale reading is recognisable as stale."""
        state = self.coordinator.state
        return {
            "accessory_id": state.accessory_battery_id,
            "last_reported": (
                dt_util.utc_from_timestamp(
                    state.accessory_battery_timestamp
                ).isoformat()
                if state.accessory_battery_timestamp
                else None
            ),
            "restored": state.accessory_battery_level is None,
        }
