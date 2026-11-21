"""Diagnostics download for the Tedee BLE integration.

Settings → Devices & services → Tedee BLE → ⋮ → Download diagnostics.

Besides the usual state dump it records what the lock and the Tedee cloud
say about paired accessories (keypad, Keypad PRO, door sensor), so a user who
owns hardware the maintainer does not can report how it behaves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
import hashlib
import hmac
import re
import time
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import (
    CONF_ADDRESS,
    CONF_API_KEY,
    CONF_CERT_EXPIRATION,
    CONF_DEVICE_ID,
    CONF_FINGERPRINT_READER,
    CONF_FIRMWARE_VERSION,
    CONF_HAS_DOOR_SENSOR,
    CONF_KEYPAD_NAMES,
    CONF_LOCK_MODEL,
    CONF_MOBILE_ID,
    CONF_RECHARGEABLE,
    CONF_SERIAL,
    CONF_UPDATE_AVAILABLE,
    DOMAIN,
)
from .keypad_names import FALLBACK_LABELS
from .tedee_lib.ble import serial_to_service_uuid
from .tedee_lib.cloud_api import TedeeCloudAPI

REDACTED = "**REDACTED**"

# Cloud responses are exported whole, because the fields of accessories we
# have never seen are exactly what is wanted. Values are therefore redacted
# by what their key looks like, not from a list of known keys.
_REDACT_KEY_PARTS = (
    "name", "mail", "serial", "mac", "key", "certificate", "token", "signature",
    "password", "ssid", "alias", "identity", "address", "latitude", "longitude",
    "location", "pin", "code", "phone", "wifi",
)
# Names of people (app users, PIN and fingerprint aliases) are replaced by a
# placeholder instead: the same name gives the same placeholder everywhere in
# the file and in later files from the same installation, so which keypad ID
# belongs to which name can be followed without the name being disclosed.
_PERSON_NAME_KEYS = ("alias", "username", "accesslinkname")
_NOT_A_PERSON = re.compile(
    rf"^(?:N/A|\d+|(?:{'|'.join(FALLBACK_LABELS.values())}) \d+)$"
)
# Per-device advertising UUID: the 14 serial digits padded with zeros.
_SERIAL_UUID_RE = re.compile(r"^\d{4}0000-(\d{2})\d{2}-\d{4}-\d{2}00-0{12}$")
_MAC_RE = re.compile(r"\b((?:[0-9A-Fa-f]{2}[:-]){3})(?:[0-9A-Fa-f]{2}[:-]){2}[0-9A-Fa-f]{2}\b")

# Tedee device types that pair to a lock rather than being one.
ACCESSORY_TYPES = {3: "Keypad", 8: "Door Sensor", 9: "Fingerprint module", 10: "Keypad PRO"}

CLOUD_TIMEOUT = 20
ACTIVITY_ENTRIES = 60


def _mask_macs(text: str) -> str:
    """Keep the vendor half of a MAC address, hide the device half."""
    return _MAC_RE.sub(lambda m: f"{m.group(1)}XX:XX:XX", text)


def mask(value: Any) -> Any:
    """Mask MAC addresses anywhere in a JSON-like structure, keys included."""
    if isinstance(value, dict):
        return {_mask_macs(k) if isinstance(k, str) else k: mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    if isinstance(value, str):
        return _mask_macs(value)
    return value


def name_placeholders(secret: str) -> Callable[[str], str]:
    """Return a function replacing a person's name by a stable placeholder.

    Keyed with a secret of this installation, so a placeholder cannot be
    turned back into a name by trying likely names.
    """
    def placeholder(name: str) -> str:
        digest = hmac.new(secret.encode(), name.strip().encode(), hashlib.sha256)
        return f"name-{digest.hexdigest()[:6]}"
    return placeholder


def redact(value: Any, key: str = "", person: Callable[[str], str] | None = None) -> Any:
    """Redact personal data and secrets from a cloud response.

    With `person`, the names of people become placeholders rather than
    being removed (see name_placeholders).
    """
    if isinstance(value, dict):
        return {k: redact(v, str(k), person) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key, person) for v in value]
    if value is None or isinstance(value, bool):
        return value
    if (
        person
        and isinstance(value, str)
        and value.strip()
        and any(key.lower().endswith(part) for part in _PERSON_NAME_KEYS)
    ):
        return person(value)
    if any(part in key.lower() for part in _REDACT_KEY_PARTS):
        return REDACTED
    return value


def find_devices(node: Any, found: dict[int, dict] | None = None) -> dict[int, dict]:
    """Collect every {id, type, serialNumber} object in a cloud response."""
    if found is None:
        found = {}
    if isinstance(node, dict):
        dev_id, dev_type = node.get("id"), node.get("type")
        if (
            isinstance(dev_id, int)
            and isinstance(dev_type, int)
            and not isinstance(dev_type, bool)
            and isinstance(node.get("serialNumber"), str)
        ):
            found.setdefault(dev_id, node)
        for child in node.values():
            find_devices(child, found)
    elif isinstance(node, list):
        for child in node:
            find_devices(child, found)
    return found


async def _probe(api: TedeeCloudAPI, path: str, **params) -> dict:
    """One cloud GET, reported as data or as the error it produced."""
    try:
        async with asyncio.timeout(CLOUD_TIMEOUT):
            body = await api.get_raw(path, **params)
    except Exception as err:  # noqa: BLE001 - the error is the finding
        return {"ok": False, "error": _mask_macs(f"{type(err).__name__}: {err}")[:300]}
    return {"ok": True, "result": body.get("result") if isinstance(body, dict) else body}


async def cloud_snapshot(api_key: str, mobile_id: str, device_id: int) -> tuple[dict, dict[int, dict]]:
    """Ask the Tedee cloud about the account's devices and the lock's accessories.

    Read-only. Returns the redacted report and the devices found in it.
    """
    report: dict[str, Any] = {}
    async with TedeeCloudAPI(api_key) as api:
        details = await _probe(
            api, "/my/device/details",
            includeUserSettings="true", includeLockAccessories="true",
        )
        report["device_details"] = details
        accessories = await _probe(api, f"/my/lock/{device_id}/accessories")
        report["lock_accessories"] = accessories
        activity = await _probe(
            api, "/my/deviceactivity", DeviceId=device_id, Elements=ACTIVITY_ENTRIES
        )
        report["lock_activity"] = activity
        # PIN list: IDs to compare with what the lock reports.
        report["lock_pins"] = await _probe(api, f"/my/Lock/{device_id}/pin")

        devices = find_devices([details.get("result"), accessories.get("result")])
        # A bridge holds a connection to the lock of its own.
        lock = devices.get(device_id, {})
        report["lock_bridge"] = {
            "paired_bridge_id": lock.get("connectedToId"),
            "lock_connected_to_cloud": lock.get("isConnected"),
        }
        report["accessory_probes"] = probes = {}
        for dev_id, device in devices.items():
            if device["type"] not in ACCESSORY_TYPES or dev_id == device_id:
                continue
            probe: dict[str, Any] = {"type": device["type"]}
            if device["type"] in (3, 10):
                probe["keypad"] = await _probe(api, f"/my/keypad/{dev_id}", includeUserSettings="true")
            elif device["type"] == 9:
                probe["fingerprintmodule"] = await _probe(
                    api, f"/my/fingerprintmodule/{dev_id}", includeUserSettings="true"
                )
            # Would the cloud let this Home Assistant open a secure session to
            # the accessory itself? Only the outcome is kept, not the certificate.
            cert = await _probe(
                api, "/my/devicecertificate/getformobile",
                mobileId=mobile_id, deviceId=dev_id,
            )
            if cert["ok"] and isinstance(cert["result"], dict):
                cert = {
                    "ok": True,
                    "fields": sorted(cert["result"]),
                    "expirationDate": cert["result"].get("expirationDate"),
                }
            probe["mobile_certificate"] = cert
            probes[str(dev_id)] = probe
    return redact(report, person=name_placeholders(api_key)), devices


def scanner_snapshot(hass: HomeAssistant) -> list[dict]:
    """Every Bluetooth adapter and proxy, with its connection slots if it has any.

    A proxy with no free slot hears the lock but cannot connect to it.
    """
    # Not in older Home Assistant releases.
    current_scanners = getattr(bluetooth, "async_current_scanners", None)
    if current_scanners is None:
        return []
    report = []
    for scanner in current_scanners(hass):
        item: dict[str, Any] = {
            "scanner": scanner.name,
            "connectable": scanner.connectable,
        }
        try:
            slots = scanner.get_allocations()
        except Exception:  # noqa: BLE001 - optional detail
            slots = None
        if slots is not None:
            item["connection_slots"] = slots.slots
            item["free_slots"] = slots.free
        report.append(item)
    return sorted(report, key=lambda item: item["scanner"])


def bluetooth_snapshot(hass: HomeAssistant, address: str, devices: dict[int, dict]) -> dict:
    """What Home Assistant's scanners currently hear from the account's devices."""
    uuid_owner: dict[str, str] = {}
    for dev_id, device in devices.items():
        try:
            uuid = serial_to_service_uuid(device["serialNumber"]).lower()
        except ValueError:
            continue
        label = ACCESSORY_TYPES.get(device["type"], "lock")
        uuid_owner[uuid] = f"{label} id {dev_id} (type {device['type']})"

    now = time.monotonic()
    heard = []
    addresses = {address.upper()} if address else set()
    for info in bluetooth.async_discovered_service_info(hass, connectable=False):
        uuids = [u.lower() for u in info.service_uuids]
        owners = [uuid_owner[u] for u in uuids if u in uuid_owner]
        tedee = [u for u in uuids if u.endswith("-4899-489f-a301-fbee544b1db0")]
        # A serial UUID is not exported (it is the serial); the device type
        # encoded in it is, so an accessory the cloud did not list still shows.
        types = [int(m.group(1)) for u in uuids if (m := _SERIAL_UUID_RE.match(u))]
        if not tedee and not types and info.address.upper() not in addresses:
            continue
        addresses.add(info.address.upper())
        heard.append({
            "device": owners or ["not in the cloud device list"],
            "device_type_from_serial_uuid": types,
            "address": info.address,
            "local_name": info.name,
            "rssi": info.rssi,
            "connectable": info.connectable,
            "source": info.source,
            "age_seconds": round(now - info.time, 1),
            "tedee_service_uuids": tedee,
            "manufacturer_data": {str(k): v.hex() for k, v in info.manufacturer_data.items()},
            "service_data": {k: v.hex() for k, v in info.service_data.items()},
        })

    scanners = {}
    for addr in sorted(addresses):
        scanners[addr] = [
            {
                "scanner": item.scanner.name,
                "connectable": item.scanner.connectable,
                "rssi": item.advertisement.rssi,
            }
            for item in bluetooth.async_scanner_devices_by_address(hass, addr, connectable=False)
        ]

    return {
        "scanners": scanner_snapshot(hass),
        "scanner_count": bluetooth.async_scanner_count(hass, connectable=False),
        "connectable_scanner_count": bluetooth.async_scanner_count(hass, connectable=True),
        "advertisements": heard,
        "heard_by": scanners,
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.data
    integration = await async_get_integration(hass, DOMAIN)
    person = name_placeholders(data.get(CONF_API_KEY) or entry.entry_id)
    result: dict[str, Any] = {
        "integration_version": str(integration.version),
        "entry": {
            "state": entry.state.value,
            "device_id": data.get(CONF_DEVICE_ID),
            "model": data.get(CONF_LOCK_MODEL),
            "firmware_version": data.get(CONF_FIRMWARE_VERSION),
            "update_available": data.get(CONF_UPDATE_AVAILABLE),
            "has_door_sensor": data.get(CONF_HAS_DOOR_SENSOR),
            "rechargeable": data.get(CONF_RECHARGEABLE),
            "certificate_expiration": data.get(CONF_CERT_EXPIRATION),
            "ble_address": data.get(CONF_ADDRESS),
            "fingerprint_reader": data.get(CONF_FINGERPRINT_READER),
            # Which ID has which name, as placeholders (see name_placeholders).
            "keypad_names": {
                method: {i: person(names[i]) for i in sorted(names, key=int)}
                for method, names in data.get(CONF_KEYPAD_NAMES, {}).items()
            },
            "options": dict(entry.options),
        },
    }

    # Absent if setup did not finish; the cloud and Bluetooth sections below
    # do not need it.
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        state = asdict(coordinator.state)
        if not _NOT_A_PERSON.match(state["last_user"]):
            state["last_user"] = person(state["last_user"])
        result["coordinator"] = {
            "connected": coordinator.is_connected,
            # Why the lock is not reachable: counters, the retry the
            # integration is on, and the last failed attempts.
            "connection": {
                **coordinator.connect_stats,
                "retry_number": coordinator._reconnect_attempt,
                "failures": list(coordinator.connect_failures),
            },
            "state": state,
            # Decrypted lock notifications: first byte is the notification id.
            "notifications": [
                {"time": round(ts, 1), "data": payload}
                for ts, payload in coordinator.notification_log
            ],
            "keypad_uses": [
                {"time": round(ts, 1), "method": method, "id": access_id}
                for ts, method, access_id in coordinator._keypad_observations
            ],
        }

    devices: dict[int, dict] = {}
    if data.get(CONF_API_KEY) and data.get(CONF_DEVICE_ID) is not None:
        cloud, devices = await cloud_snapshot(
            data[CONF_API_KEY], data.get(CONF_MOBILE_ID, ""), data[CONF_DEVICE_ID]
        )
        result["cloud"] = cloud
    if not devices and data.get(CONF_SERIAL):
        devices = {data.get(CONF_DEVICE_ID, 0): {"serialNumber": data[CONF_SERIAL], "type": -1}}

    try:
        result["bluetooth"] = bluetooth_snapshot(hass, data.get(CONF_ADDRESS, ""), devices)
    except Exception as err:  # noqa: BLE001 - never lose the rest of the file
        result["bluetooth"] = {"error": f"{type(err).__name__}: {err}"}

    return mask(result)
