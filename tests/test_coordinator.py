"""Tests for how the coordinator handles a lock Home Assistant cannot see."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from custom_components.tedee_ble.const import (
    CONF_ADDRESS,
    CONF_DEVICE_ID,
    CONF_FIRMWARE_VERSION,
    CONF_LOCK_MODEL,
    CONF_LOCK_NAME,
    CONF_SERIAL,
    DOMAIN,
    PROXY_EXHAUSTED_DELAY_INDEX,
)
from custom_components.tedee_ble.coordinator import LockNotVisibleError, TedeeCoordinator

from .conftest import ADDRESS, DEVICE_ID, SERIAL, SERVICE_UUID, service_info

COORD = "custom_components.tedee_ble.coordinator"
NEW_ADDRESS = "F8:F6:15:D2:AA:AA"
NO_SLOT = (
    "GO-C1A3 - F8:F6:15:D2:AA:9D: Failed to connect after 9 attempt(s): No backend "
    "with an available connection slot that can reach address F8:F6:15:D2:AA:9D was found"
)


@pytest.fixture
def coordinator(hass: HomeAssistant) -> TedeeCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(DEVICE_ID),
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_SERIAL: SERIAL,
            CONF_LOCK_NAME: "Lock",
            CONF_LOCK_MODEL: "Tedee GO",
            CONF_FIRMWARE_VERSION: "2.1.5795",
        },
    )
    entry.add_to_hass(hass)
    return TedeeCoordinator(hass, entry)


@pytest.fixture
def unseen():
    """Home Assistant has not heard the lock and no advertisement arrives."""
    with (
        patch(f"{COORD}.async_ble_device_from_address", return_value=None),
        patch(f"{COORD}.async_discovered_service_info", return_value=[]),
        patch(
            f"{COORD}.async_process_advertisements",
            AsyncMock(side_effect=asyncio.TimeoutError),
        ) as process,
    ):
        yield process


async def test_unseen_lock_raises_after_waiting(coordinator, unseen) -> None:
    """No bare-address dial: the coordinator waits, then says why it gave up."""
    with pytest.raises(LockNotVisibleError, match="cannot see Lock"):
        await coordinator._resolve_or_wait_for_lock(ADDRESS)

    unseen.assert_awaited_once()
    matcher = unseen.call_args.args[2]
    assert matcher["service_uuid"] == SERVICE_UUID
    assert matcher["connectable"] is True


async def test_advertisement_during_wait_is_used(coordinator, unseen) -> None:
    """A lock heard during the wait is used, and a new MAC is stored."""
    unseen.side_effect = None
    unseen.return_value = service_info(NEW_ADDRESS.lower())

    device = await coordinator._resolve_or_wait_for_lock(ADDRESS)

    assert device.address == NEW_ADDRESS.lower()
    assert coordinator.entry.data[CONF_ADDRESS] == NEW_ADDRESS


async def test_wait_ignores_replayed_advertisements(coordinator, unseen) -> None:
    """HA replays old advertisements to new callbacks; only fresh ones count."""
    with pytest.raises(LockNotVisibleError):
        await coordinator._resolve_or_wait_for_lock(ADDRESS)

    is_fresh = unseen.call_args.args[1]
    assert not is_fresh(service_info(time=0.0))
    assert is_fresh(service_info(time=10**9))


async def test_failed_setup_shuts_down(hass: HomeAssistant, coordinator) -> None:
    """A failed first connect leaves no poll timer or reconnect behind."""
    with (
        patch.object(coordinator, "_refresh_firmware_info_quietly", AsyncMock()),
        patch.object(
            coordinator, "_connect", AsyncMock(side_effect=LockNotVisibleError("gone"))
        ),
        patch.object(coordinator, "_schedule_reconnect") as schedule,
    ):
        with pytest.raises(ConfigEntryNotReady, match="gone"):
            await coordinator.async_setup()
        await hass.async_block_till_done()

    assert coordinator._unsub_firmware_poll is None
    assert coordinator._shutting_down
    schedule.assert_not_called()


@pytest.mark.parametrize(
    ("lock_heard", "expected_attempt"),
    [(True, PROXY_EXHAUSTED_DELAY_INDEX), (False, 0)],
)
async def test_no_slot_backoff_only_while_lock_is_heard(
    coordinator, lock_heard, expected_attempt
) -> None:
    """habluetooth's 'no connection slot' also means 'nobody hears the lock'.

    Only the first is proxy exhaustion and deserves the long back-off; for the
    second the normal ladder keeps retrying so a returning proxy is picked up.
    """
    with (
        patch.object(coordinator, "_connect", AsyncMock(side_effect=HomeAssistantError(NO_SLOT))),
        patch(f"{COORD}.async_address_present", return_value=lock_heard),
        patch.object(coordinator, "_schedule_reconnect") as schedule,
    ):
        await coordinator._reconnect(0)

    assert coordinator._reconnect_attempt == expected_attempt
    schedule.assert_called_once()
