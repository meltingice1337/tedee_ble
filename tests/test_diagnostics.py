"""Tests for the diagnostics download."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.tedee_ble import diagnostics
from custom_components.tedee_ble.const import (
    CONF_ADDRESS,
    CONF_API_KEY,
    CONF_DEVICE_ID,
    CONF_MOBILE_ID,
    CONF_PRIVATE_KEY_PEM,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.tedee_ble.coordinator import TedeeCoordinator
from custom_components.tedee_ble.tedee_lib.cloud_api import CloudAPIError

from .conftest import ADDRESS, DEVICE_ID, SERIAL, SERVICE_UUID

KEYPAD_ID = 555001
KEYPAD_SERIAL = "25181021-000123"
KEYPAD_ADDRESS = "C4:11:22:33:44:55"
SECRETS = ("the-api-key", "the-private-key", "mobile-1", SERIAL, KEYPAD_SERIAL,
           ADDRESS, KEYPAD_ADDRESS, "Alice", "alice@example.com", "Front door")


class FakeCloudAPI:
    """Cloud with one lock that has a Keypad PRO paired."""

    def __init__(self, api_key: str) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get_raw(self, path: str, **params) -> dict:
        self.calls.append((path, params))
        if path == "/my/device/details":
            return {"result": {
                "locks": [{
                    "id": DEVICE_ID, "type": 4, "serialNumber": SERIAL, "name": "Front door",
                    "lockAccessories": [
                        {"id": KEYPAD_ID, "type": 10, "serialNumber": KEYPAD_SERIAL,
                         "name": "Keypad", "macAddress": KEYPAD_ADDRESS, "batteryLevel": 80},
                    ],
                }],
            }}
        if path == "/my/deviceactivity":
            return {"result": [{"event": 77, "userId": 9, "username": "Alice",
                                "userEmail": "alice@example.com", "pinAlias": "Alice"}]}
        if path == "/my/devicecertificate/getformobile":
            assert params == {"mobileId": "mobile-1", "deviceId": KEYPAD_ID}
            return {"result": {"certificate": "CERT", "devicePublicKey": "PUB",
                               "expirationDate": "2027-01-01T00:00:00Z"}}
        if path == f"/my/keypad/{KEYPAD_ID}":
            return {"result": {"id": KEYPAD_ID, "type": 10, "softwareVersions": [{"version": "1.2"}]}}
        raise CloudAPIError(404, "not found")


def _advert(address, uuids, name, source="AA:BB:CC:DD:EE:01"):
    return SimpleNamespace(
        address=address, service_uuids=uuids, name=name, rssi=-70, connectable=True,
        source=source, time=0.0, manufacturer_data={741: b"\x01\x02"}, service_data={},
    )


async def test_diagnostics_reports_accessories_without_secrets(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(DEVICE_ID),
        data={
            CONF_ADDRESS: ADDRESS, CONF_DEVICE_ID: DEVICE_ID, CONF_SERIAL: SERIAL,
            CONF_API_KEY: "the-api-key", CONF_MOBILE_ID: "mobile-1",
            CONF_PRIVATE_KEY_PEM: "the-private-key",
        },
    )
    entry.add_to_hass(hass)
    coordinator = TedeeCoordinator(hass, entry)
    coordinator.notification_log.append((1763550000.0, "d550691d9a000008777900"))
    coordinator.state.last_user = "Alice"
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    scanner = SimpleNamespace(name="proxy (AA:BB:CC:DD:EE:01)", connectable=True)
    adverts = [
        _advert(ADDRESS, [SERVICE_UUID, "00000002-4899-489f-a301-fbee544b1db0"], "GO-C1A3"),
        _advert(KEYPAD_ADDRESS, ["25180000-1021-0001-2300-000000000000"], "KP"),
        _advert("11:22:33:44:55:66", ["0000180f-0000-1000-8000-00805f9b34fb"], "other"),
    ]
    with (
        patch.object(diagnostics, "TedeeCloudAPI", FakeCloudAPI),
        patch.object(diagnostics.bluetooth, "async_discovered_service_info", return_value=adverts),
        patch.object(
            diagnostics.bluetooth, "async_scanner_devices_by_address",
            return_value=[SimpleNamespace(scanner=scanner, advertisement=SimpleNamespace(rssi=-70))],
        ),
        patch.object(diagnostics.bluetooth, "async_scanner_count", return_value=2),
    ):
        result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    dumped = json.dumps(result)
    for secret in SECRETS:
        assert secret not in dumped

    assert result["coordinator"]["notifications"] == [
        {"time": 1763550000.0, "data": "d550691d9a000008777900"}
    ]
    cloud = result["cloud"]
    accessory = cloud["device_details"]["result"]["locks"][0]["lockAccessories"][0]
    assert accessory["id"] == KEYPAD_ID and accessory["type"] == 10
    assert accessory["batteryLevel"] == 80
    assert cloud["lock_activity"]["result"][0]["event"] == 77
    assert cloud["lock_accessories"]["ok"] is False

    probe = cloud["accessory_probes"][str(KEYPAD_ID)]
    assert probe["keypad"]["ok"] is True
    assert probe["mobile_certificate"] == {
        "ok": True,
        "fields": ["certificate", "devicePublicKey", "expirationDate"],
        "expirationDate": "2027-01-01T00:00:00Z",
    }

    heard = result["bluetooth"]["advertisements"]
    assert [a["device"] for a in heard] == [
        ["lock id 306792 (type 4)"], ["Keypad PRO id 555001 (type 10)"]
    ]
    assert heard[1]["device_type_from_serial_uuid"] == [10]
    assert heard[0]["address"] == "F8:F6:15:XX:XX:XX"
    assert list(result["bluetooth"]["heard_by"]) == ["C4:11:22:XX:XX:XX", "F8:F6:15:XX:XX:XX"]


async def test_diagnostics_without_coordinator_or_cloud(hass: HomeAssistant) -> None:
    """Still produced while setup is retrying and the cloud is unreachable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_DEVICE_ID: DEVICE_ID, CONF_SERIAL: SERIAL,
              CONF_API_KEY: "the-api-key", CONF_MOBILE_ID: "mobile-1"},
    )
    entry.add_to_hass(hass)

    class DownCloud(FakeCloudAPI):
        async def get_raw(self, path, **params):
            raise CloudAPIError(503, "down")

    with (
        patch.object(diagnostics, "TedeeCloudAPI", DownCloud),
        patch.object(diagnostics.bluetooth, "async_discovered_service_info", return_value=[]),
        patch.object(diagnostics.bluetooth, "async_scanner_devices_by_address", return_value=[]),
        patch.object(diagnostics.bluetooth, "async_scanner_count", return_value=0),
    ):
        result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    assert "coordinator" not in result
    assert result["cloud"]["device_details"] == {"ok": False, "error": "CloudAPIError: HTTP 503: down"}
    assert result["cloud"]["accessory_probes"] == {}
    assert result["bluetooth"]["advertisements"] == []
