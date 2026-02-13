"""High-level Tedee lock command interface.

Provides operations like unlock, lock, get state, get battery, and set signed time.
All commands are sent encrypted via the PTLS session over BLE.
"""

import base64
import logging
import struct

from .ble import TedeeBLETransport
from .ptls import PTLSSession

logger = logging.getLogger(__name__)

# Command opcodes
CMD_GET_BATTERY = 0x0C
CMD_LOCK = 0x50
CMD_UNLOCK = 0x51
CMD_PULL_SPRING = 0x52
CMD_GET_STATE = 0x5A
CMD_SET_SIGNED_DATETIME = 0x71

# Unlock parameters
UNLOCK_NONE = 0x00
UNLOCK_AUTO = 0x01
UNLOCK_FORCE = 0x02

# Lock parameters
LOCK_NONE = 0x00
LOCK_FORCE = 0x02

# Result codes
RESULT_SUCCESS = 0x00
RESULT_INVALID_PARAM = 0x01
RESULT_ERROR = 0x02
RESULT_BUSY = 0x03
RESULT_NOT_CALIBRATED = 0x05

RESULT_NAMES = {
    0x00: "SUCCESS",
    0x01: "INVALID_PARAM",
    0x02: "ERROR",
    0x03: "BUSY",
    0x05: "NOT_CALIBRATED",
    0x06: "ALREADY_CALLED_BY_AUTOUNLOCK",
    0x08: "NOT_CONFIGURED",
    0x09: "DISMOUNTED",
    0x0A: "ALREADY_CALLED_BY_OTHER_OPERATION",
}

# Lock state values
LOCK_STATE_UNCALIBRATED = 0x00
LOCK_STATE_CALIBRATION = 0x01
LOCK_STATE_UNLOCKED = 0x02
LOCK_STATE_PARTIALLY_UNLOCKED = 0x03
LOCK_STATE_UNLOCKING = 0x04
LOCK_STATE_LOCKING = 0x05
LOCK_STATE_LOCKED = 0x06
LOCK_STATE_PULL_SPRING = 0x07
LOCK_STATE_PULLING = 0x08
LOCK_STATE_UNKNOWN = 0x09

LOCK_STATE_NAMES = {
    0x00: "UNCALIBRATED",
    0x01: "CALIBRATION",
    0x02: "UNLOCKED",
    0x03: "PARTIALLY_UNLOCKED",
    0x04: "UNLOCKING",
    0x05: "LOCKING",
    0x06: "LOCKED",
    0x07: "PULL_SPRING",
    0x08: "PULLING",
    0x09: "UNKNOWN",
}

# State change status
STATUS_OK = 0x00
STATUS_JAMMED = 0x01

# Door sensor state
DOOR_STATE_UNKNOWN = 0x00
DOOR_STATE_OPEN = 0x02
DOOR_STATE_CLOSED = 0x03

DOOR_STATE_NAMES = {
    0x00: "UNKNOWN",
    0x02: "OPEN",
    0x03: "CLOSED",
}

# Lock trigger/source (byte 3 of LOCK_STATUS_CHANGE notification)
# Bytes 4-7 = access ID (big-endian uint32, non-zero for user-initiated actions)
TRIGGER_BUTTON = 0x01       # Button press on lock
TRIGGER_REMOTE = 0x02       # Remote (app/BLE command)
TRIGGER_AUTO_LOCK = 0x04    # Auto-lock (after door closed)
TRIGGER_DOOR_SENSOR = 0x10  # Door sensor state change

TRIGGER_NAMES = {
    0x01: "button",
    0x02: "remote",
    0x04: "auto_lock",
    0x10: "door_sensor",
}

# Notification IDs
NOTIFY_LOCK_STATUS_CHANGE = 0xBA
NOTIFY_SIGNED_DATETIME = 0x7B
NOTIFY_NEED_DATE_TIME = 0xA4
NOTIFY_DEVICE_STATS = 0xE2


class CommandError(Exception):
    def __init__(self, result_code: int):
        self.result_code = result_code
        name = RESULT_NAMES.get(result_code, f"0x{result_code:02x}")
        super().__init__(f"Command failed: {name}")


