"""Shared fixtures for the Tedee BLE tests.

Run with Python 3.14 after
``pip install pytest-homeassistant-custom-component==0.13.375 bleak-retry-connector``
plus the requirements of homeassistant/components/bluetooth/manifest.json:
``python3 -m pytest``
"""

from types import SimpleNamespace

import pytest

from homeassistant.core import HomeAssistant

pytest_plugins = "pytest_homeassistant_custom_component"

SERIAL = "25180421-000894"
# serial_to_service_uuid(SERIAL), the per-lock UUID the lock advertises.
SERVICE_UUID = "25180000-0421-0008-9400-000000000000"
ADDRESS = "F8:F6:15:D2:AA:9D"
DEVICE_ID = 306792

CLOUD_LOCK = {
    "id": DEVICE_ID,
    "serialNumber": SERIAL,
    "name": "Lock",
    "type": 4,
    "softwareVersions": [{"version": "2.1.5795", "updateAvailable": False}],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/tedee_ble."""
    yield


@pytest.fixture(autouse=True)
def skip_dependencies(hass: HomeAssistant) -> None:
    """Mark the manifest dependencies as loaded.

    The tests patch the Bluetooth helpers the integration calls, so the real
    bluetooth/http/frontend/lovelace setup (which needs adapters and a web
    server) is not wanted.
    """
    hass.config.components.update({"bluetooth", "http", "frontend", "lovelace"})


def service_info(address: str = ADDRESS, time: float = 1.0, uuids=None):
    """A stand-in for BluetoothServiceInfoBleak with the fields the code reads."""
    return SimpleNamespace(
        address=address,
        service_uuids=[SERVICE_UUID] if uuids is None else uuids,
        time=time,
        device=SimpleNamespace(address=address, name="GO-C1A3"),
    )
