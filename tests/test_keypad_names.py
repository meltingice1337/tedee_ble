"""Tests for naming keypad PIN and fingerprint uses (issue #16)."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.tedee_ble.const import (
    CONF_API_KEY,
    CONF_DEVICE_ID,
    CONF_FINGERPRINT_READER,
    CONF_KEYPAD_NAMES,
    CONF_LOCK_NAME,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.tedee_ble.coordinator import TedeeCoordinator
from custom_components.tedee_ble.keypad_names import (
    METHOD_FINGERPRINT,
    METHOD_PIN,
    match_activity,
    parse_pin_list,
)
from custom_components.tedee_ble.tedee_lib.lock_commands import TedeeLock

from .conftest import DEVICE_ID, SERIAL

COORD = "custom_components.tedee_ble.coordinator"

# 2026-11-19T14:07:59Z, the first fingerprint use in the issue #16 capture.
T0 = 1795097279.0


def activity(seconds: float, event: int, alias: str | None) -> dict:
    stamp = f"2026-11-19T14:0{int(7 + (59 + seconds) // 60)}:{(59 + seconds) % 60:06.3f}"
    return {"event": event, "date": stamp, "pinAlias": alias, "accessLinkName": None}


async def test_notification_carries_pin_and_fingerprint_ids() -> None:
    """Raw notifications from a PRO lock with a Keypad PRO (issue #16)."""
    lock = TedeeLock(None, None)

    pin = await lock.parse_notification(bytes.fromhex("00ba08000a0000000300"))
    finger = await lock.parse_notification(bytes.fromhex("00ba0800170000001c00"))

    assert (pin["trigger"], pin["trigger_name"], pin["access_id"]) == (0x0A, "keypad", 3)
    assert (finger["trigger"], finger["trigger_name"], finger["access_id"]) == (
        0x17, "keypad", 28,
    )


def test_parse_pin_list() -> None:
    result = {"pins": [{"id": 0, "alias": "Anna"}, {"id": 1, "alias": "Ben"}, {"id": 2}]}

    assert parse_pin_list(result) == {METHOD_PIN: {"0": "Anna", "1": "Ben"}}
    assert parse_pin_list([{"id": 4, "alias": "Cleo"}]) == {METHOD_PIN: {"4": "Cleo"}}
    assert parse_pin_list(None) == {}


def test_match_activity_names_a_use_by_timestamp() -> None:
    learned = match_activity(
        [(T0, METHOD_FINGERPRINT, 28), (T0 + 60, METHOD_PIN, 0)],
        [
            activity(1.2, 80, "Anna left thumb"),
            activity(61, 63, "Anna PIN"),
            activity(1.0, 83, None),  # door sensor, not a keypad use
        ],
    )

    assert learned == {
        METHOD_FINGERPRINT: {"28": "Anna left thumb"},
        METHOD_PIN: {"0": "Anna PIN"},
    }


def test_match_activity_ignores_other_method_and_far_entries() -> None:
    learned = match_activity(
        [(T0, METHOD_FINGERPRINT, 28)],
        [activity(0.5, 63, "a PIN"), activity(20, 80, "someone else")],
    )

    assert learned == {}


def test_match_activity_leaves_ambiguous_uses_unnamed() -> None:
    """Two fingers within seconds: either entry could belong to either ID."""
    learned = match_activity(
        [(T0, METHOD_FINGERPRINT, 30), (T0 + 4.6, METHOD_FINGERPRINT, 31)],
        [activity(0.4, 80, "right thumb"), activity(5.0, 80, "right index")],
    )

    assert learned == {}


@pytest.fixture
def coordinator(hass: HomeAssistant) -> TedeeCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(DEVICE_ID),
        data={
            CONF_API_KEY: "key",
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_SERIAL: SERIAL,
            CONF_LOCK_NAME: "Lock",
            CONF_KEYPAD_NAMES: {METHOD_PIN: {"0": "Anna"}, METHOD_FINGERPRINT: {}},
        },
    )
    entry.add_to_hass(hass)
    return TedeeCoordinator(hass, entry)


def fake_cloud(accessory_types, pins, activities):
    class FakeCloudAPI:
        def __init__(self, api_key: str) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_accessory_types(self, device_id):
            return accessory_types

        async def get_pin_list(self, device_id):
            return pins

        async def get_device_activity(self, device_id, limit=200):
            return activities

    return FakeCloudAPI


async def test_fingerprint_needs_a_reader(hass: HomeAssistant, coordinator) -> None:
    """0x17 is only read as a fingerprint when a reader is paired."""
    assert coordinator._keypad_method(0x0A) == METHOD_PIN
    assert coordinator._keypad_method(0x17) is None
    assert coordinator._keypad_method(0x02) is None

    hass.config_entries.async_update_entry(
        coordinator.entry, data={**coordinator.entry.data, CONF_FINGERPRINT_READER: True}
    )

    assert coordinator._keypad_method(0x17) == METHOD_FINGERPRINT


async def test_keypad_user_known_and_unknown(coordinator) -> None:
    with patch.object(coordinator, "_start_keypad_learning") as learn:
        assert coordinator._keypad_user(METHOD_PIN, 0) == "Anna"
        learn.assert_not_called()

        assert coordinator._keypad_user(METHOD_FINGERPRINT, 28) == "Fingerprint 28"
        # The same use reported again (pulling, pulled, unlocked).
        assert coordinator._keypad_user(METHOD_FINGERPRINT, 28) == "Fingerprint 28"
        learn.assert_called_once()

    assert [o[1:] for o in coordinator._keypad_observations] == [
        (METHOD_PIN, 0), (METHOD_FINGERPRINT, 28),
    ]


async def test_refresh_stores_cloud_and_learned_names(coordinator) -> None:
    coordinator._keypad_observations.append((T0, METHOD_FINGERPRINT, 28))
    cloud = fake_cloud(
        [10],
        {"pins": [{"id": 1, "alias": "Ben"}]},
        [activity(0.8, 80, "Anna left thumb")],
    )

    with patch(f"{COORD}.TedeeCloudAPI", cloud):
        await coordinator._refresh_keypad_names()

    data = coordinator.entry.data
    # PIN 0 is gone from the cloud list, so its stored name goes too.
    assert data[CONF_KEYPAD_NAMES] == {
        METHOD_PIN: {"1": "Ben"},
        METHOD_FINGERPRINT: {"28": "Anna left thumb"},
    }
    assert data[CONF_FINGERPRINT_READER] is True
    assert not coordinator._unnamed_keypad_observations()


async def test_refresh_without_keypad_keeps_names(coordinator) -> None:
    with patch(f"{COORD}.TedeeCloudAPI", fake_cloud([8], None, [])):
        await coordinator._refresh_keypad_names()

    data = coordinator.entry.data
    assert data[CONF_KEYPAD_NAMES][METHOD_PIN] == {"0": "Anna"}
    assert data[CONF_FINGERPRINT_READER] is False