class TedeeLock:
    """High-level interface for controlling a Tedee lock."""

    def __init__(
        self,
        transport: TedeeBLETransport,
        session: PTLSSession,
        initial_door_state: int = DOOR_STATE_UNKNOWN,
    ):
        self.transport = transport
        self.session = session
        self.door_state: int = initial_door_state

    async def _send_command(self, command: bytes, timeout: float = 10.0) -> bytes:
        """Send an encrypted command and receive the response."""
        encrypted = self.session.encrypt(command)
        await self.transport.write_api_command(encrypted)

        response = await self.transport.read_api_command(timeout=timeout)
        decrypted = self.session.decrypt(response)
        logger.debug("Command response raw: %s", decrypted.hex())

        # Response format: [opcode] [result_code] [data...]
        return decrypted[1:]

    async def set_signed_time(self, signed_time: dict) -> None:
        """Set signed datetime on the lock. Must be called first after session establishment."""
        dt_bytes = base64.b64decode(signed_time["datetime"])
        sig_bytes = base64.b64decode(signed_time["signature"])

        payload = bytes([CMD_SET_SIGNED_DATETIME]) + dt_bytes + sig_bytes

        logger.info("Setting signed datetime...")
        response = await self._send_command(payload)

        result = response[0]
        if result != RESULT_SUCCESS:
            raise CommandError(result)
        logger.info("Signed datetime set successfully")

    async def unlock(self, mode: int = UNLOCK_NONE) -> int:
        """Unlock the door."""
        command = bytes([CMD_UNLOCK, mode])
        logger.info("Sending UNLOCK command (mode=0x%02x)...", mode)
        response = await self._send_command(command)

        result = response[0]
        if result != RESULT_SUCCESS:
            raise CommandError(result)
        logger.info("Unlock command accepted")
        return result

    async def lock(self, mode: int = LOCK_NONE) -> int:
        """Lock the door."""
        command = bytes([CMD_LOCK, mode])
        logger.info("Sending LOCK command (mode=0x%02x)...", mode)
        response = await self._send_command(command)

        result = response[0]
        if result != RESULT_SUCCESS:
            raise CommandError(result)
        logger.info("Lock command accepted")
        return result

    async def pull_spring(self) -> int:
        """Activate pull spring mechanism."""
        command = bytes([CMD_PULL_SPRING])
        logger.info("Sending PULL_SPRING command...")
        response = await self._send_command(command)

        result = response[0]
        if result != RESULT_SUCCESS:
            raise CommandError(result)
        logger.info("Pull spring command accepted")
        return result

    async def get_state(self) -> tuple[int, int, int]:
        """Get current lock state.

        Returns:
            (lock_state, status, door_state) tuple
        """
        command = bytes([CMD_GET_STATE])
        logger.info("Getting lock state...")
        response = await self._send_command(command)

        result = response[0]
        if result != RESULT_SUCCESS:
            raise CommandError(result)

        lock_state = response[1]
        status = response[2] if len(response) > 2 else STATUS_OK

        state_name = LOCK_STATE_NAMES.get(lock_state, f"0x{lock_state:02x}")
        door_name = DOOR_STATE_NAMES.get(self.door_state, f"0x{self.door_state:02x}")
        logger.info("Lock state: %s, Status: %s, Door: %s",
                     state_name, "OK" if status == 0 else "JAMMED", door_name)
        return lock_state, status, self.door_state

    async def drain_pending_notifications(self) -> None:
        """Drain any pending notifications after connect."""
        import asyncio
        await asyncio.sleep(0.3)
        while True:
            try:
                data = await self.transport.read_notification(timeout=0.3)
                self.parse_notification(data)
            except asyncio.TimeoutError:
                break

    async def get_battery(self) -> tuple[int, bool]:
        """Get battery level and charging status."""
        command = bytes([CMD_GET_BATTERY])
        logger.info("Getting battery info...")
        response = await self._send_command(command)

        result = response[0]
        if result != RESULT_SUCCESS:
            raise CommandError(result)

        level = response[1]
        is_charging = response[2] == 1 if len(response) > 2 else False

        logger.info("Battery: %d%%, Charging: %s", level, is_charging)
        return level, is_charging

    def parse_notification(self, data: bytes) -> dict | None:
        """Parse a notification from the lock.

        Returns:
            Parsed notification dict, or None if unknown type
        """
        header = data[0] & 0x0F
        if header == 0x01:  # DATA_ENCRYPTED
            try:
                data = self.session.decrypt(data)
            except Exception as e:
                logger.warning("Failed to decrypt notification: %s", e)
                return None
        elif header == 0x00:  # DATA_NOT_ENCRYPTED
            data = data[1:]

        if not data:
            return None

        notify_id = data[0]

        if notify_id == NOTIFY_LOCK_STATUS_CHANGE:
            lock_state = data[1] if len(data) > 1 else 0xFF
            status = data[2] if len(data) > 2 else 0x00
            trigger = data[3] if len(data) > 3 else 0xFF
            state_name = LOCK_STATE_NAMES.get(lock_state, f"0x{lock_state:02x}")
            trigger_name = TRIGGER_NAMES.get(trigger, f"unknown_0x{trigger:02x}")
            door_state = data[8] if len(data) > 8 else DOOR_STATE_UNKNOWN
            if door_state != DOOR_STATE_UNKNOWN:
                self.door_state = door_state
            door_name = DOOR_STATE_NAMES.get(door_state, f"0x{door_state:02x}")
            # Bytes 4-7: access ID (big-endian uint32, identifies who triggered it)
            access_id = int.from_bytes(data[4:8], "big") if len(data) > 7 else 0
            logger.info(
                "Lock status change: state=%s status=%s trigger=%s access_id=%d door=%s raw=%s",
                state_name, "OK" if status == 0 else "JAMMED",
                trigger_name, access_id, door_name, data.hex(),
            )
            return {
                "type": "lock_state",
                "state": lock_state,
                "state_name": state_name,
                "status": status,
                "jammed": status == STATUS_JAMMED,
                "trigger": trigger,
                "trigger_name": trigger_name,
                "access_id": access_id,
                "door_state": door_state,
                "door_name": door_name,
            }

        if notify_id == NOTIFY_NEED_DATE_TIME:
            return {"type": "need_datetime"}

        if notify_id == NOTIFY_SIGNED_DATETIME:
            result = data[1] if len(data) > 1 else 0xFF
            return {"type": "signed_datetime_ack", "result": result}

        if notify_id == NOTIFY_DEVICE_STATS:
            return {"type": "device_stats", "data": data[1:].hex()}

        logger.debug("Unknown notification: 0x%02x data=%s", notify_id, data.hex())
        return {"type": "unknown", "id": notify_id, "data": data.hex()}
