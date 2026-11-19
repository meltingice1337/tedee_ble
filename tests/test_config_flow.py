"""Tests for the BLE address step of the config flow."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tedee_ble.const import CONF_ADDRESS, DOMAIN

from .conftest import ADDRESS, CLOUD_LOCK, DEVICE_ID, SERVICE_UUID, service_info

FLOW = "custom_components.tedee_ble.config_flow"


class FakeCloudAPI:
    """TedeeCloudAPI stand-in: one lock on the account, registration succeeds."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get_devices(self):
        return [CLOUD_LOCK]

    async def register_mobile(self, public_key_b64):
        return "mobile-1"

    async def get_device_certificate(self, mobile_id, device_id):
        return {
            "certificate": "cert",
            "expirationDate": "2027-01-01T00:00:00Z",
            "devicePublicKey": "device-key",
        }

    async def get_signed_time(self):
        return {"datetime": "2026-11-19T10:00:00Z", "signature": "sig"}

    async def get_user_map(self, device_id):
        return {}


@pytest.fixture
def bluetooth():
    """Patch the Home Assistant Bluetooth helpers the config flow uses.

    Defaults: HA has heard nothing, no advertisement arrives in time, and no
    address is present. Tests override what they need.
    """
    with (
        patch(f"{FLOW}.TedeeCloudAPI", FakeCloudAPI),
        patch(f"{FLOW}.async_discovered_service_info", return_value=[]) as discovered,
        patch(
            f"{FLOW}.async_process_advertisements",
            AsyncMock(side_effect=asyncio.TimeoutError),
        ) as process,
        patch(f"{FLOW}.async_address_present", return_value=False) as present,
        patch("custom_components.tedee_ble.async_setup", return_value=True),
        patch("custom_components.tedee_ble.async_setup_entry", return_value=True),
    ):
        yield MagicMock(discovered=discovered, process=process, present=present)


async def _start_flow_to_scan(hass: HomeAssistant):
    """Run the user and lock-picker steps; the next step is the BLE scan."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "key"}
    )
    assert result["step_id"] == "select_lock"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"lock_selection": str(DEVICE_ID)}
    )


def _suggested_address(result) -> str | None:
    for key in result["data_schema"].schema:
        if key == CONF_ADDRESS:
            return (key.description or {}).get("suggested_value")
    raise AssertionError("address field missing")


async def test_scan_uses_most_recently_heard_cached_address(
    hass: HomeAssistant, bluetooth
) -> None:
    """A lock HA has already heard is used without waiting for a new scan."""
    bluetooth.discovered.return_value = [
        service_info("AA:AA:AA:AA:AA:01", time=10.0),
        service_info("aa:aa:aa:aa:aa:02", time=20.0),
        service_info("BB:BB:BB:BB:BB:BB", time=30.0, uuids=["0000180f-0000-1000-8000-00805f9b34fb"]),
    ]

    result = await _start_flow_to_scan(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == "AA:AA:AA:AA:AA:02"
    assert bluetooth.discovered.call_args.kwargs == {"connectable": True}
    bluetooth.process.assert_not_called()


async def test_scan_waits_for_an_advertisement(hass: HomeAssistant, bluetooth) -> None:
    """With nothing cached, the scan waits for the lock to advertise."""
    bluetooth.process.side_effect = None
    bluetooth.process.return_value = service_info("f8:f6:15:d2:aa:9d")

    result = await _start_flow_to_scan(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS
    _, _, matcher, _, timeout = bluetooth.process.call_args.args
    assert matcher["service_uuid"] == SERVICE_UUID
    assert matcher["connectable"] is True
    assert timeout == 15


async def test_scan_timeout_shows_form(hass: HomeAssistant, bluetooth) -> None:
    """A lock that is not heard leaves the user on the address form."""
    result = await _start_flow_to_scan(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ble_address"
    assert result["errors"] == {"base": "scan_failed"}
    assert result["description_placeholders"]["serial"] == CLOUD_LOCK["serialNumber"]
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_invalid_mac(hass: HomeAssistant, bluetooth) -> None:
    result = await _start_flow_to_scan(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "12:34"}
    )

    assert result["errors"] == {"base": "invalid_mac"}
    bluetooth.present.assert_not_called()


async def test_address_not_visible_keeps_input(hass: HomeAssistant, bluetooth) -> None:
    """A well-formed MAC HA cannot reach is refused before any cloud registration."""
    result = await _start_flow_to_scan(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: " f8:f6:15:d2:aa:9d "}
    )

    assert result["errors"] == {"base": "address_not_visible"}
    bluetooth.present.assert_called_once_with(hass, ADDRESS, connectable=True)
    assert _suggested_address(result) == ADDRESS
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_visible_manual_address_creates_entry(hass: HomeAssistant, bluetooth) -> None:
    result = await _start_flow_to_scan(hass)
    bluetooth.present.return_value = True

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "f8:f6:15:d2:aa:9d"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["result"].unique_id == str(DEVICE_ID)


async def test_empty_submit_scans_again(hass: HomeAssistant, bluetooth) -> None:
    """Submitting the form empty rescans, e.g. after a proxy was fixed."""
    result = await _start_flow_to_scan(hass)
    assert result["errors"] == {"base": "scan_failed"}
    assert bluetooth.process.call_count == 1

    bluetooth.discovered.return_value = [service_info()]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS


async def test_empty_submit_without_lock_shows_form_again(
    hass: HomeAssistant, bluetooth
) -> None:
    result = await _start_flow_to_scan(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ""}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "scan_failed"}
    assert bluetooth.process.call_count == 2
