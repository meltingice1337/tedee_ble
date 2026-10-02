"""Constants for the Tedee BLE integration."""

from datetime import timedelta

DOMAIN = "tedee_ble"

# Config entry data keys
CONF_API_KEY = "api_key"
CONF_DEVICE_ID = "device_id"
CONF_ADDRESS = "address"
CONF_SERIAL = "serial"
CONF_LOCK_NAME = "lock_name"
CONF_MOBILE_ID = "mobile_id"
CONF_PRIVATE_KEY_PEM = "private_key_pem"
CONF_CERTIFICATE = "certificate"
CONF_CERT_EXPIRATION = "cert_expiration"
CONF_DEVICE_PUBLIC_KEY = "device_public_key"
CONF_SIGNED_TIME = "signed_time"
CONF_LOCK_MODEL = "lock_model"
CONF_USER_MAP = "user_map"  # {userId: username} from activity logs
CONF_AUTO_PULL = "auto_pull"  # Unlock also pulls spring
CONF_FIRMWARE_VERSION = "firmware_version"
CONF_UPDATE_AVAILABLE = "update_available"
CONF_HAS_DOOR_SENSOR = "has_door_sensor"  # a door sensor accessory is paired
CONF_RECHARGEABLE = "rechargeable"  # lock has a built-in rechargeable battery

# Tedee API device type → model name
DEVICE_TYPE_MODELS = {
    0: "PRO",
    2: "PRO",
    4: "GO",
    12: "Z-Wave Lock",
    13: "PRO 2",
    # 1=Bridge, 3=Keypad, 5=Gate, 6=DryContact, 8=Door Sensor,
    # 9=Fingerprint, 10=Keypad PRO
}

# Device types that run on disposable cells (GO / GO 2: 3x CR123A) and so can
# never report charging. The BLE battery response and the cloud lock
# properties carry a charging flag for every model regardless -- the official
# app also keys its battery UI purely on the device type -- so the model is
# the only signal there is. Unknown types default to rechargeable: a useless
# charging entity is a smaller mistake than hiding a real one (issue #13).
NON_RECHARGEABLE_DEVICE_TYPES = {4}


def _device_type_from_serial(serial: str) -> int | None:
    """Return the device type encoded in characters 4-5 of the serial."""
    digits = serial.replace("-", "")
    if len(digits) >= 6 and digits[4:6].isdigit():
        return int(digits[4:6])
    return None


def resolve_rechargeable(device_type: int | None, serial: str) -> bool:
    """Whether a lock has a rechargeable battery (see NON_RECHARGEABLE_DEVICE_TYPES).

    Falls back to the serial when the cloud type is unknown, so it can also
    be derived for config entries created before this flag was stored.
    """
    if device_type is None:
        device_type = _device_type_from_serial(serial)
    return device_type not in NON_RECHARGEABLE_DEVICE_TYPES


def resolve_lock_model(device_type: int | None, serial: str) -> str:
    """Resolve the display model name for a lock.

    GO and GO 2 both report device type 4, so the cloud `type` field alone
    cannot tell them apart. The distinction is encoded in the serial number:
    characters 6-7 are >= 20 on a GO 2.

    When device_type is None the family is derived from the serial as well,
    from characters 4-5.
    """
    digits = serial.replace("-", "")
    if device_type is None:
        device_type = _device_type_from_serial(serial)
    model = DEVICE_TYPE_MODELS.get(device_type, "Lock")
    if (
        model == "GO"
        and len(digits) >= 8
        and digits[6:8].isdigit()
        and int(digits[6:8]) >= 20
    ):
        return "GO 2"
    return model

# Coordinator
RECONNECT_DELAYS = [2, 5, 10, 30, 60, 120, 300, 600]
# When the proxy reports it's out of connection slots, jump to this index in
# RECONNECT_DELAYS so we back off hard instead of hammering every 60s.
PROXY_EXHAUSTED_DELAY_INDEX = 6  # → 300s (5 min)
POLL_INTERVAL_SECONDS = 600  # 10 minutes
KEEPALIVE_INTERVAL_SECONDS = 45  # BLE keep-alive (lock disconnects after ~25-45s idle on GO)
UNAVAILABLE_GRACE_SECONDS = 15  # Don't mark unavailable until reconnect fails this long
CERT_CHECK_INTERVAL_SECONDS = 6 * 3600  # 6 hours
# After observing UPDATING, the lock reboots (and changes BLE MAC). For this long
# after the last UPDATING sighting, treat connect failures as a reboot — retry
# fast and rediscover by serial — instead of backing off as if proxy-exhausted.
FIRMWARE_REBOOT_WINDOW_SECONDS = 600  # 10 minutes (reboot seen ~3 min after UPDATING)
# How long to wait for the lock to advertise when its stored MAC has gone away.
# Only spent when HA can't see the stored address at all, so it doesn't slow
# down ordinary connect failures.
ADVERTISEMENT_WAIT_SECONDS = 20
# Inside the reboot window, how long one connect attempt waits for the lock to
# advertise again after the link dropped. The rebooted lock took ~95 s to show up
# on its new MAC (2026-09-24); dialing the dead old MAC meanwhile just burns
# bleak-retry-connector's ~57 s ladder and misses the new one appearing.
REBOOT_ADVERTISEMENT_WAIT_SECONDS = 120
# sw_version comes from the cloud, which only learns the new version once the
# lock checks in — that lags the reboot. Re-poll on this schedule until it moves.
FIRMWARE_REFRESH_DELAYS = [30, 60, 120, 300, 600]
# Slow background poll so "update available" doesn't go stale between the
# monthly certificate refreshes (one cheap cloud call).
FIRMWARE_INFO_POLL_INTERVAL = timedelta(hours=12)

# Event bus event type for logbook
EVENT_LOCK_ACTION = f"{DOMAIN}_lock_action"
