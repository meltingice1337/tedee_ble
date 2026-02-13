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
CONF_USER_MAP = "user_map"  # {userId: username} from activity logs

# Coordinator
RECONNECT_DELAYS = [2, 5, 10, 30, 60]
POLL_INTERVAL_SECONDS = 600  # 10 minutes
KEEPALIVE_INTERVAL_SECONDS = 45  # BLE keep-alive (lock disconnects after ~60-90s idle)
CERT_CHECK_INTERVAL_SECONDS = 6 * 3600  # 6 hours
