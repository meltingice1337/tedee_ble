"""Binary sensors for Tedee BLE integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_ID,
    CONF_HAS_DOOR_SENSOR,
    CONF_LOCK_MODEL,
    CONF_LOCK_NAME,
    CONF_SERIAL,
    CONF_UPDATE_AVAILABLE,
    DOMAIN,
)
from .coordinator import TedeeCoordinator, async_remove_stale_entity
from .tedee_lib.lock_commands import DOOR_STATE_OPEN, DOOR_STATE_UNKNOWN, LOCK_STATE_UPDATING

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tedee binary sensors."""
    coordinator: TedeeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    # Same gate as the door sensor's battery: with no sensor paired the lock
    # never reports a door state, so this entity could only ever be unavailable.
    if entry.data.get(CONF_HAS_DOOR_SENSOR):
        entities.append(TedeeDoorSensor(coordinator, entry))
    else:
        async_remove_stale_entity(
            hass, Platform.BINARY_SENSOR, f"{entry.data[CONF_DEVICE_ID]}_door"
        )
    entities.append(TedeeBatteryChargingSensor(coordinator, entry))
    entities.append(TedeeFirmwareUpdateSensor(coordinator, entry))
    async_add_entities(entities)


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
            model=entry.data.get(CONF_LOCK_MODEL, "Lock"),
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


class TedeeFirmwareUpdateSensor(CoordinatorEntity[TedeeCoordinator], BinarySensorEntity):
    """Firmware update status — on while an update is available or being applied."""

    _attr_has_entity_name = True
    _attr_translation_key = "firmware_update"
    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TedeeCoordinator, entry: ConfigEntry) -> None:
        """Initialize the firmware update sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_firmware_update"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.data[CONF_DEVICE_ID]))},
            name=entry.data.get(CONF_LOCK_NAME, "Tedee Lock"),
            manufacturer="Tedee",
            model=entry.data.get(CONF_LOCK_MODEL, "Lock"),
            serial_number=entry.data.get(CONF_SERIAL),
        )

    @property
    def is_on(self) -> bool:
        return (
            self._entry.data.get(CONF_UPDATE_AVAILABLE, False)
            or self.coordinator.state.lock_state == LOCK_STATE_UPDATING
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        if self.coordinator.state.lock_state == LOCK_STATE_UPDATING:
            status = "updating"
        elif self._entry.data.get(CONF_UPDATE_AVAILABLE, False):
            status = "available"
        else:
            status = "idle"
        return {"status": status}


class TedeeBatteryChargingSensor(CoordinatorEntity[TedeeCoordinator], BinarySensorEntity):
    """Whether the lock's battery is charging.

    The same value is also exposed as a `charging` attribute on the battery
    sensor, kept for backwards compatibility. This entity is what Home
    Assistant's `battery.started_charging` / `battery.stopped_charging`
    triggers and `battery.is_charging` condition actually bind to — they
    dispatch on domain + device class and cannot read attributes.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "battery_charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TedeeCoordinator, entry: ConfigEntry) -> None:
        """Initialize the battery charging sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_battery_charging"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.data[CONF_DEVICE_ID]))},
            name=entry.data.get(CONF_LOCK_NAME, "Tedee Lock"),
            manufacturer="Tedee",
            model=entry.data.get(CONF_LOCK_MODEL, "Lock"),
            serial_number=entry.data.get(CONF_SERIAL),
        )

    @property
    def available(self) -> bool:
        """Return True if the sensor is available.

        `battery_charging` defaults to False on the coordinator, so without a
        battery reading behind it we would assert "not charging" having never
        confirmed it. Mirror the battery sensor and report unavailable instead.
        """
        return (
            self.coordinator.state.available
            and self.coordinator.state.battery_level is not None
        )

    @property
    def is_on(self) -> bool:
        """Return True if the battery is charging."""
        return self.coordinator.state.battery_charging
