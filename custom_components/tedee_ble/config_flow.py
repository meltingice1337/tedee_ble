"""Config flow for Tedee BLE integration."""

from __future__ import annotations

import logging
import re

import voluptuous as vol
from bleak import BleakScanner

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_ADDRESS,
    CONF_API_KEY,
    CONF_CERT_EXPIRATION,
    CONF_CERTIFICATE,
    CONF_DEVICE_ID,
    CONF_DEVICE_PUBLIC_KEY,
    CONF_LOCK_NAME,
    CONF_MOBILE_ID,
    CONF_PRIVATE_KEY_PEM,
    CONF_SERIAL,
    CONF_SIGNED_TIME,
    CONF_USER_MAP,
    DOMAIN,
)
from .tedee_lib.ble import serial_to_service_uuid
from .tedee_lib.cloud_api import CloudAPIError, TedeeCloudAPI
from .tedee_lib.crypto import (
    generate_ecdsa_keypair,
    private_key_to_pem,
    public_key_to_base64,
)

logger = logging.getLogger(__name__)

MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

BLE_SCAN_TIMEOUT = 15.0


class TedeeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tedee BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._api_key: str = ""
        self._locks: list[dict] = []
        self._selected_lock: dict = {}
        self._address: str = ""

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Step 1: Enter API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            try:
                async with TedeeCloudAPI(api_key) as api:
                    locks = await api.get_devices()
            except CloudAPIError as err:
                logger.error("Cloud API error: %s", err)
                if err.status_code in (401, 403):
                    errors["base"] = "invalid_api_key"
                else:
                    errors["base"] = "cannot_connect"
            except Exception:
                logger.exception("Unexpected error connecting to Tedee Cloud")
                errors["base"] = "cannot_connect"
            else:
                if not locks:
                    errors["base"] = "no_locks_found"
                else:
                    self._api_key = api_key
                    self._locks = locks
                    return await self.async_step_select_lock()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_lock(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Step 2: Select lock from dropdown."""
        # Filter out already-configured locks
        configured_ids = {
            entry.data.get(CONF_DEVICE_ID)
            for entry in self._async_current_entries()
        }
        available_locks = [
            lock for lock in self._locks if lock["id"] not in configured_ids
        ]

        if not available_locks:
            return self.async_abort(reason="all_locks_configured")

        lock_options = {
            str(lock["id"]): f"{lock.get('name', 'Lock')} ({lock.get('serialNumber', '?')})"
            for lock in available_locks
        }

        if user_input is not None:
            device_id = int(user_input["lock_selection"])
            for lock in available_locks:
                if lock["id"] == device_id:
                    self._selected_lock = lock
                    break
            return await self.async_step_ble_address()

        return self.async_show_form(
            step_id="select_lock",
            data_schema=vol.Schema(
                {
                    vol.Required("lock_selection"): vol.In(lock_options),
                }
            ),
        )

    async def async_step_ble_address(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Step 3: Scan for BLE address, or enter manually."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            if not MAC_REGEX.match(address):
                errors["base"] = "invalid_mac"
            else:
                self._address = address
                return await self.async_step_register()
        else:
            # Auto-scan for the lock by serial number
            serial = self._selected_lock.get("serialNumber", "")
            found_address = await self._scan_for_lock(serial)
            if found_address:
                logger.info("Found lock %s at %s via BLE scan", serial, found_address)
                self._address = found_address
                return await self.async_step_register()
            logger.warning("BLE scan did not find lock %s, requesting manual entry", serial)
            errors["base"] = "scan_failed"

        return self.async_show_form(
            step_id="ble_address",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "serial": self._selected_lock.get("serialNumber", ""),
            },
        )

    async def _scan_for_lock(self, serial: str) -> str | None:
        """Scan BLE for a Tedee lock by serial number. Returns MAC or None."""
        try:
            target_uuid = serial_to_service_uuid(serial).lower()
            logger.info("Scanning BLE for serial %s (UUID: %s)...", serial, target_uuid)
            devices = await BleakScanner.discover(
                timeout=BLE_SCAN_TIMEOUT, return_adv=True
            )
            for device, adv_data in devices.values():
                service_uuids = [str(u).lower() for u in (adv_data.service_uuids or [])]
                if target_uuid in service_uuids:
                    return device.address.upper()
        except Exception:
            logger.exception("BLE scan failed")
        return None

    async def async_step_register(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Step 4: Generate keys, register with cloud, create entry."""
        lock = self._selected_lock
        device_id = lock["id"]
        serial = lock.get("serialNumber", "")
        lock_name = lock.get("name", "Lock")

        try:
            # Generate ECDSA P-256 key pair
            private_key = generate_ecdsa_keypair()
            private_key_pem = private_key_to_pem(private_key).decode()
            public_key_b64 = public_key_to_base64(private_key.public_key())

            async with TedeeCloudAPI(self._api_key) as api:
                mobile_id = await api.register_mobile(public_key_b64)
                cert_data = await api.get_device_certificate(mobile_id, device_id)
                signed_time = await api.get_signed_time()
                user_map = await api.get_user_map(device_id)

        except CloudAPIError as err:
            logger.error("Registration failed: %s", err)
            return self.async_abort(reason="registration_failed")
        except Exception:
            logger.exception("Unexpected error during registration")
            return self.async_abort(reason="registration_failed")

        await self.async_set_unique_id(str(device_id))
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"{lock_name} ({serial})",
            data={
                CONF_API_KEY: self._api_key,
                CONF_DEVICE_ID: device_id,
                CONF_ADDRESS: self._address,
                CONF_SERIAL: serial,
                CONF_LOCK_NAME: lock_name,
                CONF_MOBILE_ID: mobile_id,
                CONF_PRIVATE_KEY_PEM: private_key_pem,
                CONF_CERTIFICATE: cert_data["certificate"],
                CONF_CERT_EXPIRATION: cert_data["expirationDate"],
                CONF_DEVICE_PUBLIC_KEY: cert_data["devicePublicKey"],
                CONF_SIGNED_TIME: signed_time,
                CONF_USER_MAP: {str(k): v for k, v in user_map.items()},
            },
        )
