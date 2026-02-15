"""Constants for the Tedee BLE integration."""

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

# Tedee API device type → model name
DEVICE_TYPE_MODELS = {
    2: "PRO",
    4: "GO",
    # 1=Bridge, 3=Keypad, 5=Gate, 6=DryContact, 8=Door Sensor, 10=Keypad PRO
}

# Coordinator
RECONNECT_DELAYS = [2, 5, 10, 30, 60]
POLL_INTERVAL_SECONDS = 600  # 10 minutes
KEEPALIVE_INTERVAL_SECONDS = 45  # BLE keep-alive (lock disconnects after ~25-45s idle on GO)
UNAVAILABLE_GRACE_SECONDS = 15  # Don't mark unavailable until reconnect fails this long
CERT_CHECK_INTERVAL_SECONDS = 6 * 3600  # 6 hours
