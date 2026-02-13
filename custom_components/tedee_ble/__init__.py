"""Tedee BLE integration for Home Assistant.

Controls Tedee locks over Bluetooth Low Energy without the Tedee bridge.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import TedeeCoordinator

logger = logging.getLogger(__name__)

PLATFORMS = ["lock", "binary_sensor", "sensor"]

CARD_JS = "tedee-lock-card.js"
CARD_URL = f"/tedee-ble/{CARD_JS}"
CARD_VERSION = "1.0.3"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the Tedee Lock Card as a Lovelace resource."""
    js_path = Path(__file__).parent / "www" / CARD_JS

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(js_path), True)]
    )

    # Auto-register as a lovelace resource (module) so users don't have to.
    resources = hass.data["lovelace"].resources
    if resources is not None:
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True

        resource_url = f"{CARD_URL}?v={CARD_VERSION}"

        existing_id = None
        needs_update = False
        for item in resources.async_items():
            if item["url"].startswith(CARD_URL):
                existing_id = item["id"]
                if not item["url"].endswith(CARD_VERSION):
                    needs_update = True
                break

        if existing_id and needs_update:
            if isinstance(resources, ResourceStorageCollection):
                await resources.async_update_item(
                    existing_id, {"res_type": "module", "url": resource_url}
                )
        elif not existing_id:
            if isinstance(resources, ResourceStorageCollection):
                await resources.async_create_item(
                    {"res_type": "module", "url": resource_url}
                )
            elif getattr(resources, "data", None) and getattr(
                resources.data, "append", None
            ):
                resources.data.append({"type": "module", "url": resource_url})

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tedee BLE from a config entry."""
    coordinator = TedeeCoordinator(hass, entry)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Tedee BLE config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: TedeeCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
