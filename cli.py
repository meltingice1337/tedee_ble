#!/usr/bin/env python3
"""Tedee BLE CLI -- interactive test tool.

Usage:
    python cli.py scan                  # Find Tedee locks nearby
    python cli.py register              # One-time: generate keys, register with cloud
    python cli.py connect               # Connect + PTLS handshake (test)
    python cli.py unlock [--force] [--pull]  # Unlock (--pull to also pull spring)
    python cli.py lock [--force]        # Lock the door
    python cli.py pull                  # Pull spring
    python cli.py status                # Get lock state + battery
    python cli.py info [--raw]          # Show lock model, serial, firmware from cloud API
    python cli.py access                # Show device access shares and activity
    python cli.py shell                 # Interactive shell with persistent connection

ESPHome BT Proxy:
    python cli.py --proxy 192.168.1.50 scan       # Scan via ESPHome proxy
    python cli.py --proxy 192.168.1.50 shell       # Shell via ESPHome proxy
    python cli.py --proxy 192.168.1.50 connect     # Connect via proxy

Config layout:
    config.json              — api_key, lock_serial, device_id, lock_address, lock_name
    keys/device_key.pem      — ECDSA P-256 private key (generated during register)
    keys/registration.json   — mobile_id, certificate, devicePublicKey, signed_time
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add custom_components to path so tedee_lib is importable
sys.path.insert(0, str(Path(__file__).parent / "custom_components" / "tedee_ble"))

from tedee_lib import crypto
from tedee_lib.ble import TedeeBLETransport, serial_to_service_uuid
from tedee_lib.ble_esphome import ESPHomeBLETransport, esphome_scan
from tedee_lib.cloud_api import TedeeCloudAPI
from tedee_lib.lock_commands import (
    DOOR_STATE_NAMES,
    DOOR_STATE_UNKNOWN,
    LOCK_FORCE,
    LOCK_STATE_NAMES,
    TRIGGER_NAMES,
    UNLOCK_FORCE,
    TedeeLock,
)
from tedee_lib.ptls import ALERT_NO_TRUSTED_TIME, PTLSAlertError, PTLSSession

logger = logging.getLogger(__name__)

DEVICE_TYPE_MODELS = {
    1: "Bridge",
    2: "PRO",
    3: "Keypad",
    4: "GO",
    5: "Gate",
    6: "DryContact",
    8: "Door Sensor",
    10: "Keypad PRO",
}

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
KEYS_DIR = ROOT / "keys"
DEVICE_KEY_PATH = KEYS_DIR / "device_key.pem"
REGISTRATION_PATH = KEYS_DIR / "registration.json"


# ─── Config / keys helpers ───────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


def load_registration() -> dict | None:
    if REGISTRATION_PATH.exists():
        return json.loads(REGISTRATION_PATH.read_text())
    return None


def save_registration(reg: dict) -> None:
    KEYS_DIR.mkdir(exist_ok=True)
    REGISTRATION_PATH.write_text(json.dumps(reg, indent=2) + "\n")


def load_private_key():
    if not DEVICE_KEY_PATH.exists():
        return None
    return crypto.pem_to_private_key(DEVICE_KEY_PATH.read_bytes())


def save_private_key(key) -> None:
    KEYS_DIR.mkdir(exist_ok=True)
    DEVICE_KEY_PATH.write_bytes(crypto.private_key_to_pem(key))
    DEVICE_KEY_PATH.chmod(0o600)


# ─── BLE scan helpers ────────────────────────────────────────────

async def scan_for_tedee_locks(timeout: float = 10.0):
    """Scan for any Tedee locks advertising the Tedee service UUID prefix."""
    from bleak import BleakScanner

    TEDEE_PREFIX = "0000-4899-489f-a301-"
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found = []
    for device, adv_data in devices.values():
        for uuid in (adv_data.service_uuids or []):
            if TEDEE_PREFIX in str(uuid).lower():
                found.append(device)
                break
    return found


async def scan_for_serial(serial: str, timeout: float = 10.0):
    """Scan for a specific Tedee lock by serial number."""
    from bleak import BleakScanner

    target_uuid = serial_to_service_uuid(serial).lower()
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for device, adv_data in devices.values():
        service_uuids = [str(u).lower() for u in (adv_data.service_uuids or [])]
        if target_uuid in service_uuids:
            return device
    return None


# ─── Signed time refresh ─────────────────────────────────────────

async def refresh_signed_time(api_key: str) -> dict:
    """Refresh signed time from cloud API and update registration."""
    reg = load_registration()
    if not reg:
        raise RuntimeError("No registration found. Run 'register' first.")
    async with TedeeCloudAPI(api_key) as api:
        signed_time = await api.get_signed_time()
    reg["signed_time"] = signed_time
    save_registration(reg)
    return signed_time


# ─── Logging setup ───────────────────────────────────────────────

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        logging.getLogger("bleak").setLevel(logging.WARNING)


# ─── Commands ────────────────────────────────────────────────────

async def cmd_scan(args):
    """Scan for Tedee locks."""
    config = load_config()
    serial = config.get("lock_serial")

    if args.proxy:
        print(f"Scanning via ESPHome proxy {args.proxy}...")
        found = await esphome_scan(
            args.proxy, args.proxy_port, args.proxy_password, args.noise_psk,
            timeout=args.timeout, serial=serial,
        )
        if found:
            for d in found:
                print(f"  Found: {d['name']} ({d['address']}) RSSI={d.get('rssi', '?')}")
        else:
            print("  No Tedee locks found via proxy.")
        return

    if serial:
        print(f"Scanning for lock with serial {serial}...")
        device = await scan_for_serial(serial, timeout=args.timeout)
        if device:
            print(f"  Found: {device.name} ({device.address})")
        else:
            print("  Not found. Trying general scan...")
            locks = await scan_for_tedee_locks(timeout=args.timeout)
            if locks:
                for d in locks:
                    print(f"  Found: {d.name} ({d.address})")
            else:
                print("  No Tedee locks found.")
    else:
        print("Scanning for Tedee locks...")
        locks = await scan_for_tedee_locks(timeout=args.timeout)
        if locks:
            for d in locks:
                print(f"  Found: {d.name} ({d.address})")
        else:
            print("  No Tedee locks found.")
            print("  Tip: Set 'lock_serial' in config.json to scan by serial UUID.")


async def cmd_register(args):
    """One-time registration: generate keys, register with cloud, get certificate."""
    config = load_config()
    api_key = config.get("api_key")
    if not api_key:
        print("Error: Set 'api_key' in config.json first.")
        print("Generate a Personal Access Key at https://portal.tedee.com/personal-access-keys")
        return

    device_id = config.get("device_id")
    serial = config.get("lock_serial")

    if not device_id:
        if not serial:
            print("Error: Set 'device_id' or 'lock_serial' in config.json.")
            return
        print(f"Looking up device ID for serial {serial}...")
        async with TedeeCloudAPI(api_key) as api:
            device_id = await api.find_device_id(serial)
            if not device_id:
                print(f"Error: No device found with serial {serial}")
                print("Available locks:")
                locks = await api.get_devices()
                for lock in locks:
                    print(f"  {lock.get('name')}: serial={lock.get('serialNumber')}, id={lock.get('id')}")
                return
            print(f"  Device ID: {device_id}")
            config["device_id"] = device_id
            save_config(config)

    print("Setting up registration...")

    # Generate ECDSA P-256 key pair
    private_key = crypto.generate_ecdsa_keypair()
    save_private_key(private_key)
    pub_b64 = crypto.public_key_to_base64(private_key.public_key())

    async with TedeeCloudAPI(api_key) as api:
        mobile_id = await api.register_mobile(pub_b64)
        cert_data = await api.get_device_certificate(mobile_id, device_id)
        signed_time = await api.get_signed_time()

    reg = {
        "mobile_id": mobile_id,
        "device_id": device_id,
        "public_key": pub_b64,
        "certificate": cert_data["certificate"],
        "expirationDate": cert_data["expirationDate"],
        "devicePublicKey": cert_data["devicePublicKey"],
        "mobilePublicKey": cert_data.get("mobilePublicKey", pub_b64),
        "signed_time": signed_time,
    }
    save_registration(reg)

    config["mobile_id"] = mobile_id
    save_config(config)

    print(f"  Mobile ID: {mobile_id}")
    print(f"  Certificate expires: {cert_data['expirationDate']}")
    print(f"  Device public key: {cert_data['devicePublicKey'][:20]}...")
    print(f"  Keys saved to: {KEYS_DIR}/")
    print("Registration complete!")


async def _connect_and_run(args, callback):
    """Helper: connect to lock, establish PTLS session, run callback."""
    config = load_config()
    api_key = config.get("api_key")
    if not api_key:
        print("Error: Set 'api_key' in config.json first.")
        return

    private_key = load_private_key()
    if not private_key:
        print(f"Error: No device key found at {DEVICE_KEY_PATH}. Run 'register' first.")
        return

    reg = load_registration()
    if not reg:
        print(f"Error: No registration found at {REGISTRATION_PATH}. Run 'register' first.")
        return

    certificate = reg["certificate"]
    device_public_key = reg["devicePublicKey"]

    # Find lock via BLE
    serial = config.get("lock_serial")
    address = config.get("lock_address")

    if args.proxy:
        if not address:
            print("Error: --proxy requires 'lock_address' in config.json (MAC address).")
            print("Run 'python cli.py scan --proxy HOST' to find it.")
            return
        transport = ESPHomeBLETransport(
            args.proxy, address, args.proxy_port, args.proxy_password, args.noise_psk,
        )
    else:
        device = None
        if address:
            device = address
            print(f"Using stored address: {address}")
        elif serial:
            print(f"Scanning for lock {serial}...")
            device = await scan_for_serial(serial, timeout=args.timeout)
            if not device:
                print("Lock not found via serial UUID, trying general scan...")
                locks = await scan_for_tedee_locks(timeout=args.timeout)
                if locks:
                    device = locks[0]
                    print(f"Using first found lock: {device.name} ({device.address})")
                else:
                    print("Error: No Tedee lock found.")
                    return
        else:
            print("Scanning for Tedee locks...")
            locks = await scan_for_tedee_locks(timeout=args.timeout)
            if not locks:
                print("Error: No Tedee lock found.")
                return
            device = locks[0]
            print(f"Using first found lock: {device.name} ({device.address})")
        transport = TedeeBLETransport(device)
    try:
        await transport.connect()

        # Establish PTLS session
        session = PTLSSession(
            transport=transport,
            device_private_key=private_key,
            certificate_b64=certificate,
            device_public_key_b64=device_public_key,
        )

        _needs_signed_time = False
        try:
            await session.handshake()
        except PTLSAlertError as err:
            if err.code == ALERT_NO_TRUSTED_TIME:
                print("Lock has no trusted time, fetching and retrying...")
                await transport.disconnect()
                signed_time = await refresh_signed_time(api_key)
                await transport.connect()
                session = PTLSSession(
                    transport=transport,
                    device_private_key=private_key,
                    certificate_b64=certificate,
                    device_public_key_b64=device_public_key,
                )
                await session.handshake()
                _needs_signed_time = True
            else:
                raise

        # Create lock interface
        lock = TedeeLock(transport, session)

        # Only set signed time when the lock requested it
        if _needs_signed_time:
            await lock.set_signed_time(signed_time)
        await lock.drain_pending_notifications()

        # Run the actual command
        await callback(lock)

    finally:
        await transport.disconnect()
        if hasattr(transport, "stop_proxy"):
            await transport.stop_proxy()


async def cmd_connect(args):
    """Test connection and PTLS handshake."""
    async def _test(lock):
        print("Connection test successful!")
        state, status, door = await lock.get_state()
        state_name = LOCK_STATE_NAMES.get(state, f"0x{state:02x}")
        print(f"  Lock state: {state_name}")
        level, charging = await lock.get_battery()
        print(f"  Battery: {level}% {'(charging)' if charging else ''}")

    await _connect_and_run(args, _test)


async def cmd_unlock(args):
    """Unlock the door."""
    mode = UNLOCK_FORCE if args.force else 0x00

    async def _unlock(lock):
        await lock.unlock(mode=mode)
        print("Unlock command sent!")
        if args.pull:
            print("Waiting for unlock to complete...")
            from tedee_lib.lock_commands import LOCK_STATE_UNLOCKED
            for _ in range(20):
                await asyncio.sleep(0.5)
                try:
                    state, _, _ = await lock.get_state()
                    if state == LOCK_STATE_UNLOCKED:
                        await lock.pull_spring()
                        print("Pull spring done!")
                        return
                except Exception as e:
                    print(f"Error polling state: {e}")
                    break
            print("Lock did not reach unlocked state, skipping pull.")

    await _connect_and_run(args, _unlock)


async def cmd_lock(args):
    """Lock the door."""
    mode = LOCK_FORCE if args.force else 0x00

    async def _lock(lock):
        await lock.lock(mode=mode)
        print("Lock command sent!")

    await _connect_and_run(args, _lock)


async def cmd_pull(args):
    """Pull the spring."""
    async def _pull(lock):
        await lock.pull_spring()
        print("Pull spring command sent!")

    await _connect_and_run(args, _pull)


async def cmd_status(args):
    """Get lock status and battery."""
    async def _status(lock):
        state, status, door = await lock.get_state()
        state_name = LOCK_STATE_NAMES.get(state, f"0x{state:02x}")
        jammed = " (JAMMED!)" if status == 0x01 else ""
        print(f"Lock state: {state_name}{jammed}")
        if door != DOOR_STATE_UNKNOWN:
            print(f"Door: {DOOR_STATE_NAMES.get(door, f'0x{door:02x}')}")

        level, charging = await lock.get_battery()
        charge_str = " (charging)" if charging else ""
        print(f"Battery: {level}%{charge_str}")

    await _connect_and_run(args, _status)


async def cmd_info(args):
    """Show lock info from cloud API (model, serial, firmware, etc.)."""
    config = load_config()
    api_key = config.get("api_key")
    if not api_key:
        print("Error: Set 'api_key' in config.json first.")
        return

    device_id = config.get("device_id")

    async with TedeeCloudAPI(api_key) as api:
        locks = await api.get_devices()

    if not locks:
        print("No locks found on account.")
        return

    for lock in locks:
        if device_id and lock.get("id") != device_id:
            continue
        device_type = lock.get("type", 0)
        model = DEVICE_TYPE_MODELS.get(device_type, f"Unknown ({device_type})")
        print(f"=== {lock.get('name', '?')} ===")
        print(f"  ID:             {lock.get('id')}")
        print(f"  Serial:         {lock.get('serialNumber', '?')}")
        print(f"  Type:           {device_type} → {model}")
        print(f"  Firmware:       {lock.get('softwareVersions', [{}])[0].get('version', '?') if lock.get('softwareVersions') else '?'}")
        print(f"  Connected:      {lock.get('isConnected', '?')}")
        print(f"  Battery:        {lock.get('batteryLevel', '?')}%")
        print(f"  State:          {lock.get('lockProperties', {}).get('state', '?')}")
        print()

    if args.raw:
        print("=== Raw API response ===\n")
        target = [l for l in locks if l.get("id") == device_id] if device_id else locks
        print(json.dumps(target, indent=2))


async def cmd_access(args):
    """Show who has access to the lock and their IDs."""
    config = load_config()
    api_key = config.get("api_key")
    device_id = config.get("device_id")
    if not api_key or not device_id:
        print("Error: Set 'api_key' and 'device_id' in config.json first.")
        return

    async with TedeeCloudAPI(api_key) as api:
        # Full device details
        print("=== Full device details ===\n")
        try:
            locks = await api.get_devices()
            for lock in locks:
                if lock.get("id") == device_id:
                    print(json.dumps(lock, indent=2))
                    print()
                    break
        except Exception as e:
            print(f"  Error: {e}")

        # Device access
        print(f"=== Device access for device {device_id} ===\n")
        try:
            access = await api.get_device_access(device_id)
            if access:
                for a in access:
                    print(json.dumps(a, indent=2))
                    print()
            else:
                print("  No access entries found.")
        except Exception as e:
            print(f"  Access error: {e}")

        # Recent activity logs
        print(f"\n=== Recent activity for device {device_id} ===\n")
        try:
            activity = await api.get_device_activity(device_id, limit=10)
            if activity:
                for a in activity:
                    print(f"  {a.get('date', '?')} | event={a.get('event')} source={a.get('source')} | {a.get('username', '?')} (userId={a.get('userId')})")
                print()
            else:
                print("  No activity found.")
        except Exception as e:
            print(f"  Activity error: {e}")


async def cmd_shell(args):
    """Interactive shell with persistent BLE connection."""
    config = load_config()
    api_key = config.get("api_key")
    if not api_key:
        print("Error: Set 'api_key' in config.json first.")
        return

    private_key = load_private_key()
    if not private_key:
        print(f"Error: No device key found at {DEVICE_KEY_PATH}. Run 'register' first.")
        return

    reg = load_registration()
    if not reg:
        print(f"Error: No registration found at {REGISTRATION_PATH}. Run 'register' first.")
        return

    certificate = reg["certificate"]
    device_public_key = reg["devicePublicKey"]

    address = config.get("lock_address")
    serial = config.get("lock_serial")

    if args.proxy:
        if not address:
            print("Error: --proxy requires 'lock_address' in config.json.")
            return
        transport = ESPHomeBLETransport(
            args.proxy, address, args.proxy_port, args.proxy_password, args.noise_psk,
        )
    else:
        device = None
        if address:
            device = address
            print(f"Using stored address: {address}")
        elif serial:
            print(f"Scanning for lock {serial}...")
            device = await scan_for_serial(serial, timeout=args.timeout)
        if not device:
            print("Scanning for Tedee locks...")
            locks = await scan_for_tedee_locks(timeout=args.timeout)
            if locks:
                device = locks[0]
                print(f"Found: {device.name} ({device.address})")
            else:
                print("Error: No Tedee lock found.")
                return
        transport = TedeeBLETransport(device)
    lock = None
    session = None

    async def connect():
        nonlocal lock, session
        await transport.connect()
        session = PTLSSession(
            transport=transport,
            device_private_key=private_key,
            certificate_b64=certificate,
            device_public_key_b64=device_public_key,
        )

        _needs_signed_time = False
        try:
            await session.handshake()
        except PTLSAlertError as err:
            if err.code == ALERT_NO_TRUSTED_TIME:
                print("Lock has no trusted time, fetching and retrying...")
                await transport.disconnect()
                signed_time = await refresh_signed_time(api_key)
                await transport.connect()
                session = PTLSSession(
                    transport=transport,
                    device_private_key=private_key,
                    certificate_b64=certificate,
                    device_public_key_b64=device_public_key,
                )
                await session.handshake()
                _needs_signed_time = True
            else:
                raise

        lock = TedeeLock(transport, session)
        if _needs_signed_time:
            await lock.set_signed_time(signed_time)
        await lock.drain_pending_notifications()
        print("Connected and session established.")

    async def ensure_connected():
        if not transport.is_connected:
            print("Reconnecting...")
            await connect()

    _notification_task = None

    async def notification_listener():
        while True:
            try:
                data = await transport.read_notification(timeout=3600)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            except Exception:
                if not transport.is_connected:
                    return
                continue
            if lock:
                parsed = await lock.parse_notification(data)
                if not parsed:
                    continue
                if parsed["type"] == "lock_state":
                    jammed = " JAMMED!" if parsed["jammed"] else ""
                    door = f" | Door: {parsed['door_name']}" if parsed.get("door_state", 0) != 0 else ""
                    trigger = parsed.get("trigger_name", "?")
                    access_id = parsed.get("access_id", 0)
                    who = f" by #{access_id}" if access_id else ""
                    print(f"\n  << {parsed['state_name']}{jammed} [trigger: {trigger}{who}]{door}")
                    print("tedee> ", end="", flush=True)
                elif parsed["type"] == "need_datetime":
                    try:
                        signed_time = await refresh_signed_time(api_key)
                        await lock.set_signed_time(signed_time)
                        print("\n  << Lock requested time sync (done)")
                        print("tedee> ", end="", flush=True)
                    except Exception as e:
                        print(f"\n  << Time sync failed: {e}")
                        print("tedee> ", end="", flush=True)

    try:
        await connect()
        _notification_task = asyncio.create_task(notification_listener())

        print("\nTedee Shell - type 'help' for commands, 'quit' to exit\n")

        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("tedee> ")
                )
            except EOFError:
                break

            line = line.strip().lower()
            if not line:
                continue

            parts = line.split()
            cmd = parts[0]

            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print("  unlock [force] [pull]  - Unlock (add 'pull' to also pull spring)")
                print("  lock [force]    - Lock the door")
                print("  pull            - Pull spring")
                print("  status          - Lock state + battery")
                print("  state           - Lock state only")
                print("  battery         - Battery only")
                print("  reconnect       - Force reconnect")
                print("  quit            - Exit")
                continue

            try:
                await ensure_connected()

                if cmd == "unlock":
                    mode = UNLOCK_FORCE if "force" in parts[1:] else 0x00
                    await lock.unlock(mode=mode)
                    if "pull" in parts[1:]:
                        from tedee_lib.lock_commands import LOCK_STATE_UNLOCKED
                        for _ in range(20):
                            await asyncio.sleep(0.5)
                            state, _, _ = await lock.get_state()
                            if state == LOCK_STATE_UNLOCKED:
                                await lock.pull_spring()
                                break
                    print("Unlocked.")
                elif cmd == "lock":
                    mode = LOCK_FORCE if "force" in parts[1:] else 0x00
                    await lock.lock(mode=mode)
                    print("Locked.")
                elif cmd == "pull":
                    await lock.pull_spring()
                    print("Pull spring activated.")
                elif cmd in ("status", "st"):
                    state, status, door = await lock.get_state()
                    state_name = LOCK_STATE_NAMES.get(state, f"0x{state:02x}")
                    jammed = " JAMMED!" if status == 0x01 else ""
                    door_str = f"  Door: {DOOR_STATE_NAMES.get(door, f'0x{door:02x}')}" if door != DOOR_STATE_UNKNOWN else ""
                    level, charging = await lock.get_battery()
                    charge_str = " (charging)" if charging else ""
                    print(f"  State: {state_name}{jammed}")
                    if door_str:
                        print(door_str)
                    print(f"  Battery: {level}%{charge_str}")
                elif cmd == "state":
                    state, status, door = await lock.get_state()
                    state_name = LOCK_STATE_NAMES.get(state, f"0x{state:02x}")
                    jammed = " JAMMED!" if status == 0x01 else ""
                    door_str = f" | Door: {DOOR_STATE_NAMES.get(door, f'0x{door:02x}')}" if door != DOOR_STATE_UNKNOWN else ""
                    print(f"  {state_name}{jammed}{door_str}")
                elif cmd in ("battery", "bat"):
                    level, charging = await lock.get_battery()
                    charge_str = " (charging)" if charging else ""
                    print(f"  {level}%{charge_str}")
                elif cmd == "reconnect":
                    if transport.is_connected:
                        await transport.disconnect()
                    await connect()
                else:
                    print(f"Unknown command: {cmd}. Type 'help' for commands.")

            except Exception as e:
                print(f"Error: {e}")
                if not transport.is_connected:
                    print("Connection lost. Will reconnect on next command.")

    finally:
        if _notification_task:
            _notification_task.cancel()
            try:
                await _notification_task
            except asyncio.CancelledError:
                pass
        if transport.is_connected:
            await transport.disconnect()
        if hasattr(transport, "stop_proxy"):
            await transport.stop_proxy()
        print("Disconnected.")


def main():
    parser = argparse.ArgumentParser(
        description="Tedee BLE CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument("-t", "--timeout", type=float, default=10.0, help="BLE scan timeout")
    parser.add_argument(
        "--proxy", metavar="HOST",
        help="ESPHome BT proxy address (e.g. 192.168.1.50 or proxy.local)",
    )
    parser.add_argument("--proxy-port", type=int, default=6053, help="ESPHome API port (default: 6053)")
    parser.add_argument("--proxy-password", default="", help="ESPHome API password")
    parser.add_argument("--noise-psk", default=None, help="ESPHome noise encryption PSK (base64)")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="Scan for Tedee locks")
    sub.add_parser("register", help="Register with cloud API")
    sub.add_parser("connect", help="Test connection + handshake")

    p_unlock = sub.add_parser("unlock", help="Unlock the door")
    p_unlock.add_argument("--force", action="store_true", help="Force unlock")
    p_unlock.add_argument("--pull", action="store_true", help="Also pull spring after unlock")

    p_lock = sub.add_parser("lock", help="Lock the door")
    p_lock.add_argument("--force", action="store_true", help="Force lock")

    sub.add_parser("pull", help="Pull spring")
    sub.add_parser("status", help="Get lock state + battery")

    p_info = sub.add_parser("info", help="Show lock info from cloud API")
    p_info.add_argument("--raw", action="store_true", help="Show raw API JSON")

    sub.add_parser("access", help="Show device access shares and activity")
    sub.add_parser("shell", help="Interactive shell with persistent connection")

    args = parser.parse_args()
    setup_logging(args.verbose)

    commands = {
        "scan": cmd_scan,
        "register": cmd_register,
        "connect": cmd_connect,
        "unlock": cmd_unlock,
        "lock": cmd_lock,
        "pull": cmd_pull,
        "status": cmd_status,
        "info": cmd_info,
        "access": cmd_access,
        "shell": cmd_shell,
    }

    asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    main()
