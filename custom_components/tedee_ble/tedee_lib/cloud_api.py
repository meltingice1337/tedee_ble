"""Tedee Cloud API client for BLE integration.

Handles device registration, certificate management, and signed time retrieval.
Uses Personal Access Key (PAK) authentication.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from . import crypto

logger = logging.getLogger(__name__)

API_BASE = "https://api.tedee.com/api/v37"


class CloudAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


def _create_httpx_client(api_key: str) -> httpx.AsyncClient:
    """Create httpx client (blocking SSL init — must run in executor)."""
    return httpx.AsyncClient(
        base_url=API_BASE,
        headers={"Authorization": f"PersonalKey {api_key}"},
        timeout=30.0,
    )


class TedeeCloudAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create httpx client in executor (SSL init blocks)."""
        if self._client is None:
            loop = asyncio.get_running_loop()
            self._client = await loop.run_in_executor(
                None, _create_httpx_client, self.api_key
            )
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        client = await self._get_client()
        resp = await client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("errorMessages", [resp.text])
            except Exception:
                msg = resp.text
            raise CloudAPIError(resp.status_code, str(msg))
        return resp.json()

    async def get_devices(self) -> list[dict]:
        """Get all devices with details. Returns the locks list."""
        data = await self._request("GET", "/my/device/details")
        return data.get("result", {}).get("locks", [])

    async def find_device_id(self, serial: str) -> int | None:
        """Find device ID by serial number."""
        locks = await self.get_devices()
        for lock in locks:
            if lock.get("serialNumber") == serial:
                return lock["id"]
        return None

    async def register_mobile(
        self, public_key_b64: str, name: str = "tedee-ble-ha"
    ) -> str:
        """Register a mobile device with our public key."""
        data = await self._request(
            "POST",
            "/my/mobile",
            json={
                "name": name,
                "operatingSystem": 3,  # Other/Linux
                "publicKey": public_key_b64,
            },
        )
        mobile_id = data["result"]["id"]
        logger.info("Registered mobile device: %s", mobile_id)
        return mobile_id

    async def get_device_certificate(self, mobile_id: str, device_id: int) -> dict:
        """Get access certificate for BLE communication."""
        data = await self._request(
            "GET",
            "/my/devicecertificate/getformobile",
            params={"mobileId": mobile_id, "deviceId": device_id},
        )
        result = data["result"]
        logger.info("Got certificate expiring %s", result.get("expirationDate"))
        return result

    async def get_signed_time(self) -> dict:
        """Get signed time for lock synchronization."""
        data = await self._request("GET", "/datetime/getsignedtime")
        return data["result"]

    async def delete_mobile(self, mobile_id: str) -> None:
        """Delete a registered mobile device."""
        await self._request("DELETE", f"/my/mobile/{mobile_id}")
        logger.info("Deleted mobile device: %s", mobile_id)

    async def get_device_activity(self, device_id: int, limit: int = 200) -> list[dict]:
        """Get recent activity logs. Each entry has userId + username."""
        data = await self._request(
            "GET", "/my/deviceactivity",
            params={"DeviceId": device_id, "Elements": limit},
        )
        return data.get("result", [])

    async def get_user_map(self, device_id: int) -> dict[int, str]:
        """Build userId → username lookup from activity logs."""
        activities = await self.get_device_activity(device_id, limit=200)
        user_map: dict[int, str] = {}
        for a in activities:
            uid = a.get("userId")
            name = a.get("username")
            if uid and name and uid not in user_map:
                user_map[uid] = name
        return user_map


def certificate_needs_refresh(expiration_date: str) -> bool:
    """Check if certificate needs refresh (< 5 days remaining)."""
    if not expiration_date:
        return True
    try:
        exp = datetime.fromisoformat(expiration_date.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        remaining = (exp - now).total_seconds()
        five_days = 5 * 24 * 3600
        return remaining < five_days
    except Exception:
        return True
