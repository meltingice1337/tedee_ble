"""Logbook descriptions for Tedee BLE lock events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_LOCK_ACTION

ACTION_LABELS = {
    "locked": "Locked",
    "unlocked": "Unlocked",
    "locking": "Locking",
    "unlocking": "Unlocking",
    "pull_spring": "Pull spring",
    "pulling": "Pulling",
    "partially_unlocked": "Partially unlocked",
    "uncalibrated": "Uncalibrated",
    "calibration": "Calibration",
    "unknown": "Unknown",
}


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Describe Tedee BLE logbook events."""

    @callback
    def async_describe_lock_action(event: Event) -> dict[str, Any]:
        """Describe a lock action event."""
        data = event.data
        action = ACTION_LABELS.get(data.get("action", ""), data.get("action", ""))
        trigger = data.get("trigger", "unknown")
        user = data.get("user", "N/A")

        if user and user != "N/A":
            message = f"{action} by {user} ({trigger})"
        else:
            message = f"{action} ({trigger})"

        return {
            LOGBOOK_ENTRY_NAME: data.get("lock_name", "Tedee"),
            LOGBOOK_ENTRY_MESSAGE: message,
            LOGBOOK_ENTRY_ENTITY_ID: data.get("entity_id"),
        }

    async_describe_event(DOMAIN, EVENT_LOCK_ACTION, async_describe_lock_action)
